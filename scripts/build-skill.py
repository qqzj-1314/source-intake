"""Build a portable .skill zip from a Skill folder without Git or PyYAML."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import stat
import sys
import zipfile
from pathlib import Path

PRODUCT_DIRS = ("agents", "references", "scripts")
EXCLUDED_DIRS = {"__pycache__", ".source-intake", "node_modules"}
NAME_PATTERN = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
VERSION_PATTERN = re.compile(r"^[0-9A-Za-z][0-9A-Za-z._-]*$")


def _error(message: str) -> ValueError:
    return ValueError(message)


def _read_frontmatter(skill_md: Path) -> dict[str, str]:
    try:
        text = skill_md.read_text(encoding="utf-8-sig")
    except UnicodeDecodeError as exc:
        raise _error("SKILL.md 必须是 UTF-8 文本") from exc
    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        raise _error("SKILL.md 缺少 YAML frontmatter")
    values: dict[str, str] = {}
    closed = False
    for line in lines[1:]:
        if line.strip() == "---":
            closed = True
            break
        if not line.strip() or line[:1].isspace():
            continue
        key, separator, value = line.partition(":")
        if not separator:
            continue
        key = key.strip()
        if key not in {"name", "description"}:
            continue
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        values[key] = value
    if not closed:
        raise _error("SKILL.md frontmatter 未闭合")
    name = values.get("name", "")
    description = values.get("description", "")
    if not NAME_PATTERN.fullmatch(name) or len(name) > 64:
        raise _error(f"Skill name 必须是 64 字符以内的 kebab-case，实际为 {name!r}")
    if not description:
        raise _error("SKILL.md frontmatter 缺少 description")
    return {"name": name, "description": description}


def _has_reparse_attribute(info: os.stat_result) -> bool:
    reparse = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0)
    return bool(getattr(info, "st_file_attributes", 0) & reparse)


def _is_link(entry: os.DirEntry) -> bool:
    try:
        if entry.is_symlink():
            return True
        info = entry.stat(follow_symlinks=False)
    except OSError:
        return True
    return _has_reparse_attribute(info)


def _path_is_link(path: Path) -> bool:
    info = os.lstat(path)
    return stat.S_ISLNK(info.st_mode) or _has_reparse_attribute(info)


def _is_within(path: Path, root: Path) -> bool:
    try:
        return path == root or path.is_relative_to(root)
    except ValueError:
        return False


def _walk(base: Path, prefix: str, output_root: Path, files: list[tuple[str, Path]]) -> None:
    with os.scandir(base) as entries:
        ordered = sorted(entries, key=lambda entry: entry.name)
    for entry in ordered:
        if entry.name.startswith(".") or _is_link(entry):
            continue
        path = Path(entry.path)
        try:
            resolved = path.resolve()
        except OSError:
            continue
        if _is_within(resolved, output_root):
            continue
        if entry.is_dir(follow_symlinks=False):
            if entry.name in EXCLUDED_DIRS:
                continue
            _walk(path, f"{prefix}/{entry.name}", output_root, files)
        elif entry.is_file(follow_symlinks=False):
            if entry.name.lower().endswith(".pyc"):
                continue
            files.append((f"{prefix}/{entry.name}", path))


def _collect(source: Path, output_root: Path) -> tuple[str, list[tuple[str, Path]]]:
    skill_md = source / "SKILL.md"
    if not skill_md.is_file():
        raise _error(f"未找到 SKILL.md: {skill_md}")
    if _path_is_link(skill_md):
        raise _error("SKILL.md 不能是符号链接或重解析点")
    frontmatter = _read_frontmatter(skill_md)
    files: list[tuple[str, Path]] = [("SKILL.md", skill_md)]
    for dirname in PRODUCT_DIRS:
        base = source / dirname
        if not base.is_dir() or _path_is_link(base):
            continue
        _walk(base, dirname, output_root, files)
    return frontmatter["name"], files


def build_skill(source: Path, output: Path, version: str) -> dict:
    """Package a Skill without colliding with the full project release archive."""
    source = Path(source).resolve()
    output_root = Path(output).resolve()
    if not source.is_dir():
        raise _error(f"source 不是目录: {source}")
    if not VERSION_PATTERN.fullmatch(version) or ".." in version:
        raise _error("version 必须是安全的文件名片段，例如 0.4.0")
    if output_root == source:
        raise _error("output 不能是 source 目录本身")
    name, files = _collect(source, output_root)
    stem = f"source-intake-skill-{version}"
    skill_path = output_root / f"{stem}.skill"
    zip_path = output_root / f"{stem}.zip"
    sha_path = output_root / f"{stem}.zip.sha256"
    skill_sha_path = output_root / f"{stem}.skill.sha256"
    output_root.mkdir(parents=True, exist_ok=True)
    temporary = output_root / f".{stem}.tmp"
    with zipfile.ZipFile(temporary, "w", zipfile.ZIP_DEFLATED) as archive:
        for relative, path in files:
            info = zipfile.ZipInfo(f"{name}/{relative}", date_time=(1980, 1, 1, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            info.create_system = 3
            info.external_attr = 0o644 << 16
            archive.writestr(info, path.read_bytes())
    data = temporary.read_bytes()
    temporary.unlink()
    digest = hashlib.sha256(data).hexdigest()
    skill_path.write_bytes(data)
    zip_path.write_bytes(data)
    sha_path.write_bytes(f"{digest}  {zip_path.name}\n".encode("ascii"))
    skill_sha_path.write_bytes(f"{digest}  {skill_path.name}\n".encode("ascii"))
    return {
        "name": name,
        "version": version,
        "skill": str(skill_path),
        "zip": str(zip_path),
        "sha256_file": str(sha_path),
        "skill_sha256_file": str(skill_sha_path),
        "files": len(files),
        "sha256": digest,
    }


def main(argv: list[str] | None = None) -> int:
    root = Path(__file__).resolve().parents[1]
    parser = argparse.ArgumentParser(description="将 Skill 目录打成可搬迁的 .skill 压缩包")
    parser.add_argument("--source", type=Path, default=root / "skill" / "source-intake")
    parser.add_argument("--output", type=Path, default=root / "dist")
    parser.add_argument("--version", required=True, help="发行版本号，例如 0.4.0")
    args = parser.parse_args(argv)
    try:
        result = build_skill(args.source, args.output, args.version)
    except (ValueError, OSError) as exc:
        print(f"错误: {exc}", file=sys.stderr)
        return 2
    print(json.dumps(result))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
