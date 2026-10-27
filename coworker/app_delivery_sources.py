"""Read only exact Go module versions declared by the uploaded application."""

import os
from pathlib import Path
import re

from .app_delivery_skill.scripts import delivery


def dependency_path(source: Path, module: str = "", name: str = ""):
    mod = source / "go.mod"
    if not mod.is_file():
        raise ValueError("当前源码没有 go.mod；请提供缺失依赖的源码或接口说明")
    contents = mod.read_text()
    declarations = re.findall(r"^\s*(?:require\s+)?([A-Za-z0-9_.!/-]+)\s+(v[0-9][A-Za-z0-9.+-]*)\s*(?://[^\n]*)?$", contents, re.M)
    replaced = set(re.findall(r"^\s*(?:replace\s+)?([A-Za-z0-9_.!/-]+)(?:\s+v[^\s]+)?\s*=>", contents, re.M))
    cache = Path(os.environ.get("GOMODCACHE") or str(Path(os.environ.get("GOPATH", str(Path.home() / "go")).split(os.pathsep)[0]) / "pkg/mod"))
    modules = {}
    for dependency, version in declarations:
        if dependency.startswith("/") or any(part in {"", ".", ".."} for part in dependency.split("/")):
            continue
        # Go's module-cache encoding uses !lowercase for uppercase letters.
        escaped = re.sub(r"[A-Z]", lambda m: "!" + m[0].lower(), dependency)
        modules[dependency] = (version, cache / (escaped + "@" + version))
    if not module:
        return [{"module": key, "version": version, "cached": path.is_dir() and key not in replaced} for key, (version, path) in modules.items()]
    if module not in modules:
        raise ValueError("只能查看 go.mod 声明的依赖，不能读取任意宿主目录")
    if module in replaced:
        raise ValueError("该依赖被 replace 替换；请读取上传包内的替换源码或提供实际版本，不使用旧缓存推断")
    version, root = modules[module]
    if not root.is_dir() or root.is_symlink():
        raise ValueError("该版本依赖尚未缓存；请提供源码或先由用户准备依赖，不自动下载")
    if not name:
        return [{"path": path.name, "directory": path.is_dir()} for path in sorted(root.iterdir()) if not path.is_symlink() and path.name not in delivery.EXCLUDED][:100]
    target = delivery.relative(root, name)
    if target.is_dir():
        return [{"path": path.relative_to(root).as_posix(), "directory": path.is_dir()} for path in sorted(target.iterdir()) if not path.is_symlink() and path.name not in delivery.EXCLUDED][:100]
    return target
