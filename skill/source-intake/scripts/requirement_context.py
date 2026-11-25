#!/usr/bin/env python3
"""读取需求工作包上下文，输出 JSON（仅用 Python 标准库）。"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import stat
import sys
from pathlib import Path
from typing import Any

SCHEMA_VERSION = 1
MAX_BODY_BYTES = 1024 * 1024
MAX_ATTACHMENTS = 200
MAX_HASH_BYTES = 64 * 1024 * 1024
README_NAME = "README.md"
PROMPT_NAME = "修改说明.md"
REQUIREMENT_NAME = "requirement.json"
ATTACHMENTS_DIR = "attachments"

IMAGE_SUFFIXES = frozenset({
    ".png", ".jpg", ".jpeg", ".jfif", ".gif", ".bmp", ".webp", ".tif", ".tiff",
    ".ico", ".svg", ".heic", ".avif",
})
DOCUMENT_SUFFIXES = frozenset({
    ".pdf", ".doc", ".docx", ".xls", ".xlsx", ".ppt", ".pptx", ".txt", ".md",
    ".markdown", ".csv", ".tsv", ".json", ".xml", ".html", ".htm", ".rtf",
    ".odt", ".ods", ".odp", ".wps", ".et", ".dps", ".log", ".yaml", ".yml",
})
REPARSE_ATTRIBUTE = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0)


class CliArgumentError(Exception):
    pass


def _error(directory: Any, error_type: str, message: str, warnings: list[str] | None = None) -> dict[str, Any]:
    return {
        "schema_version": SCHEMA_VERSION,
        "directory": "" if directory is None else str(directory),
        "ok": False,
        "error": {"type": error_type, "message": message},
        "warnings": list(warnings or []),
    }


def _inspect(path: Path) -> tuple[str, os.stat_result | None, str | None]:
    try:
        info = os.lstat(path)
    except FileNotFoundError:
        return "missing", None, None
    except OSError as exc:
        return "error", None, str(exc)
    if stat.S_ISLNK(info.st_mode) or getattr(info, "st_file_attributes", 0) & REPARSE_ATTRIBUTE:
        return "link", info, None
    if stat.S_ISDIR(info.st_mode):
        return "directory", info, None
    if stat.S_ISREG(info.st_mode):
        return "file", info, None
    return "other", info, None


def _is_within(root: Path, path: Path) -> bool:
    try:
        path.resolve(strict=False).relative_to(root)
    except (OSError, ValueError):
        return False
    return True


def _decode_utf8(data: bytes, label: str, warnings: list[str]) -> str:
    try:
        return data.decode("utf-8-sig")
    except UnicodeDecodeError:
        warnings.append(f"{label} 不是有效的 UTF-8，已用替换字符解码")
        return data.decode("utf-8-sig", errors="replace")


def _read_limited_file(
    path: Path, label: str, root: Path, limit: int, warnings: list[str],
) -> tuple[bytes, bool] | None:
    kind, _info, error = _inspect(path)
    if kind == "missing":
        warnings.append(f"缺少 {label}")
        return None
    if kind == "error":
        warnings.append(f"无法检查 {label}：{error}")
        return None
    if kind == "link":
        warnings.append(f"{label} 是符号链接或 Windows junction，已跳过")
        return None
    if kind != "file":
        warnings.append(f"{label} 不是普通文件，已跳过")
        return None
    if not _is_within(root, path):
        warnings.append(f"{label} 解析到工作包外，已跳过")
        return None
    try:
        with open(path, "rb") as handle:
            data = handle.read(limit + 1)
    except OSError as exc:
        warnings.append(f"无法读取 {label}：{exc}")
        return None
    if len(data) > limit:
        warnings.append(f"{label} 超过 {limit} 字节，已截断")
        return data[:limit], True
    return data, False


def _read_requirement(path: Path, root: Path, warnings: list[str]) -> tuple[dict[str, Any], str | None]:
    result = _read_limited_file(path, REQUIREMENT_NAME, root, MAX_BODY_BYTES, warnings)
    if result is None:
        return {}, None
    data, truncated = result
    if truncated:
        warnings.append(f"{REQUIREMENT_NAME} 超过 1 MiB，未解析")
        return {}, None
    try:
        text = data.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        return {}, f"{REQUIREMENT_NAME} 不是有效的 UTF-8：{exc}"
    try:
        value = json.loads(text)
    except json.JSONDecodeError as exc:
        return {}, (
            f"{REQUIREMENT_NAME} 不是有效 JSON（第 {exc.lineno} 行第 {exc.colno} 列）：{exc.msg}"
        )
    if not isinstance(value, dict):
        warnings.append(f"{REQUIREMENT_NAME} 顶层不是对象，requirement 视为空对象")
        return {}, None
    nested = value.get("requirement")
    return (nested if isinstance(nested, dict) else value), None


def _kind_for(name: str) -> str:
    suffix = Path(name).suffix.lower()
    if suffix in IMAGE_SUFFIXES:
        return "image"
    if suffix in DOCUMENT_SUFFIXES:
        return "document"
    return "other"


def _scan_attachments(root: Path, attachments: Path, warnings: list[str]) -> list[tuple[Path, str, int]]:
    found: list[tuple[Path, str, int]] = []

    def walk(directory: Path) -> None:
        try:
            with os.scandir(directory) as scan:
                entries = sorted(scan, key=lambda item: (item.name.casefold(), item.name))
        except OSError as exc:
            warnings.append(f"无法扫描附件目录 {directory.relative_to(root).as_posix()}：{exc}")
            return
        for entry in entries:
            path = Path(entry.path)
            relative = path.relative_to(attachments).as_posix()
            display = f"{ATTACHMENTS_DIR}/{relative}"
            kind, info, error = _inspect(path)
            if kind == "link":
                warnings.append(f"跳过符号链接或 Windows junction：{display}")
                continue
            if kind == "error":
                warnings.append(f"无法检查附件 {display}：{error}")
                continue
            if kind == "missing":
                warnings.append(f"附件在扫描时消失：{display}")
                continue
            if kind == "directory":
                walk(path)
                continue
            if kind != "file":
                warnings.append(f"跳过非普通文件：{display}")
                continue
            if not _is_within(root, path):
                warnings.append(f"跳过工作包外文件：{display}")
                continue
            assert info is not None
            found.append((path, relative, int(info.st_size)))

    walk(attachments)
    found.sort(key=lambda item: (item[1].casefold(), item[1]))
    return found


def _sha256_limited(path: Path, relative: str, size: int, warnings: list[str]) -> str | None:
    display = f"{ATTACHMENTS_DIR}/{relative}"
    if size > MAX_HASH_BYTES:
        warnings.append(f"附件超过 64 MiB，未计算 sha256：{display}")
        return None
    digest = hashlib.sha256()
    total = 0
    try:
        with open(path, "rb") as handle:
            while True:
                chunk = handle.read(1024 * 1024)
                if not chunk:
                    break
                total += len(chunk)
                if total > MAX_HASH_BYTES:
                    warnings.append(f"附件读取超过 64 MiB，未计算 sha256：{display}")
                    return None
                digest.update(chunk)
    except OSError as exc:
        warnings.append(f"无法读取附件 {display}：{exc}")
        return None
    return digest.hexdigest()


def _attachment_context(root: Path, warnings: list[str]) -> list[dict[str, Any]]:
    directory = root / ATTACHMENTS_DIR
    kind, _info, error = _inspect(directory)
    if kind == "missing":
        warnings.append(f"缺少 {ATTACHMENTS_DIR} 目录")
        return []
    if kind == "error":
        warnings.append(f"无法检查 {ATTACHMENTS_DIR} 目录：{error}")
        return []
    if kind == "link":
        warnings.append(f"{ATTACHMENTS_DIR} 是符号链接或 Windows junction，已跳过")
        return []
    if kind != "directory":
        warnings.append(f"{ATTACHMENTS_DIR} 不是目录，已跳过")
        return []
    if not _is_within(root, directory):
        warnings.append(f"{ATTACHMENTS_DIR} 解析到工作包外，已跳过")
        return []
    candidates = _scan_attachments(root, directory, warnings)
    if len(candidates) > MAX_ATTACHMENTS:
        warnings.append(
            f"附件数量 {len(candidates)} 超过上限 {MAX_ATTACHMENTS}，仅列出前 {MAX_ATTACHMENTS} 个"
        )
        candidates = candidates[:MAX_ATTACHMENTS]
    attachments = []
    for path, relative, size in candidates:
        attachments.append({
            "name": path.name,
            "relative_path": f"{ATTACHMENTS_DIR}/{relative}",
            "size": size,
            "sha256": _sha256_limited(path, relative, size, warnings),
            "kind": _kind_for(path.name),
        })
    return attachments


def read_context(directory: str | Path) -> dict[str, Any]:
    """读取工作包；致命问题返回带 ok=false/error 的字典，缺个别文件只记 warnings。"""
    warnings: list[str] = []
    try:
        package = Path(directory)
    except (TypeError, ValueError) as exc:
        return _error(directory, "InvalidDirectory", f"目录参数无效：{exc}")

    try:
        info = os.stat(package)
    except FileNotFoundError:
        return _error(package, "DirectoryNotFound", f"工作包目录不存在：{package}")
    except OSError as exc:
        return _error(package, "DirectoryUnavailable", f"无法访问工作包目录：{exc}")
    if not stat.S_ISDIR(info.st_mode):
        return _error(package, "NotADirectory", f"工作包路径不是目录：{package}")
    try:
        root = package.resolve(strict=True)
    except OSError as exc:
        return _error(package, "DirectoryUnavailable", f"无法解析工作包目录：{exc}")

    requirement, fatal = _read_requirement(root / REQUIREMENT_NAME, root, warnings)
    if fatal is not None:
        return _error(root, "InvalidRequirementJson", fatal, warnings)

    readme_result = _read_limited_file(root / README_NAME, README_NAME, root, MAX_BODY_BYTES, warnings)
    readme = _decode_utf8(readme_result[0], README_NAME, warnings) if readme_result else ""
    prompt_result = _read_limited_file(root / PROMPT_NAME, PROMPT_NAME, root, MAX_BODY_BYTES, warnings)
    prompt = _decode_utf8(prompt_result[0], PROMPT_NAME, warnings) if prompt_result else ""

    return {
        "schema_version": SCHEMA_VERSION,
        "directory": str(root),
        "requirement": requirement,
        "prompt": prompt,
        "readme": readme,
        "attachments": _attachment_context(root, warnings),
        "warnings": warnings,
    }


class _JsonArgumentParser(argparse.ArgumentParser):
    def error(self, message: str) -> None:  # type: ignore[override]
        raise CliArgumentError(message)


def build_parser() -> argparse.ArgumentParser:
    parser = _JsonArgumentParser(prog="requirement_context.py", add_help=True)
    parser.add_argument("--directory", required=True, help="需求工作包目录")
    return parser


def _configure_stdout() -> None:
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except (AttributeError, ValueError):
        pass


def main(argv: list[str] | None = None) -> int:
    _configure_stdout()
    try:
        args = build_parser().parse_args(argv)
    except CliArgumentError as exc:
        print(json.dumps(_error("", "ArgumentError", str(exc)), ensure_ascii=False, indent=2))
        return 1
    try:
        payload = read_context(args.directory)
    except Exception as exc:  # 保证 stdout 始终是 JSON
        payload = _error(args.directory, type(exc).__name__, f"读取工作包时发生未预期错误：{exc}")
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0 if payload.get("ok") is not False else 1


if __name__ == "__main__":
    raise SystemExit(main())
