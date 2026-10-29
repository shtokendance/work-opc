#!/usr/bin/env python3
"""Invoke a documented application operation without embedding credentials."""

import argparse
import json
import math
import os
from pathlib import Path
import re
import sys
import urllib.error
import urllib.parse
import urllib.request


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def valid_origin(value):
    if not isinstance(value, str) or any(c.isspace() or ord(c) < 32 for c in value) or "\\" in value:
        raise ValueError("base_url must be an HTTP(S) origin without whitespace or backslashes")
    u = urllib.parse.urlsplit(value)
    if u.scheme not in {"http", "https"} or not u.hostname or u.username is not None or u.password is not None:
        raise ValueError("base_url must be a complete HTTP(S) URL without credentials")
    if u.query or u.fragment or u.path not in {"", "/"} or (u.port is not None and not 1 <= u.port <= 65535):
        raise ValueError("base_url must be an application origin, not a path/query or invalid port")
    return u


def read_json(value, filename):
    if filename:
        return json.load(sys.stdin) if filename == "-" else json.loads(Path(filename).read_text())
    return json.loads(value)


def validate_basic(value, schema, label):
    """Validate common JSON shapes, not the whole JSON Schema standard."""
    types = {"string": str, "integer": int, "number": (int, float), "boolean": bool, "object": dict, "array": list, "null": type(None)}
    kind = schema.get("type")
    if kind in types:
        if not isinstance(value, types[kind]) or (kind in {"integer", "number"} and isinstance(value, bool)):
            raise ValueError(label + " has the wrong JSON type")
    if "enum" in schema and value not in schema["enum"]:
        raise ValueError(label + " is outside the documented enum")
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        if isinstance(value, float) and not math.isfinite(value):
            raise ValueError(label + " must be a finite JSON number")
        if "minimum" in schema and value < schema["minimum"]:
            raise ValueError(label + " is below the documented minimum")
        if "maximum" in schema and value > schema["maximum"]:
            raise ValueError(label + " is above the documented maximum")
    if isinstance(value, dict):
        missing = set(schema.get("required", [])) - set(value)
        if missing:
            raise ValueError("missing required fields in " + label + ": " + ", ".join(sorted(missing)))
        if schema.get("additionalProperties") is False and set(value) - set(schema.get("properties", {})):
            raise ValueError("unknown fields in " + label)
        for key, child_schema in schema.get("properties", {}).items():
            if key in value:
                validate_basic(value[key], child_schema, label + "." + key)
    if isinstance(value, list) and isinstance(schema.get("items"), dict):
        for item in value:
            validate_basic(item, schema["items"], label + "[]")


def build_request(contract, operation, params, body, approve_write=False, credential_env=None):
    origin = valid_origin(contract["base_url"])
    matches = [a for a in contract["apis"] if a["id"] == operation]
    if len(matches) != 1:
        raise ValueError("unknown operation; consult references/operations.json")
    api = matches[0]
    if api.get("effect") not in {"read", "write", "destructive", "external"}:
        raise ValueError("operation must declare its business side effects")
    if api["effect"] != "read" and not approve_write:
        raise ValueError("operation changes business state; obtain user approval before --approve-write")
    if not isinstance(params, dict):
        raise ValueError("parameters must be a JSON object")
    path = api["path"]
    query, headers = {}, {"Accept": "application/json"}
    known = {p["name"] for p in api.get("parameters", [])}
    if set(params) - known:
        raise ValueError("unknown parameters: " + ", ".join(sorted(set(params) - known)))
    for param in api.get("parameters", []):
        key = param["name"]
        if param.get("required") and (key not in params or params[key] is None):
            raise ValueError("missing required parameter: " + key)
        if key not in params:
            continue
        value = params[key]
        validate_basic(value, param, "parameter " + key)
        if isinstance(value, (dict, list)):
            raise ValueError("complex parameter encoding must be defined in the skill: " + key)
        value = str(value).lower() if isinstance(value, bool) else str(value)
        if param["in"] == "path":
            if value in {".", "..", ""}:
                raise ValueError("path parameter must be a nonempty non-traversing segment")
            path = path.replace("{" + key + "}", urllib.parse.quote(value, safe=""))
        elif param["in"] == "query":
            query[key] = value
        elif param["in"] == "header":
            if key.lower() in {"host", "authorization", "proxy-authorization", "cookie", "content-length", "transfer-encoding", "connection", "upgrade"}:
                raise ValueError("authentication headers must use the declared auth environment variable")
            headers[key] = value
    if re.search(r"\{[^}]+\}", path):
        raise ValueError("unresolved path parameter")
    if not path.startswith("/") or path.startswith("//") or "?" in path or "#" in path:
        raise ValueError("invalid operation path")
    auth = api.get("auth", {"type": "none"})
    if auth.get("type") not in {"none", "bearer", "header"}:
        raise ValueError("unsupported authentication; a dedicated client is required")
    if auth["type"] != "none":
        credential = (os.environ if credential_env is None else credential_env).get(auth["env"], "")
        if not credential:
            raise ValueError("missing credential environment variable: " + auth["env"])
        if auth["type"] == "bearer":
            headers["Authorization"] = "Bearer " + credential
        else:
            if auth["header"].lower() in {"host", "proxy-authorization", "content-length", "transfer-encoding", "connection", "upgrade"}:
                raise ValueError("invalid authentication header name")
            headers[auth["header"]] = credential
    spec = api.get("request_body")
    if spec and spec.get("required") and body is None:
        raise ValueError("request body is required")
    if body is not None:
        if not spec:
            raise ValueError("operation does not declare a request body")
        schema = spec.get("schema", {})
        validate_basic(body, schema, "request body")
        headers["Content-Type"] = "application/json"
    url = contract["base_url"].rstrip("/") + path
    if query:
        url += "?" + urllib.parse.urlencode(query)
    target = urllib.parse.urlsplit(url)
    if (target.scheme, target.netloc) != (origin.scheme, origin.netloc):
        raise ValueError("request escaped the declared application origin")
    for key, value in headers.items():
        if not re.fullmatch(r"[!#$%&'*+.^_`|~0-9A-Za-z-]+", key) or any(c in value for c in "\r\n\x00"):
            raise ValueError("invalid header name/value; credential values are not logged")
        try:
            value.encode("latin-1")
        except UnicodeEncodeError:
            raise ValueError("header value requires explicit encoding; credential values are not logged") from None
    payload = None if body is None else json.dumps(body, allow_nan=False).encode()
    return urllib.request.Request(url, data=payload, headers=headers, method=api["method"])


def execute(contract, operation, params, body=None, approve_write=False, credential_env=None):
    request = build_request(contract, operation, params, body, approve_write, credential_env)
    # Do not forward credentials through redirects or system proxies.
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())
    try:
        with opener.open(request, timeout=20) as response:
            return response.status, response.headers.get("Content-Type", ""), response.read(2_000_001)
    except urllib.error.HTTPError as exc:
        try:
            return exc.code, exc.headers.get("Content-Type", ""), exc.read(2_000_001)
        finally:
            exc.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--operation", required=True)
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--params", default="{}")
    group.add_argument("--params-file")
    parser.add_argument("--body-file")
    parser.add_argument("--approve-write", action="store_true")
    args = parser.parse_args()
    try:
        root = Path(__file__).resolve().parents[1]
        if (root / ".work-delivery-pending").exists():
            raise ValueError("Work application deployment has not passed verification; this skill is disabled")
        contract = json.loads((root / "references/operations.json").read_text())
        params = read_json(args.params, args.params_file)
        body = read_json("null", args.body_file)
        status, content_type, content = execute(contract, args.operation, params, body, args.approve_write)
        if len(content) > 2_000_000:
            raise ValueError("response exceeds 2 MB; narrow the query")
        print(content.decode("utf-8", errors="replace"))
        return 0 if 200 <= status < 300 else 1
    except (ValueError, KeyError, OSError, urllib.error.URLError) as exc:
        # Avoid logging request objects, credential values, or credential-bearing URLs.
        print("Invocation failed: " + (str(exc) if isinstance(exc, ValueError) else type(exc).__name__), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
