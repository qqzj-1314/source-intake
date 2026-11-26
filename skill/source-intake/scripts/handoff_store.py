#!/usr/bin/env python3
"""需求工作包的追加式交接记录存储（仅用 Python 标准库）。

调用方负责传入已读取的 context；本模块不会调用 read_context，
也不会修改需求、提示词、说明或附件等原始材料。
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import stat
import uuid
from datetime import datetime, timezone
from itertools import islice
from pathlib import Path
from typing import Any

SCHEMA_VERSION = 1
HANDOFFS_DIR = "handoffs"
REQUIREMENT_NAME = "requirement.json"

MAX_RECORDS = 500
MAX_RECORD_BYTES = 64 * 1024
MAX_TOTAL_READ_BYTES = 4 * 1024 * 1024
MAX_REQUIREMENT_BYTES = 1024 * 1024
DEFAULT_LIMIT = 20
MIN_LIMIT = 1
MAX_LIMIT = 100

MAX_AUTHOR_CHARS = 100
MAX_SUMMARY_CHARS = 8000
MAX_VALIDATION_CHARS = 12000
MAX_NEXT_STEPS_CHARS = 8000
MAX_OPEN_QUESTIONS_CHARS = 8000
MAX_SOURCE_REVISION_CHARS = 200
MAX_CHANGED_FILES = 100
MAX_CHANGED_FILE_CHARS = 500

STATUSES = frozenset({"in_progress", "blocked", "ready_for_review"})
TEMP_PREFIX = ".handoff-"
TEMP_SUFFIX = ".tmp"
PUBLISH_ATTEMPTS = 5

_HEX64 = re.compile(r"[0-9a-fA-F]{64}\Z")
_DRIVE_PREFIX = re.compile(r"[A-Za-z]:")
_REPARSE_ATTRIBUTE = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0)

_DATA_KEYS = frozenset({
    "author",
    "status",
    "summary",
    "changed_files",
    "validation",
    "next_steps",
    "open_questions",
    "source_revision",
    "expected_material_fingerprint",
})
_RECORD_KEYS = frozenset({
    "schema_version",
    "record_id",
    "created_at",
    "author",
    "status",
    "summary",
    "changed_files",
    "validation",
    "next_steps",
    "open_questions",
    "source_revision",
    "material_snapshot",
})


def _inspect(path: Path) -> tuple[str, os.stat_result | None, str | None]:
    try:
        info = os.lstat(path)
    except FileNotFoundError:
        return "missing", None, None
    except OSError as exc:
        return "error", None, str(exc)
    if stat.S_ISLNK(info.st_mode) or getattr(info, "st_file_attributes", 0) & _REPARSE_ATTRIBUTE:
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


def _context_text(context: dict[str, Any], key: str) -> str:
    value = context.get(key, "")
    if value is None:
        return ""
    if not isinstance(value, str):
        raise ValueError(f"context.{key} 必须是字符串")
    return value


def material_snapshot(context: dict[str, Any]) -> dict[str, Any]:
    """对已读取的原始材料做规范化指纹；不访问磁盘。"""
    if not isinstance(context, dict):
        raise ValueError("context 必须是对象")

    requirement = context.get("requirement", {})
    if requirement is None:
        requirement = {}
    prompt = _context_text(context, "prompt")
    readme = _context_text(context, "readme")

    attachments = context.get("attachments", [])
    if attachments is None:
        attachments = []
    if not isinstance(attachments, list):
        raise ValueError("context.attachments 必须是列表")
    complete = True
    for item in attachments:
        if not isinstance(item, dict):
            raise ValueError("context.attachments 的每一项必须是对象")
        relative_path = item.get("relative_path")
        if not isinstance(relative_path, str):
            raise ValueError("context.attachments 的每一项必须包含字符串 relative_path")
        digest = item.get("sha256")
        if not isinstance(digest, str) or _HEX64.fullmatch(digest) is None:
            complete = False
    attachments = sorted(attachments, key=lambda item: item["relative_path"])

    warnings = context.get("warnings", [])
    if warnings is None:
        warnings = []
    if not isinstance(warnings, list):
        raise ValueError("context.warnings 必须是列表")
    if warnings:
        complete = False

    payload = {
        "requirement": requirement,
        "prompt": prompt,
        "readme": readme,
        "attachments": attachments,
        "warnings": warnings,
    }
    try:
        canonical = json.dumps(
            payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
    except (TypeError, ValueError) as exc:
        raise ValueError(f"context 材料无法规范序列化：{exc}") from exc
    fingerprint = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
    return {"fingerprint": fingerprint, "complete": complete}


def _ensure_text(value: Any, label: str, limit: int, *, allow_empty: bool) -> str:
    if not isinstance(value, str):
        raise ValueError(f"{label} 必须是字符串")
    if len(value) > limit:
        raise ValueError(f"{label} 不能超过 {limit} 个字符")
    if not allow_empty and not value.strip():
        raise ValueError(f"{label} 不能为空")
    return value


def _validate_relative_path(value: Any, label: str) -> str:
    if not isinstance(value, str):
        raise ValueError(f"{label} 必须是字符串")
    if not value.strip():
        raise ValueError(f"{label} 不能为空")
    if len(value) > MAX_CHANGED_FILE_CHARS:
        raise ValueError(f"{label} 不能超过 {MAX_CHANGED_FILE_CHARS} 个字符")
    if "\x00" in value:
        raise ValueError(f"{label} 不能包含 NUL")
    if "\\" in value:
        raise ValueError(f"{label} 不能包含反斜杠：{value}")
    if value.startswith("/"):
        raise ValueError(f"{label} 必须是相对路径：{value}")
    if _DRIVE_PREFIX.match(value):
        raise ValueError(f"{label} 不能包含盘符：{value}")
    parts = value.split("/")
    if any(part in ("", ".", "..") for part in parts):
        raise ValueError(f"{label} 必须是规范化的项目相对路径：{value}")
    return value


def _normalize_business(source: dict[str, Any], *, require_expected: bool, require_all: bool) -> dict[str, Any]:
    if not isinstance(source, dict):
        raise ValueError("data 必须是对象")

    author = _ensure_text(source.get("author"), "author", MAX_AUTHOR_CHARS, allow_empty=False)
    status = source.get("status")
    if not isinstance(status, str) or status not in STATUSES:
        allowed = "|".join(sorted(STATUSES))
        raise ValueError(f"status 必须是 {allowed} 之一")
    summary = _ensure_text(source.get("summary"), "summary", MAX_SUMMARY_CHARS, allow_empty=False)

    changed_files = source.get("changed_files", [])
    if not isinstance(changed_files, list):
        raise ValueError("changed_files 必须是列表")
    if len(changed_files) > MAX_CHANGED_FILES:
        raise ValueError(f"changed_files 不能超过 {MAX_CHANGED_FILES} 项")
    normalized_files = []
    for index, item in enumerate(changed_files):
        normalized_files.append(_validate_relative_path(item, f"changed_files[{index}]"))

    def optional_text(key: str, limit: int) -> str:
        if key not in source:
            if require_all:
                raise ValueError(f"记录缺少字段 {key}")
            return ""
        return _ensure_text(source[key], key, limit, allow_empty=True)

    validation = optional_text("validation", MAX_VALIDATION_CHARS)
    next_steps = optional_text("next_steps", MAX_NEXT_STEPS_CHARS)
    open_questions = optional_text("open_questions", MAX_OPEN_QUESTIONS_CHARS)
    source_revision = optional_text("source_revision", MAX_SOURCE_REVISION_CHARS)

    if require_expected:
        expected = source.get("expected_material_fingerprint")
        if not isinstance(expected, str) or _HEX64.fullmatch(expected) is None:
            raise ValueError("expected_material_fingerprint 必须是 64 位十六进制字符串")

    return {
        "author": author,
        "status": status,
        "summary": summary,
        "changed_files": normalized_files,
        "validation": validation,
        "next_steps": next_steps,
        "open_questions": open_questions,
        "source_revision": source_revision,
    }


def _validate_data(data: Any) -> tuple[dict[str, Any], str]:
    if not isinstance(data, dict):
        raise ValueError("data 必须是对象")
    unknown = set(data) - _DATA_KEYS
    if unknown:
        raise ValueError(f"data 包含未知键：{', '.join(sorted(unknown))}")
    for key in ("author", "status", "summary", "expected_material_fingerprint"):
        if key not in data:
            raise ValueError(f"data 缺少必填字段 {key}")
    business = _normalize_business(data, require_expected=True, require_all=False)
    expected = str(data["expected_material_fingerprint"]).lower()
    return business, expected


def _validate_snapshot(value: Any) -> dict[str, Any] | None:
    if not isinstance(value, dict):
        return None
    if set(value) != {"fingerprint", "complete"}:
        return None
    fingerprint = value.get("fingerprint")
    complete = value.get("complete")
    if not isinstance(fingerprint, str) or _HEX64.fullmatch(fingerprint) is None:
        return None
    if not isinstance(complete, bool):
        return None
    return {"fingerprint": fingerprint.lower(), "complete": complete}


def _validate_record(value: Any, filename: str) -> tuple[dict[str, Any] | None, str | None]:
    if not filename.lower().endswith(".json"):
        return None, "文件名不是 .json"
    record_id = filename[:-5]
    if not record_id or record_id in (".", "..") or "/" in record_id or "\\" in record_id:
        return None, "文件名不能作为 record_id"
    if not isinstance(value, dict):
        return None, "顶层不是对象"

    keys = set(value)
    missing = _RECORD_KEYS - keys
    unknown = keys - _RECORD_KEYS
    if missing:
        return None, f"缺少字段 {', '.join(sorted(missing))}"
    if unknown:
        return None, f"包含未知字段 {', '.join(sorted(unknown))}"

    schema_version = value["schema_version"]
    if isinstance(schema_version, bool) or schema_version != SCHEMA_VERSION:
        return None, "schema_version 不是 1"

    stored_id = value["record_id"]
    if not isinstance(stored_id, str) or not stored_id or len(stored_id) > 200:
        return None, "record_id 必须是长度 1..200 的字符串"
    if stored_id != record_id:
        return None, "record_id 与文件名不一致"

    created_at = value["created_at"]
    if not isinstance(created_at, str) or not (1 <= len(created_at) <= 64):
        return None, "created_at 必须是长度 1..64 的字符串"

    snapshot = _validate_snapshot(value["material_snapshot"])
    if snapshot is None:
        return None, "material_snapshot 无效"

    try:
        business = _normalize_business(value, require_expected=False, require_all=True)
    except ValueError as exc:
        return None, str(exc)

    record = {
        "schema_version": SCHEMA_VERSION,
        "record_id": stored_id,
        "created_at": created_at,
        **business,
        "material_snapshot": snapshot,
    }
    return record, None


def _workspace(directory: Any, *, require_requirement: bool) -> Path:
    try:
        path = Path(os.fspath(directory))
    except (TypeError, ValueError) as exc:
        raise ValueError(f"目录参数无效：{exc}") from exc

    kind, _info, error = _inspect(path)
    if kind == "missing":
        raise ValueError(f"工作包目录不存在：{path}")
    if kind == "link":
        raise ValueError(f"工作包目录不能是符号链接或 Windows reparse point：{path}")
    if kind == "error":
        raise OSError(f"无法检查工作包目录：{error}")
    if kind != "directory":
        raise ValueError(f"工作包路径不是目录：{path}")

    try:
        root = path.resolve(strict=True)
    except OSError as exc:
        raise OSError(f"无法解析工作包目录：{exc}") from exc

    if require_requirement:
        _validate_requirement(root)
    return root


def _validate_requirement(root: Path) -> None:
    path = root / REQUIREMENT_NAME
    kind, _info, error = _inspect(path)
    if kind == "missing":
        raise ValueError(f"工作包缺少 {REQUIREMENT_NAME}")
    if kind == "link":
        raise ValueError(f"{REQUIREMENT_NAME} 不能是符号链接或 Windows reparse point")
    if kind == "error":
        raise OSError(f"无法检查 {REQUIREMENT_NAME}：{error}")
    if kind != "file":
        raise ValueError(f"{REQUIREMENT_NAME} 不是普通文件")
    if not _is_within(root, path):
        raise ValueError(f"{REQUIREMENT_NAME} 解析到工作包外")

    try:
        with open(path, "rb") as handle:
            data = handle.read(MAX_REQUIREMENT_BYTES + 1)
    except OSError as exc:
        raise OSError(f"无法读取 {REQUIREMENT_NAME}：{exc}") from exc
    if len(data) > MAX_REQUIREMENT_BYTES:
        raise ValueError(f"{REQUIREMENT_NAME} 超过 {MAX_REQUIREMENT_BYTES} 字节")

    try:
        text = data.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise ValueError(f"{REQUIREMENT_NAME} 不是有效的 UTF-8：{exc}") from exc
    try:
        value = json.loads(text)
    except (json.JSONDecodeError, RecursionError) as exc:
        raise ValueError(f"{REQUIREMENT_NAME} 不是有效 JSON：{exc}") from exc
    if not isinstance(value, dict):
        raise ValueError(f"{REQUIREMENT_NAME} 顶层必须是对象")
    if not (
        isinstance(value.get("requirement"), dict)
        or any(key in value for key in ("id", "title", "description"))
    ):
        raise ValueError(
            f"{REQUIREMENT_NAME} 必须包含 requirement 对象或 id/title/description 之一"
        )


def _existing_handoffs(root: Path) -> tuple[Path | None, str | None]:
    path = root / HANDOFFS_DIR
    kind, _info, error = _inspect(path)
    if kind == "missing":
        return None, None
    if kind == "error":
        return None, f"无法检查 {HANDOFFS_DIR}：{error}"
    if kind == "link":
        return None, f"{HANDOFFS_DIR} 是符号链接或 Windows reparse point，已跳过"
    if kind != "directory":
        return None, f"{HANDOFFS_DIR} 不是目录，已跳过"
    if not _is_within(root, path):
        return None, f"{HANDOFFS_DIR} 解析到工作包外，已跳过"
    try:
        resolved = path.resolve(strict=True)
    except OSError as exc:
        return None, f"无法解析 {HANDOFFS_DIR}：{exc}"
    kind, _info, error = _inspect(resolved)
    if kind == "link":
        return None, f"{HANDOFFS_DIR} 是符号链接或 Windows reparse point，已跳过"
    if kind == "error":
        return None, f"无法检查 {HANDOFFS_DIR}：{error}"
    if kind != "directory":
        return None, f"{HANDOFFS_DIR} 不是目录，已跳过"
    return resolved, None


def _ensure_handoffs(root: Path) -> Path:
    path = root / HANDOFFS_DIR
    for _attempt in range(2):
        kind, _info, error = _inspect(path)
        if kind == "missing":
            try:
                os.mkdir(path)
            except FileExistsError:
                continue
            except OSError as exc:
                raise OSError(f"无法创建 {HANDOFFS_DIR} 目录：{exc}") from exc
        elif kind == "link":
            raise ValueError(f"{HANDOFFS_DIR} 不能是符号链接或 Windows reparse point")
        elif kind == "error":
            raise OSError(f"无法检查 {HANDOFFS_DIR}：{error}")
        elif kind != "directory":
            raise ValueError(f"{HANDOFFS_DIR} 不是目录")

        if not _is_within(root, path):
            raise ValueError(f"{HANDOFFS_DIR} 解析到工作包外")
        try:
            resolved = path.resolve(strict=True)
        except OSError as exc:
            raise OSError(f"无法解析 {HANDOFFS_DIR}：{exc}") from exc
        try:
            resolved.relative_to(root)
        except ValueError as exc:
            raise ValueError(f"{HANDOFFS_DIR} 解析到工作包外") from exc
        kind, _info, error = _inspect(resolved)
        if kind == "link":
            raise ValueError(f"{HANDOFFS_DIR} 不能是符号链接或 Windows reparse point")
        if kind == "error":
            raise OSError(f"无法检查 {HANDOFFS_DIR}：{error}")
        if kind != "directory":
            raise ValueError(f"{HANDOFFS_DIR} 不是目录")
        return resolved
    raise OSError(f"无法创建 {HANDOFFS_DIR} 目录")


def _scan_handoffs(
    handoffs: Path, snapshot: dict[str, Any], *, strict_scan_error: bool,
) -> tuple[list[dict[str, Any]], list[str], bool, int]:
    warnings: list[str] = []
    truncated = False
    try:
        with os.scandir(handoffs) as iterator:
            entries = list(islice(iterator, MAX_RECORDS + 1))
    except OSError as exc:
        if strict_scan_error:
            raise OSError(f"无法扫描 {HANDOFFS_DIR}：{exc}") from exc
        return [], [f"无法扫描 {HANDOFFS_DIR}：{exc}"], False, 0

    entry_count = len(entries)
    if entry_count > MAX_RECORDS:
        truncated = True
        warnings.append(f"{HANDOFFS_DIR} 条目超过上限 {MAX_RECORDS}，仅检查部分条目")
    entries.sort(key=lambda item: item.name, reverse=True)
    if entry_count > MAX_RECORDS:
        entries = entries[:MAX_RECORDS]

    records: list[dict[str, Any]] = []
    remaining = MAX_TOTAL_READ_BYTES
    for entry in entries:
        name = entry.name
        lower_name = name.lower()
        if lower_name.endswith(".tmp"):
            continue
        if not lower_name.endswith(".json"):
            warnings.append(f"{HANDOFFS_DIR} 中存在未知文件，已跳过：{name}")
            continue

        path = handoffs / name
        kind, info, error = _inspect(path)
        if kind == "link":
            warnings.append(f"交接记录是符号链接或 Windows reparse point，已跳过：{name}")
            continue
        if kind == "missing":
            warnings.append(f"交接记录在扫描时消失，已跳过：{name}")
            continue
        if kind == "error":
            warnings.append(f"无法检查交接记录 {name}：{error}")
            continue
        if kind == "directory":
            warnings.append(f"{HANDOFFS_DIR} 中存在未知目录，已跳过：{name}")
            continue
        if kind != "file":
            warnings.append(f"交接记录不是普通文件，已跳过：{name}")
            continue
        if not _is_within(handoffs, path):
            warnings.append(f"交接记录解析到工作包外，已跳过：{name}")
            continue
        assert info is not None
        if info.st_size > MAX_RECORD_BYTES:
            warnings.append(
                f"交接记录超过 {MAX_RECORD_BYTES} 字节（64 KiB），已跳过：{name}"
            )
            continue
        if remaining <= 0:
            truncated = True
            warnings.append(f"读取总预算 {MAX_TOTAL_READ_BYTES} 字节已用完，已停止")
            break
        if info.st_size > remaining:
            truncated = True
            warnings.append(
                f"读取总预算剩余 {remaining} 字节，不足以读取 {name}，已停止"
            )
            break

        read_limit = min(MAX_RECORD_BYTES + 1, remaining)
        # Reserve before I/O: failed reads may already have consumed bytes.
        remaining -= read_limit
        try:
            with open(path, "rb") as handle:
                current_size = os.fstat(handle.fileno()).st_size
                if current_size > MAX_RECORD_BYTES:
                    warnings.append(f"交接记录超过 64 KiB，已跳过：{name}")
                    continue
                if current_size > read_limit:
                    truncated = True
                    warnings.append(f"交接记录在检查后增长，超出剩余读取预算：{name}")
                    break
                data = handle.read(read_limit)
                if os.fstat(handle.fileno()).st_size != len(data):
                    truncated = True
                    warnings.append(f"交接记录读取期间变化或未完整读取，已跳过：{name}")
                    continue
        except OSError as exc:
            warnings.append(f"无法读取交接记录 {name}：{exc}")
            continue
        remaining += read_limit - len(data)
        if len(data) > MAX_RECORD_BYTES:
            warnings.append(
                f"交接记录超过 {MAX_RECORD_BYTES} 字节（64 KiB），已跳过：{name}"
            )
            continue

        try:
            text = data.decode("utf-8-sig")
            value = json.loads(text)
        except (UnicodeDecodeError, json.JSONDecodeError, RecursionError) as exc:
            warnings.append(f"交接记录损坏，已跳过：{name}（{exc}）")
            continue
        record, reason = _validate_record(value, name)
        if record is None:
            warnings.append(f"交接记录格式无效，已跳过：{name}（{reason}）")
            continue
        records.append(record)

    return records, warnings, truncated, entry_count


def _material_match(stored: dict[str, Any], current: dict[str, Any]) -> str:
    stored_fingerprint = str(stored["fingerprint"]).lower()
    if (
        stored["complete"]
        and current["complete"]
        and stored_fingerprint == current["fingerprint"]
    ):
        return "unchanged"
    if stored_fingerprint != current["fingerprint"]:
        return "changed"
    return "unknown"


def read_handoffs(
    directory: str | Path, *, context: dict[str, Any], limit: int = DEFAULT_LIMIT,
) -> dict[str, Any]:
    if isinstance(limit, bool) or not isinstance(limit, int) or not (MIN_LIMIT <= limit <= MAX_LIMIT):
        raise ValueError(f"limit 必须是 {MIN_LIMIT}..{MAX_LIMIT} 的整数")

    snapshot = material_snapshot(context)
    root = _workspace(directory, require_requirement=False)
    handoffs, handoffs_warning = _existing_handoffs(root)

    warnings: list[str] = []
    records: list[dict[str, Any]] = []
    truncated = False
    if handoffs is None:
        if handoffs_warning:
            warnings.append(handoffs_warning)
    else:
        records, scan_warnings, truncated, _entry_count = _scan_handoffs(
            handoffs, snapshot, strict_scan_error=False,
        )
        warnings.extend(scan_warnings)
        records.sort(key=lambda item: item["record_id"], reverse=True)
        if len(records) > limit:
            truncated = True
            records = records[:limit]

    for record in records:
        record["material_match"] = _material_match(record["material_snapshot"], snapshot)

    return {
        "schema_version": SCHEMA_VERSION,
        "records": records,
        "warnings": warnings,
        "material_snapshot": snapshot,
        "truncated": truncated,
    }


def _new_record(
    business: dict[str, Any], snapshot: dict[str, Any],
) -> tuple[dict[str, Any], bytes]:
    now = datetime.now(timezone.utc)
    record = {
        "schema_version": SCHEMA_VERSION,
        "record_id": now.strftime("%Y%m%dT%H%M%S%fZ") + "-" + uuid.uuid4().hex,
        "created_at": now.strftime("%Y-%m-%dT%H:%M:%S.%fZ"),
        **business,
        "material_snapshot": dict(snapshot),
    }
    try:
        payload = json.dumps(
            record,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise ValueError(f"交接记录无法序列化为 JSON：{exc}") from exc
    if len(payload) > MAX_RECORD_BYTES:
        raise ValueError(f"交接记录超过 {MAX_RECORD_BYTES} 字节（64 KiB）")
    return record, payload


def _publish_record(
    handoffs: Path, business: dict[str, Any], snapshot: dict[str, Any],
) -> tuple[dict[str, Any], Path]:
    last_error: OSError | None = None
    for _attempt in range(PUBLISH_ATTEMPTS):
        record, payload = _new_record(business, snapshot)
        temp_path = handoffs / f"{TEMP_PREFIX}{uuid.uuid4().hex}{TEMP_SUFFIX}"
        created = False
        try:
            try:
                handle = open(temp_path, "xb")
            except FileExistsError:
                continue
            created = True
            with handle:
                handle.write(payload)
                handle.flush()
                os.fsync(handle.fileno())

            final_path = handoffs / f"{record['record_id']}.json"
            try:
                os.link(temp_path, final_path)
            except FileExistsError as exc:
                last_error = exc
                continue
            except (AttributeError, NotImplementedError) as exc:
                raise OSError("当前文件系统不支持原子 hard link 发布") from exc
            except OSError as exc:
                raise OSError(f"无法原子发布交接记录：{exc}") from exc

            try:
                os.unlink(temp_path)
            except OSError:
                pass
            return record, final_path
        finally:
            if created:
                try:
                    os.unlink(temp_path)
                except OSError:
                    pass
    raise OSError("无法原子发布交接记录：目标文件名连续冲突") from last_error


def _ensure_capacity(
    handoffs: Path, snapshot: dict[str, Any],
) -> None:
    _records, _warnings, truncated, entry_count = _scan_handoffs(
        handoffs, snapshot, strict_scan_error=True,
    )
    if truncated or entry_count >= MAX_RECORDS:
        raise ValueError(
            f"{HANDOFFS_DIR} 扫描预算或容量已满（上限 {MAX_RECORDS} 条），拒绝写入"
        )


def append_handoff(
    directory: str | Path, data: dict[str, Any], *, context: dict[str, Any],
) -> dict[str, Any]:
    business, expected = _validate_data(data)
    snapshot = material_snapshot(context)
    if expected != snapshot["fingerprint"]:
        raise ValueError("expected_material_fingerprint 与当前材料指纹不一致，拒绝写入")

    _new_record(business, snapshot)  # 在创建目录前拒绝超限记录

    root = _workspace(directory, require_requirement=True)
    handoffs = _ensure_handoffs(root)
    _ensure_capacity(handoffs, snapshot)
    record, final_path = _publish_record(handoffs, business, snapshot)
    return {
        "ok": True,
        "record": record,
        "path": os.path.abspath(str(final_path)),
    }
