"""Read-only inspection of the exact deployment archive; never execute app code."""
import os
from pathlib import PurePosixPath
import shutil
import subprocess
import tarfile


def read_archive_file(archive, path):
    if not archive.is_file():
        raise ValueError("没有已打包制品，请先检查原始源码与提案")
    with tarfile.open(archive) as tar:
        matches = [m for m in tar if str(PurePosixPath(m.name)) == str(PurePosixPath(path))]
        if len(matches) != 1 or not matches[0].isfile() or matches[0].size > 2_000_000:
            raise ValueError("制品文件不存在、重复、非普通文件或超过读取限制")
        return tar.extractfile(matches[0]).read().decode('utf-8')


def check_archive_syntax(archive):
    node = shutil.which('node')
    checked = 0
    with tarfile.open(archive) as tar:
        for member in tar:
            path = PurePosixPath(member.name)
            if not member.isfile() or 'node_modules' in path.parts:
                continue
            # Plain .js can contain JSX or depend on package type; do not guess.
            if path.suffix not in {'.mjs', '.cjs'}:
                continue
            if not node:
                return {'label': '交付包 JavaScript 语法', 'status': 'pending', 'detail': '本机缺少 Node.js，未执行语法检查；仍需目标环境验证'}
            if member.size > 2_000_000:
                raise ValueError(f'语法检查未完成：{member.name} 超过 2 MB 限制')
            source = tar.extractfile(member).read()
            try:
                result = subprocess.run(
                    [node, '--input-type=' + ('module' if path.suffix == '.mjs' else 'commonjs'), '--check'],
                    input=source, capture_output=True, timeout=10,
                    env={k: v for k, v in os.environ.items() if k not in {'NODE_OPTIONS', 'NODE_PATH'}},
                )
            except subprocess.TimeoutExpired as exc:
                raise ValueError(f'语法检查超时：{member.name}') from exc
            if result.returncode:
                # Do not expose source lines or embedded credentials in parser output.
                error = next((s.strip() for s in result.stderr.decode(errors='replace').splitlines() if 'SyntaxError:' in s), 'Node.js 解析失败')
                raise ValueError(f'交付包语法检查失败：{member.name}：{error}；请修复完整文件后重新打包')
            checked += 1
    return {'label': '交付包 JavaScript 语法', 'status': 'passed' if checked else 'pending', 'detail': f'已检查 {checked} 个 .mjs/.cjs 文件；不代表目标版本兼容、启动或业务验证通过' if checked else '没有可用此检查器验证的 .mjs/.cjs 文件；其他语言及 .js/TS/JSX 未检查'}
