"""Read a bounded index of local requirement packages; never modify project data."""
from __future__ import annotations

import argparse
import json
import os
import stat
import sys
from pathlib import Path
from typing import Any

MAX_SCAN_ENTRIES = 1000
MAX_METADATA_BYTES = 256 * 1024
REPARSE = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0)


def ordinary(path: Path, directory: bool = False) -> bool:
    try:
        info = path.lstat()
        if stat.S_ISLNK(info.st_mode) or getattr(info, "st_file_attributes", 0) & REPARSE:
            return False
        return stat.S_ISDIR(info.st_mode) if directory else stat.S_ISREG(info.st_mode)
    except OSError:
        return False


def text(value: Any, limit: int) -> str:
    return value[:limit] if isinstance(value, str) else ""


def list_requirements(root: str | Path, query: str = "", limit: int = 50) -> dict[str, Any]:
    try:
        if not isinstance(root, (str, Path)) or not str(root).strip():
            raise ValueError("项目目录不能为空")
        if not isinstance(query, str) or not isinstance(limit, int) or isinstance(limit, bool) or not 1 <= limit <= 100:
            raise ValueError("query 必须是文本，limit 必须为 1-100")
        project = Path(root).expanduser().resolve(strict=True)
        if not project.is_dir():
            raise ValueError("项目路径不是文件夹")
        container = project / "requirements"
        warnings: list[str] = []
        result: dict[str, Any] = {"schema_version": 1, "ok": True, "project_root": str(project),
            "requirements_directory": str(container), "items": [], "matches": 0, "truncated": False, "warnings": warnings}
        if not os.path.lexists(container):
            return result
        if not ordinary(container, directory=True):
            raise ValueError("requirements 必须是普通目录，不能是链接或 junction")
        entries: list[Path] = []
        with os.scandir(container) as scan:
            for index, entry in enumerate(scan):
                if index >= MAX_SCAN_ENTRIES:
                    result["truncated"] = True
                    warnings.append(f"扫描达到 {MAX_SCAN_ENTRIES} 项上限，结果不完整")
                    break
                entries.append(Path(entry.path))
        items = []
        for package in sorted(entries, key=lambda p: (p.name.casefold(), p.name)):
            if not ordinary(package, directory=True):
                if package.is_symlink() or package.is_dir():
                    warnings.append(f"跳过链接目录：{package.name}")
                continue
            if not package.resolve().is_relative_to(project):
                warnings.append(f"跳过项目外目录：{package.name}")
                continue
            metadata = package / "requirement.json"
            value: dict[str, Any] = {}
            item_warnings = []
            if ordinary(metadata):
                try:
                    with metadata.open('rb') as handle:
                        data = handle.read(MAX_METADATA_BYTES + 1)
                    if len(data) > MAX_METADATA_BYTES:
                        raise ValueError("需求JSON超过256KiB，未解析")
                    decoded = json.loads(data.decode('utf-8-sig'))
                    if not isinstance(decoded, dict):
                        raise ValueError("需求JSON必须是对象")
                    value = decoded
                except (OSError, ValueError) as exc:
                    item_warnings.append(str(exc))
            elif os.path.lexists(metadata):
                item_warnings.append("需求JSON不是普通文件，未读取")
            else:
                item_warnings.append("尚无结构化需求JSON")
            requirement = value.get("requirement", value)
            if not isinstance(requirement, dict):
                requirement = {}
                item_warnings.append("requirement字段不是对象")
            req_id = text(requirement.get("id"), 200)
            title = text(requirement.get("title"), 500)
            if query.casefold() not in f"{req_id}\n{title}\n{package.name}".casefold():
                continue
            prompt = package / "修改说明.md"
            attachments = value.get("attachments")
            try:
                prompt_present = ordinary(prompt) and prompt.stat().st_size > 0
                modified_ns = package.stat().st_mtime_ns
            except OSError:
                warnings.append(f"需求目录在读取时变化，已跳过：{package.name}")
                continue
            items.append({"id": req_id, "title": title, "directory": str(package),
                "relative_dir": package.relative_to(project).as_posix(),
                "description_excerpt": text(requirement.get("description"), 500),
                "prompt_present": prompt_present,
                "imported_attachment_count": len(attachments) if isinstance(attachments, list) else None,
                "created_at": text(value.get("created_at"), 100),
                "modified_ns": modified_ns,
                "material_status": "needs-review" if item_warnings else "archived",
                "warnings": item_warnings})
        items.sort(key=lambda item: (-item["modified_ns"], item["relative_dir"].casefold()))
        result["matches"] = len(items)
        result["items"] = items[:limit]
        if len(items) > limit:
            result["truncated"] = True
            warnings.append(f"匹配 {len(items)} 个需求，当前仅返回前 {limit} 个；请缩小查询")
        return result
    except (OSError, ValueError) as exc:
        return {"schema_version": 1, "ok": False, "error": {"type": type(exc).__name__, "message": str(exc)}}


class JsonParser(argparse.ArgumentParser):
    def error(self, message):
        raise ValueError(message)


def main(argv=None):
    try:
        parser = JsonParser()
        parser.add_argument('--root', required=True)
        parser.add_argument('--query', default='')
        parser.add_argument('--limit', type=int, default=50)
        args = parser.parse_args(argv)
        result = list_requirements(args.root, args.query, args.limit)
    except ValueError as exc:
        result = {"ok": False, "error": {"type": "ArgumentError", "message": str(exc)}}
    if hasattr(sys.stdout, 'reconfigure'):
        sys.stdout.reconfigure(encoding='utf-8')
    print(json.dumps(result, ensure_ascii=False))
    return 0 if result.get('ok') else 1


if __name__ == '__main__':
    raise SystemExit(main())
