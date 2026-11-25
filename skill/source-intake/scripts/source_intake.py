#!/usr/bin/env python3
"""源码需求导入助手核心逻辑与 JSON CLI（仅用 Python 标准库）。"""

from __future__ import annotations

import argparse
import codecs
import hashlib
import json
import os
import re
import secrets
import sys
import tempfile
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

TOOL_NAME = "source-intake"
TOOL_VERSION = "1.0.0"
META_DIR = ".source-intake"
BATCHES_DIR = "batches"
KINDS = ("csp", "js", "css", "cls")
KIND_EXTENSIONS = {"csp": ".csp", "js": ".js", "css": ".css", "cls": ".cls"}
EXTENSION_KINDS = {v: k for k, v in KIND_EXTENSIONS.items()}

DEFAULT_ROUTES = {
    "hisui": {
        "csp": "frontend/csp",
        "js": "frontend/scripts/dhcnewpro/dhcemc",
        "css": "frontend/scripts/dhcnewpro/dhcemc/css",
        "cls": "backend",
    },
    "flat": {"csp": "", "js": "", "css": "", "cls": ""},
}

RESERVED_NAMES = {
    "con", "prn", "aux", "nul",
    *(f"com{i}" for i in range(1, 10)),
    *(f"lpt{i}" for i in range(1, 10)),
}
INVALID_SEGMENT_CHARS = set('<>:"|?*')

CLS_CLASS_RE = re.compile(r"(?im)^[ \t]*Class[ \t]+([A-Za-z%][A-Za-z0-9_%]*(?:\.[A-Za-z%][A-Za-z0-9_%]*)*)\b")
CSP_COMMENT_RES = (
    re.compile(r"<!--[^>]{0,400}?([A-Za-z0-9_.\-]+\.csp)\b", re.I | re.S),
    re.compile(r"/\*{1,2}[^*]{0,400}?([A-Za-z0-9_.\-]+\.csp)\b", re.I | re.S),
    re.compile(r"(?im)^[ \t]*;[ \t]*([A-Za-z0-9_.\-]+\.csp)\b"),
)
CSP_INLINE_RE = re.compile(r"<csp:", re.I)
SCRIPT_SRC_RE = re.compile(r"""<script\b[^>]*?\bsrc\s*=\s*(?:"([^"]+)"|'([^']+)')""", re.I | re.S)
LINK_HREF_RE = re.compile(r"""<link\b[^>]*?\bhref\s*=\s*(?:"([^"]+)"|'([^']+)')""", re.I | re.S)
CSS_HINT_RE = re.compile(
    r"(?im)^[ \t]*(?:@media\b|@import\b|:root\s*\{|[.#]?[A-Za-z][\w\-]*(?:\s+[.#]?[\w\-]+)*\s*\{[^{}]*:[^{}]*;)"
)
JS_HINT_RE = re.compile(
    r"(?:\bfunction\b|\bvar\s+[A-Za-z_$]|\blet\s+[A-Za-z_$]|\bconst\s+[A-Za-z_$]|=>|\bdocument\.|\bwindow\.|\bconsole\.|\balert\s*\(|\$\s*\()"
)


class SourceIntakeError(Exception):
    """可安全展示给用户的错误。"""


class ValidationError(SourceIntakeError):
    pass


class PlanMismatchError(SourceIntakeError):
    pass


@dataclass
class Requirement:
    system: str = "manual"
    id: str = ""
    title: str = ""
    description: str = ""
    url: str = ""
    updated_at: str = ""

    @classmethod
    def from_value(cls, value: Any) -> "Requirement":
        if value is None:
            return cls()
        if not isinstance(value, dict):
            raise ValidationError("requirement 必须是对象")
        system = str(value.get("system") or "manual").strip()
        if system.lower() == "bos":
            system = "BOS"
        elif system.lower() == "manual":
            system = "manual"
        else:
            raise ValidationError("requirement.system 仅支持 manual 或 BOS")
        result = cls(system=system)
        for name in ("id", "title", "description", "url", "updated_at"):
            raw = value.get(name, "")
            if raw is None:
                raw = ""
            if not isinstance(raw, str):
                raise ValidationError(f"requirement.{name} 必须是字符串")
            setattr(result, name, raw)
        return result

    def to_dict(self) -> dict[str, str]:
        return {
            "system": self.system,
            "id": self.id,
            "title": self.title,
            "description": self.description,
            "url": self.url,
            "updated_at": self.updated_at,
        }


@dataclass
class NormalizedRequest:
    root: Path
    project_name: str
    profile: str
    routes: dict[str, str]
    cls_layout: str
    requirement: Requirement
    sources: list[Any]
    raw: dict[str, Any] = field(default_factory=dict)


@dataclass
class PreparedSource:
    index: int
    source_kind: str
    source_path: str | None
    source_label: str
    original_filename: str | None
    data: bytes
    original_bytes: bytes
    imported_bytes: bytes
    text: str | None
    imported_text: str | None
    encoding: str
    bom: bool
    line_endings: str
    explicit_filename: str | None
    explicit_destination: str | None
    fence_language: str | None
    fence_stripped: bool
    fence_error: str | None
    notes: list[str]
    fatal: str | None
    fence_filename: str | None = None
    kind: str | None = None
    filename: str | None = None
    package: list[str] = field(default_factory=list)
    cls_declared: bool = False
    destination: str | None = None
    target_path: str | None = None
    target_key: str | None = None
    target_exists: bool = False
    target_sha256: str | None = None
    target_is_dir: bool = False
    status: str = "pending"
    original_sha256: str = ""
    imported_sha256: str = ""

    @property
    def source_name(self) -> str:
        if self.source_kind == "path" and self.source_path:
            return Path(self.source_path).name or self.source_path
        return self.original_filename or f"粘贴内容 #{self.index + 1}"


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def now_iso() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def atomic_write_bytes(path: Path, data: bytes) -> None:
    """同目录临时文件 + os.replace，避免产生半写文件。"""
    parent = path.parent
    parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(prefix=".source-intake-", suffix=".tmp", dir=str(parent))
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp_name, path)
    except BaseException:
        try:
            os.unlink(tmp_name)
        except OSError:
            pass
        raise


def atomic_write_json(path: Path, value: Any) -> None:
    payload = json.dumps(value, ensure_ascii=False, indent=2)
    atomic_write_bytes(path, payload.encode("utf-8"))


def _path_key(value: str | Path) -> str:
    return os.path.normcase(os.path.abspath(str(value)))


def _is_within(child: str | Path, parent: str | Path) -> bool:
    try:
        common = os.path.commonpath([_path_key(child), _path_key(parent)])
    except ValueError:
        return False
    return common == _path_key(parent)


def normalize_root(root: Any) -> Path:
    if not isinstance(root, str) or not root.strip():
        raise ValidationError("root 必须是非空的项目目录路径")
    expanded = os.path.expanduser(root.strip())
    absolute = os.path.abspath(expanded)
    resolved = os.path.realpath(absolute)
    path = Path(resolved)
    if path.exists() and not path.is_dir():
        raise ValidationError(f"项目目录不是文件夹：{path}")
    return path


def _validate_segment(segment: str, *, context: str) -> None:
    if segment in ("", ".", ".."):
        raise ValidationError(f"{context} 包含非法路径段：{segment!r}")
    if len(segment) != len(segment.strip()) or segment.endswith("."):
        raise ValidationError(f"{context} 的路径段不能以空格或点结尾：{segment!r}")
    if any(ch in INVALID_SEGMENT_CHARS for ch in segment):
        raise ValidationError(f"{context} 的路径段包含非法字符：{segment!r}")
    if any(ord(ch) < 32 for ch in segment):
        raise ValidationError(f"{context} 的路径段包含控制字符")
    if segment.split(".", 1)[0].casefold() in RESERVED_NAMES:
        raise ValidationError(f"{context} 使用了 Windows 保留名称：{segment!r}")
    if segment.casefold() == META_DIR:
        raise ValidationError(f"{context} 不能指向元数据目录 {META_DIR}")


def normalize_route(route: Any, kind: str) -> str:
    if route is None:
        route = ""
    if not isinstance(route, str):
        raise ValidationError(f"routes.{kind} 必须是字符串")
    text = route.strip().replace("\\", "/")
    while text.startswith("./"):
        text = text[2:]
    if text in ("", "."):
        return ""
    if text.startswith("/"):
        raise ValidationError(f"routes.{kind} 必须是相对目录")
    if re.match(r"^[A-Za-z]:", text):
        raise ValidationError(f"routes.{kind} 不能包含盘符")
    parts = text.split("/")
    if any(part in ("", ".", "..") for part in parts):
        raise ValidationError(f"routes.{kind} 包含非法路径段：{route!r}")
    for part in parts:
        _validate_segment(part, context=f"routes.{kind}")
    return "/".join(parts)


def normalize_request(request: Any) -> NormalizedRequest:
    if not isinstance(request, dict):
        raise ValidationError("请求 JSON 必须是对象")
    root = normalize_root(request.get("root"))
    profile = str(request.get("profile") or "hisui").strip().lower()
    if profile not in DEFAULT_ROUTES:
        raise ValidationError("profile 仅支持 hisui 或 flat")
    routes = dict(DEFAULT_ROUTES[profile])
    provided_routes = request.get("routes")
    if provided_routes is not None:
        if not isinstance(provided_routes, dict):
            raise ValidationError("routes 必须是对象")
        for kind in KINDS:
            if kind in provided_routes:
                routes[kind] = normalize_route(provided_routes[kind], kind)
    cls_layout = str(request.get("cls_layout") or "package").strip().lower()
    if cls_layout not in ("package", "fullname"):
        raise ValidationError("cls_layout 仅支持 package 或 fullname")
    requirement = Requirement.from_value(request.get("requirement"))
    project_name = request.get("project_name")
    if project_name is None or not str(project_name).strip():
        project_name = root.name or "未命名项目"
    elif not isinstance(project_name, str):
        raise ValidationError("project_name 必须是字符串")
    else:
        project_name = project_name.strip()
    sources = request.get("sources")
    if sources is None:
        sources = []
    if not isinstance(sources, list):
        raise ValidationError("sources 必须是数组")
    return NormalizedRequest(
        root=root,
        project_name=project_name,
        profile=profile,
        routes=routes,
        cls_layout=cls_layout,
        requirement=requirement,
        sources=sources,
        raw=request,
    )


def detect_text_encoding(data: bytes) -> tuple[str, str, bool]:
    for bom, encoding in (
        (codecs.BOM_UTF8, "utf-8-sig"),
        (codecs.BOM_UTF32_LE, "utf-32"),
        (codecs.BOM_UTF32_BE, "utf-32"),
        (codecs.BOM_UTF16_LE, "utf-16"),
        (codecs.BOM_UTF16_BE, "utf-16"),
    ):
        if data.startswith(bom):
            try:
                return encoding, data.decode(encoding), True
            except UnicodeDecodeError:
                break
    for encoding in ("utf-8", "gb18030"):
        try:
            return encoding, data.decode(encoding), False
        except UnicodeDecodeError:
            continue
    return "latin-1", data.decode("latin-1"), False


def detect_line_endings(text: str) -> str:
    crlf = text.count("\r\n")
    lf = text.count("\n") - crlf
    cr = text.count("\r") - crlf
    if crlf and not lf and not cr:
        return "crlf"
    if lf and not crlf and not cr:
        return "lf"
    if cr and not crlf and not lf:
        return "cr"
    if crlf or lf or cr:
        return "mixed"
    return "none"


def filename_extension_kind(filename: str | None) -> str | None:
    if not filename:
        return None
    return EXTENSION_KINDS.get(Path(filename).suffix.casefold())


def validate_filename_value(name: Any) -> str:
    if not isinstance(name, str) or not name.strip():
        raise ValidationError("文件名不能为空")
    name = name.strip()
    if "/" in name or "\\" in name:
        raise ValidationError("文件名不能包含目录分隔符；目录请写在目标相对路径中")
    _validate_segment(name, context="文件名")
    return name


def classify_text(text: str) -> str | None:
    head = text[:12000]
    if CSP_INLINE_RE.search(head):
        return "csp"
    if CLS_CLASS_RE.search(head):
        return "cls"
    if extract_csp_filename(text):
        return "csp"
    if CSS_HINT_RE.search(head):
        return "css"
    if JS_HINT_RE.search(head):
        return "js"
    return None


def extract_csp_filename(text: str) -> str | None:
    head = text[:12000]
    for pattern in CSP_COMMENT_RES:
        match = pattern.search(head)
        if match:
            candidate = match.group(1)
            try:
                return validate_filename_value(candidate)
            except ValidationError:
                return None
    return None


def extract_cls_info(text: str) -> tuple[list[str], str] | None:
    match = CLS_CLASS_RE.search(text[:50000])
    if not match:
        return None
    full = match.group(1)
    parts = [part for part in full.split(".") if part]
    if not parts:
        return None
    try:
        for part in parts:
            _validate_segment(part + ".x", context="CLS 类名")
    except ValidationError:
        return None
    return parts[:-1], parts[-1]


def normalize_destination(destination: Any, root: Path) -> tuple[str, Path, str]:
    if not isinstance(destination, str) or not destination.strip():
        raise ValidationError("目标相对路径不能为空")
    text = destination.strip().replace("\\", "/")
    if text.startswith("/") or text.startswith("//"):
        raise ValidationError("目标必须是不以分隔符开头的相对路径")
    if re.match(r"^[A-Za-z]:", text):
        raise ValidationError("目标必须是相对路径，不能包含盘符")
    parts = text.split("/")
    for part in parts:
        _validate_segment(part, context="目标相对路径")
    normalized = "/".join(parts)
    target = root.joinpath(*parts)
    resolved_root = Path(os.path.realpath(root))
    resolved_target = Path(os.path.realpath(target))
    if _path_key(resolved_root) == _path_key(resolved_target):
        raise ValidationError("目标必须是项目目录内的文件路径")
    if not _is_within(resolved_target, resolved_root):
        raise ValidationError("解析链接后的目标仍在项目目录之外，已拒绝")
    existing = Path(os.path.realpath(target.parent))
    if not _is_within(existing, resolved_root):
        raise ValidationError("目标所在目录解析后位于项目目录之外，已拒绝")
    if target.exists():
        if target.is_dir():
            raise ValidationError("目标路径已存在同名目录，不能用文件覆盖目录")
        if not target.is_file():
            raise ValidationError("目标路径已存在且不是普通文件")
    return normalized, target, _path_key(resolved_target)


def _read_target_state(target: Path) -> tuple[bool, str | None]:
    if not target.exists():
        return False, None
    if target.is_dir():
        raise ValidationError("目标路径已存在同名目录")
    if not target.is_file():
        raise ValidationError("目标路径已存在且不是普通文件")
    return True, sha256_file(target)


def _fence_language(value: str) -> str | None:
    token = value.strip().split()[0] if value.strip() else ""
    token = token.strip().strip("{}").casefold()
    mapping = {
        "js": "js", "javascript": "js", "mjs": "js",
        "css": "css",
        "csp": "csp", "cache-csp": "csp",
        "cls": "cls", "objectscript": "cls",
    }
    return mapping.get(token)


def _extract_fence_filename(info: str) -> str | None:
    for raw_token in re.split(r"[\s,]+", info.strip()):
        token = raw_token.strip().strip("{}").strip()
        if "=" in token:
            token = token.split("=", 1)[1]
        token = token.strip().strip("\"'")
        token = token.replace("\\", "/").rsplit("/", 1)[-1]
        if not token or filename_extension_kind(token) is None:
            continue
        try:
            return validate_filename_value(token)
        except ValidationError:
            return None
    return None


def parse_pasted_text(text: str) -> tuple[str, str | None, str | None, bool, str | None, str | None]:
    """返回 (可导入文本, 围栏语言, 围栏文件名, 是否剥离, 围栏错误, 说明)。

    只处理包住全部文本的一对最外层围栏；多围栏或结构不明确时拒绝自动剥离。
    """
    lines = text.splitlines(keepends=True)
    fence_lines = [i for i, line in enumerate(lines) if re.match(r"^[ \t]*`{3,}", line)]
    if not fence_lines:
        return text, None, None, False, None, None
    if len(fence_lines) != 2:
        return text, None, None, False, "检测到多个或未闭合的代码围栏，请一次只收集一段源码", None
    first, last = fence_lines
    preamble = "".join(lines[:first])
    postamble = "".join(lines[last + 1:])
    opening = re.match(r"^[ \t]*`{3,}[ \t]*([^\s`]*)", lines[first])
    info_match = re.match(r"^[ \t]*`{3,}[ \t]*(.*?)[ \t]*\r?\n?$", lines[first])
    closing_ok = re.match(r"^[ \t]*`{3,}[ \t]*\r?\n?$", lines[last]) is not None
    if preamble.strip() or postamble.strip() or not closing_ok or opening is None:
        return text, None, None, False, "代码围栏之外还有正文或围栏结构不明确，请一次只收集一段源码", None
    inner = "".join(lines[first + 1:last])
    info = info_match.group(1) if info_match else opening.group(1)
    language = _fence_language(opening.group(1)) if opening else None
    fence_filename = _extract_fence_filename(info)
    if not inner.strip():
        return inner, language, fence_filename, True, "粘贴内容为空", None
    return inner, language, fence_filename, True, None, "已剥离最外层代码围栏"


def _make_notes(*values: str | None) -> list[str]:
    return [value for value in values if value]


def _load_source_spec(request: NormalizedRequest, index: int, spec: Any) -> PreparedSource:
    notes: list[str] = []
    if not isinstance(spec, dict):
        return PreparedSource(
            index=index, source_kind="text", source_path=None, source_label=f"来源 #{index + 1}",
            original_filename=None, data=b"", original_bytes=b"", imported_bytes=b"",
            text=None, imported_text=None, encoding="", bom=False, line_endings="none",
            explicit_filename=None, explicit_destination=None, fence_language=None,
            fence_stripped=False, fence_error=None, notes=[], fatal="来源条目必须是对象",
        )
    path_value = spec.get("path")
    text_value = spec.get("text")
    filename_value = spec.get("filename")
    destination_value = spec.get("destination")
    explicit_filename = filename_value.strip() if isinstance(filename_value, str) and filename_value.strip() else None
    explicit_destination = destination_value.strip() if isinstance(destination_value, str) and destination_value.strip() else None
    has_path = path_value is not None and path_value != ""
    has_text = text_value is not None
    if has_path == has_text:
        fatal = "来源条目必须且只能提供 path 或 text 之一"
        return PreparedSource(
            index=index, source_kind="path" if has_path else "text", source_path=str(path_value) if has_path else None,
            source_label=str(path_value) if has_path else f"粘贴内容 #{index + 1}",
            original_filename=explicit_filename, data=b"", original_bytes=b"", imported_bytes=b"",
            text=None, imported_text=None, encoding="", bom=False, line_endings="none",
            explicit_filename=explicit_filename, explicit_destination=explicit_destination,
            fence_language=None, fence_stripped=False, fence_error=None, notes=[], fatal=fatal,
        )
    if has_path:
        if not isinstance(path_value, str):
            return _fatal_prepared(index, "path 必须是字符串", explicit_filename, explicit_destination)
        path = Path(os.path.abspath(os.path.expanduser(path_value.strip())))
        if not path.exists():
            return _fatal_prepared(index, f"来源文件不存在：{path}", explicit_filename, explicit_destination, path)
        if not path.is_file():
            return _fatal_prepared(index, f"来源路径不是文件：{path}", explicit_filename, explicit_destination, path)
        try:
            data = path.read_bytes()
        except OSError as exc:
            return _fatal_prepared(index, f"读取来源文件失败：{exc}", explicit_filename, explicit_destination, path)
        encoding, decoded, bom = detect_text_encoding(data)
        imported_bytes = data
        imported_text = decoded
        fence_stripped = False
        fence_language = None
        fence_filename = None
        fence_error = None
        extra_note = None
        source_kind = "path"
        source_path = str(path)
        source_label = path.name or str(path)
        original_filename = path.name
    else:
        if not isinstance(text_value, str):
            return _fatal_prepared(index, "text 必须是字符串", explicit_filename, explicit_destination)
        data = text_value.encode("utf-8")
        encoding = "utf-8"
        bom = text_value.startswith("\ufeff")
        imported_text, fence_language, fence_filename, fence_stripped, fence_error, extra_note = parse_pasted_text(text_value)
        imported_bytes = imported_text.encode("utf-8")
        decoded = text_value
        source_kind = "text"
        source_path = None
        source_label = f"粘贴内容 #{index + 1}"
        original_filename = explicit_filename
    line_endings = detect_line_endings(imported_text or "")
    prepared = PreparedSource(
        index=index,
        source_kind=source_kind,
        source_path=source_path,
        source_label=source_label,
        original_filename=original_filename,
        data=data,
        original_bytes=data,
        imported_bytes=imported_bytes,
        text=decoded,
        imported_text=imported_text,
        encoding=encoding,
        bom=bom,
        line_endings=line_endings,
        explicit_filename=explicit_filename,
        explicit_destination=explicit_destination,
        fence_language=fence_language,
        fence_filename=fence_filename,
        fence_stripped=fence_stripped,
        fence_error=fence_error,
        notes=notes,
        fatal=None,
    )
    if extra_note:
        prepared.notes.append(extra_note)
    if not imported_bytes:
        prepared.fatal = "源码内容为空，未导入"
    prepared.original_sha256 = sha256_bytes(prepared.original_bytes)
    prepared.imported_sha256 = sha256_bytes(prepared.imported_bytes)
    return prepared


def _fatal_prepared(
    index: int,
    message: str,
    explicit_filename: str | None,
    explicit_destination: str | None,
    path: Path | None = None,
) -> PreparedSource:
    return PreparedSource(
        index=index, source_kind="path" if path else "text", source_path=str(path) if path else None,
        source_label=path.name if path else f"粘贴内容 #{index + 1}",
        original_filename=explicit_filename, data=b"", original_bytes=b"", imported_bytes=b"",
        text=None, imported_text=None, encoding="", bom=False, line_endings="none",
        explicit_filename=explicit_filename, explicit_destination=explicit_destination,
        fence_language=None, fence_stripped=False, fence_error=None, notes=[], fatal=message,
    )


def _prepare_sources(request: NormalizedRequest) -> list[PreparedSource]:
    items = [_load_source_spec(request, index, spec) for index, spec in enumerate(request.sources)]
    for item in items:
        _resolve_identity(request, item)
    _assign_reference_names(items)
    for item in items:
        _finalize_item(request, item)
    _mark_duplicate_targets(items)
    return items


def _assign_reference_names(items: list[PreparedSource]) -> None:
    refs: dict[str, list[str]] = {"js": [], "css": []}
    for item in items:
        if item.fatal or item.kind != "csp" or not item.text:
            continue
        for match in SCRIPT_SRC_RE.finditer(item.text):
            _add_reference(refs["js"], match.group(1) or match.group(2))
        for match in LINK_HREF_RE.finditer(item.text):
            _add_reference(refs["css"], match.group(1) or match.group(2))
    for kind in ("js", "css"):
        unnamed = [
            item for item in items
            if item.kind == kind and not item.filename and not item.fatal
            and not item.fence_error and item.status != "invalid"
        ]
        if not unnamed:
            continue
        named = {item.filename.casefold() for item in items if item.filename and item.kind == kind}
        remaining = [name for name in refs[kind] if name.casefold() not in named]
        for item in unnamed:
            if len(unnamed) == 1 and len(remaining) == 1:
                item.filename = remaining[0]
                item.notes.append(f"根据 CSP 中的唯一 {kind.upper()} 引用补充文件名：{remaining[0]}")
            elif remaining:
                item.notes.append(
                    f"CSP 引用候选为 {', '.join(remaining)}；无法唯一匹配，请补充文件名"
                )
            else:
                item.notes.append("未提供文件名，且 CSP 引用无法唯一匹配，请补充文件名")


def _add_reference(bucket: list[str], raw: str) -> None:
    cleaned = raw.strip().split("?", 1)[0].split("#", 1)[0].replace("\\", "/")
    name = cleaned.rsplit("/", 1)[-1]
    if not name:
        return
    if filename_extension_kind(name) is None:
        return
    if name.casefold() not in {item.casefold() for item in bucket}:
        bucket.append(name)


def _add_note(item: PreparedSource, note: str) -> None:
    if note and note not in item.notes:
        item.notes.append(note)


def _fill_cls_identity(item: PreparedSource) -> None:
    info = extract_cls_info(item.text or "")
    if info is not None:
        item.cls_declared = True
        item.package, class_name = info
        if not item.filename:
            item.filename = class_name + ".cls"
    if (
        item.source_kind == "path"
        and not item.explicit_filename
        and item.source_path
        and filename_extension_kind(item.source_path) == "cls"
        and item.original_filename
    ):
        item.filename = item.original_filename
        if info is not None:
            _add_note(item, "已优先保留原 .cls 文件名，包层级取自 Class 声明")


def _resolve_identity(request: NormalizedRequest, item: PreparedSource) -> None:
    if item.fatal:
        item.status = "invalid"
        _add_note(item, item.fatal)
        return
    detected_kind = None
    if item.source_kind == "path":
        detected_kind = filename_extension_kind(item.source_path)
    if detected_kind is None and item.fence_language:
        detected_kind = item.fence_language
    if detected_kind is None and item.fence_filename:
        detected_kind = filename_extension_kind(item.fence_filename)
    if detected_kind is None and item.text:
        detected_kind = classify_text(item.text)
    item.kind = detected_kind

    if item.explicit_destination:
        try:
            normalized, target, target_key = normalize_destination(item.explicit_destination, request.root)
            dest_kind = filename_extension_kind(normalized)
            if dest_kind is None:
                raise ValidationError("目标相对路径必须以 .csp、.js、.css 或 .cls 结尾")
            item.destination = normalized
            item.target_path = str(target)
            item.target_key = target_key
            if detected_kind and detected_kind != dest_kind:
                _add_note(
                    item,
                    f"目标扩展名识别为 {dest_kind}，内容识别为 {detected_kind}，以目标路径为准",
                )
            item.kind = dest_kind
            item.filename = Path(normalized).name
            if item.kind == "cls":
                _fill_cls_identity(item)
        except ValidationError as exc:
            item.status = "invalid"
            _add_note(item, str(exc))
        return

    if item.explicit_filename:
        try:
            name = validate_filename_value(item.explicit_filename)
            ext_kind = filename_extension_kind(name)
            if ext_kind is None:
                if not Path(name).suffix and item.kind:
                    name = name + KIND_EXTENSIONS[item.kind]
                    _add_note(item, f"已按识别类型补全扩展名：{KIND_EXTENSIONS[item.kind]}")
                    ext_kind = item.kind
                else:
                    _add_note(item, "文件名扩展名不受支持，请使用 .csp、.js、.css 或 .cls")
                    item.kind = None
                    return
            if item.kind and ext_kind != item.kind:
                _add_note(item, f"文件名扩展名识别为 {ext_kind}，内容识别为 {item.kind}，以文件名为准")
            item.kind = ext_kind
            item.filename = name
            if item.kind == "cls":
                _fill_cls_identity(item)
        except ValidationError as exc:
            item.status = "invalid"
            _add_note(item, str(exc))
        return

    if item.source_kind == "path" and item.source_path:
        path_kind = filename_extension_kind(item.source_path)
        if path_kind in KINDS:
            item.filename = item.original_filename or Path(item.source_path).name
    if item.fence_filename and not item.filename:
        item.filename = item.fence_filename
        _add_note(item, f"使用代码围栏中的文件名：{item.fence_filename}")
    if item.kind == "csp":
        if not item.filename:
            csp_name = extract_csp_filename(item.text or "")
            if csp_name:
                item.filename = csp_name
    elif item.kind == "cls":
        _fill_cls_identity(item)
    elif item.kind is None and item.source_kind == "path" and filename_extension_kind(item.source_path) is None:
        _add_note(item, "无法识别源码类型，请补充文件名或目标相对路径")


def _finalize_item(request: NormalizedRequest, item: PreparedSource) -> None:
    if item.fatal:
        item.status = "invalid"
        _add_note(item, item.fatal)
        return
    if item.status == "invalid":
        return
    if item.fence_error:
        item.status = "pending"
        _add_note(item, item.fence_error)
        return
    if not item.kind:
        item.status = "pending"
        _add_note(item, "无法识别源码类型，请补充文件名或目标相对路径")
        return
    if not item.filename:
        item.status = "pending"
        _add_note(item, "未提供文件名，请补充文件名或目标相对路径")
        return
    if item.kind == "cls" and request.cls_layout == "package" and not item.explicit_destination:
        if not item.cls_declared:
            item.status = "pending"
            _add_note(item, "未能从 Class 声明识别包层级，请补充目标相对路径")
            return
    if item.destination is None:
        try:
            item.destination, item.target_path, item.target_key = _auto_destination(request, item)
        except ValidationError as exc:
            item.status = "invalid"
            _add_note(item, str(exc))
            return

    target = Path(item.target_path) if item.target_path else request.root / item.destination
    try:
        exists, target_sha = _read_target_state(target)
    except (OSError, ValidationError) as exc:
        item.status = "invalid"
        _add_note(item, f"无法检查目标文件：{exc}")
        return
    item.target_exists = exists
    item.target_sha256 = target_sha
    if not exists:
        item.status = "ready"
    elif target_sha == item.imported_sha256:
        item.status = "same"
        _add_note(item, "目标文件内容一致，导入时将跳过")
    else:
        item.status = "exists"
        _add_note(item, "目标已存在且内容不同，默认跳过；可选择备份后替换")
    if item.kind == "cls" and request.cls_layout == "fullname":
        _add_note(item, "flat/fullname 布局：CLS 使用完整包名作为文件名")


def _auto_destination(request: NormalizedRequest, item: PreparedSource) -> tuple[str, str, str]:
    base = request.routes.get(item.kind or "", "")
    filename = item.filename or ""
    if item.kind == "cls" and request.cls_layout == "package":
        parts = [base] if base else []
        parts.extend(item.package)
        parts.append(filename)
        destination = "/".join(part for part in parts if part)
    elif item.kind == "cls" and request.cls_layout == "fullname":
        stem = Path(filename).stem
        full_name = ".".join([*item.package, stem]) + ".cls"
        destination = "/".join(part for part in (base, full_name) if part)
    else:
        destination = "/".join(part for part in (base, filename) if part)
    normalized, target, target_key = normalize_destination(destination, request.root)
    return normalized, str(target), target_key


def _mark_duplicate_targets(items: list[PreparedSource]) -> None:
    groups: dict[str, list[PreparedSource]] = {}
    for item in items:
        if item.target_key and item.status in ("ready", "same", "exists"):
            groups.setdefault(item.target_key, []).append(item)
    for group in groups.values():
        if len(group) < 2:
            continue
        labels = "、".join(f"#{member.index + 1}" for member in group)
        for member in group:
            member.status = "conflict"
            member.notes.append(f"同批多个来源指向同一目标（含大小写等价）：{labels}")


def _usage_summary() -> dict[str, int]:
    return {
        "total": 0, "ready": 0, "pending": 0, "invalid": 0,
        "conflict": 0, "same": 0, "exists": 0,
    }


def _plan_item(item: PreparedSource) -> dict[str, Any]:
    return {
        "index": item.index,
        "source_kind": item.source_kind,
        "source_path": item.source_path,
        "source_name": item.source_name,
        "source_label": item.source_label,
        "original_filename": item.original_filename,
        "kind": item.kind,
        "filename": item.filename,
        "package": list(item.package),
        "destination": item.destination,
        "target_path": item.target_path,
        "status": item.status,
        "notes": list(item.notes),
        "encoding": item.encoding,
        "bom": item.bom,
        "line_endings": item.line_endings,
        "fence_language": item.fence_language,
        "fence_filename": item.fence_filename,
        "fence_stripped": item.fence_stripped,
        "original_sha256": item.original_sha256,
        "imported_sha256": item.imported_sha256,
        "target_exists": item.target_exists,
        "target_sha256": item.target_sha256,
        "target_is_dir": item.target_is_dir,
    }


def _summarize_plan(plan_items: list[dict[str, Any]]) -> dict[str, int]:
    summary = _usage_summary()
    summary["total"] = len(plan_items)
    for item in plan_items:
        status = item["status"]
        if status in summary:
            summary[status] += 1
    return summary


def _make_plan(request: NormalizedRequest, prepared: list[PreparedSource]) -> dict[str, Any]:
    plan_items = [_plan_item(item) for item in prepared]
    summary = _summarize_plan(plan_items)
    warnings: list[str] = []
    for item in plan_items:
        if item["status"] in ("pending", "invalid", "conflict"):
            warnings.append(
                f"来源 #{item['index'] + 1}（{item['source_name']}）状态为 {item['status']}："
                + "；".join(item["notes"])
            )
    return {
        "ok": True,
        "command": "plan",
        "tool": TOOL_NAME,
        "version": TOOL_VERSION,
        "root": str(request.root),
        "root_exists": request.root.is_dir(),
        "project_name": request.project_name,
        "profile": request.profile,
        "routes": dict(request.routes),
        "cls_layout": request.cls_layout,
        "requirement": request.requirement.to_dict(),
        "items": plan_items,
        "summary": summary,
        "warnings": warnings,
        "conflict_policy_default": "skip",
    }


def build_plan(request: Any) -> dict[str, Any]:
    normalized = normalize_request(request)
    prepared = _prepare_sources(normalized)
    return _make_plan(normalized, prepared)


def _expected_plan_items(expected: Any) -> list[dict[str, Any]]:
    if not isinstance(expected, dict):
        raise ValidationError("expected-plan 必须是 plan 输出的 JSON 对象")
    items = expected.get("items")
    if not isinstance(items, list):
        raise ValidationError("expected-plan 缺少 items 数组")
    for item in items:
        if not isinstance(item, dict):
            raise ValidationError("expected-plan 的 items 条目必须是对象")
    return items


def verify_expected_plan(plan: dict[str, Any], expected: Any) -> None:
    expected_items = _expected_plan_items(expected)
    expected_root = expected.get("root")
    if isinstance(expected_root, str) and expected_root:
        try:
            expected_root_norm = str(normalize_root(expected_root))
        except ValidationError:
            expected_root_norm = expected_root
        if _path_key(expected_root_norm) != _path_key(plan["root"]):
            raise PlanMismatchError("预览记录的项目目录与当前请求不一致，请重新预览")
    current_items = plan["items"]
    if len(current_items) != len(expected_items):
        raise PlanMismatchError("来源清单在预览后发生变化，请重新预览")
    for expected_item, current_item in zip(expected_items, current_items):
        index = current_item["index"] + 1
        for hash_field in ("original_sha256", "imported_sha256", "target_sha256"):
            if hash_field in expected_item and expected_item.get(hash_field) != current_item.get(hash_field):
                raise PlanMismatchError(
                    f"来源 #{index} 的{hash_field}在预览后发生变化，请重新预览"
                )
        expected_dest = expected_item.get("destination")
        current_dest = current_item.get("destination")
        if expected_dest is None and current_dest is None:
            continue
        if not isinstance(expected_dest, str) or not isinstance(current_dest, str):
            raise PlanMismatchError(f"来源 #{index} 的目标状态在预览后发生变化，请重新预览")
        if expected_dest.replace("\\", "/").casefold() != current_dest.replace("\\", "/").casefold():
            raise PlanMismatchError(f"来源 #{index} 的目标路径在预览后发生变化，请重新预览")


def _revalidate_target(root: Path, item: PreparedSource, *, create_parents: bool = False) -> Path:
    if not item.destination:
        raise PlanMismatchError("目标相对路径为空，请重新预览")
    if root.exists() and _path_key(os.path.realpath(root)) != _path_key(root):
        raise PlanMismatchError("项目目录本身在预览后变成了链接或已改变落点，请重新预览")
    try:
        _, target, target_key = normalize_destination(item.destination, root)
    except ValidationError as exc:
        raise PlanMismatchError(
            f"目标 {item.destination} 解析后不再安全（{exc}），已拒绝写入，请重新预览"
        ) from exc
    if item.target_key and target_key != item.target_key:
        raise PlanMismatchError(
            f"目标 {item.destination} 解析链接后的落点在预览后发生变化，请重新预览"
        )
    if create_parents:
        target.parent.mkdir(parents=True, exist_ok=True)
        try:
            _, target, target_key = normalize_destination(item.destination, root)
        except ValidationError as exc:
            raise PlanMismatchError(
                f"目标 {item.destination} 的目录在写入前未能保持在项目内（{exc}），请重新预览"
            ) from exc
        if item.target_key and target_key != item.target_key:
            raise PlanMismatchError(
                f"目标 {item.destination} 解析链接后的落点在写入前发生变化，请重新预览"
            )
    return target


def _preflight_targets(plan: dict[str, Any], prepared: list[PreparedSource]) -> None:
    root = Path(plan["root"])
    if root.exists() and not root.is_dir():
        raise ValidationError(f"项目目录不是文件夹：{root}")
    for item in prepared:
        if item.status not in ("ready", "same", "exists"):
            continue
        target = _revalidate_target(root, item)
        try:
            exists, target_sha = _read_target_state(target)
        except (OSError, ValidationError) as exc:
            raise PlanMismatchError(f"无法复核目标 {item.destination}：{exc}；请重新预览") from exc
        if exists != item.target_exists or target_sha != item.target_sha256:
            raise PlanMismatchError(
                f"目标 {item.destination} 在预览后发生变化，已拒绝写入，请重新预览"
            )


def _prepare_metadata_dirs(root: Path) -> Path:
    root_real = Path(os.path.realpath(root))
    if _path_key(root_real) != _path_key(root):
        raise ValidationError("项目目录本身是链接或落点已改变，已拒绝写入")
    meta = root / META_DIR
    if os.path.lexists(meta):
        if not meta.is_dir():
            raise ValidationError(f"元数据路径不是文件夹：{meta}")
    else:
        meta.mkdir()
    if not _is_within(os.path.realpath(meta), root_real):
        raise ValidationError(f"元数据目录 {META_DIR} 解析后位于项目目录之外，已拒绝写入")
    batches = meta / BATCHES_DIR
    if os.path.lexists(batches):
        if not batches.is_dir():
            raise ValidationError(f"批次目录不是文件夹：{batches}")
    else:
        batches.mkdir()
    if not _is_within(os.path.realpath(batches), root_real):
        raise ValidationError(f"批次目录解析后位于项目目录之外，已拒绝写入")
    return batches


def _new_batch_id(batches_dir: Path) -> str:
    stamp = time.strftime("%Y%m%d-%H%M%S")
    for _ in range(20):
        candidate = f"{stamp}-{secrets.token_hex(3)}"
        if not (batches_dir / candidate).exists():
            return candidate
    raise SourceIntakeError("无法生成唯一批次编号")


def _safe_snapshot_name(item: PreparedSource) -> str:
    raw_name = item.source_name or "source"
    cleaned = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", raw_name).strip(" .")
    if not cleaned:
        cleaned = "source"
    if len(cleaned) > 80:
        stem = Path(cleaned).stem[:60]
        suffix = Path(cleaned).suffix[:10]
        cleaned = stem + suffix
    prefix = f"{item.index + 1:04d}_"
    if item.source_kind == "text" and not item.original_filename:
        cleaned = "pasted.txt"
    return prefix + cleaned


def _blank_result(plan: dict[str, Any], status: str, message: str | None = None) -> dict[str, Any]:
    result = {
        "ok": True,
        "command": "apply",
        "tool": TOOL_NAME,
        "version": TOOL_VERSION,
        "batch_id": None,
        "batch_dir": None,
        "status": status,
        "items": [
            dict(item, plan_status=item["status"], status=item["status"], final_status=item["status"])
            for item in plan["items"]
        ],
        "summary": _summarize_plan(plan["items"]),
        "plan_summary": dict(plan["summary"]),
        "root": plan["root"],
        "project_name": plan["project_name"],
        "requirement": plan["requirement"],
    }
    if message:
        result["message"] = message
    return result


def _manifest_item(item: PreparedSource, snapshot_rel: str | None) -> dict[str, Any]:
    return {
        "index": item.index,
        "source": {
            "kind": item.source_kind,
            "path": item.source_path,
            "name": item.source_name,
            "original_filename": item.original_filename,
            "snapshot": snapshot_rel,
        },
        "kind": item.kind,
        "filename": item.filename,
        "destination": item.destination,
        "encoding": item.encoding,
        "bom": item.bom,
        "line_endings": item.line_endings,
        "original_sha256": item.original_sha256,
        "imported_sha256": item.imported_sha256,
        "target_sha256_before": item.target_sha256,
        "status": item.status,
        "notes": list(item.notes),
        "backup": None,
        "error": None,
        "written_at": None,
    }


def _summarize_manifest_items(items: list[dict[str, Any]]) -> dict[str, int]:
    summary: dict[str, int] = {}
    for item in items:
        status = item.get("status", "unknown")
        summary[status] = summary.get(status, 0) + 1
    summary["total"] = len(items)
    return summary


def _save_manifest(batch_dir: Path, manifest: dict[str, Any]) -> tuple[bool, str | None]:
    manifest["updated_at"] = now_iso()
    manifest["summary"] = _summarize_manifest_items(manifest["items"])
    try:
        atomic_write_json(batch_dir / "manifest.json", manifest)
        return True, None
    except OSError as exc:
        return False, str(exc)


def _baseline_failure_result(
    plan: dict[str, Any],
    manifest: dict[str, Any],
    batch_dir: Path,
    batch_id: str,
    stage: str,
    exc: Exception,
) -> dict[str, Any]:
    manifest["status"] = "failed"
    manifest["stage"] = stage
    manifest["error"] = str(exc)
    for item in manifest["items"]:
        if item["status"] in ("ready", "same", "exists"):
            item["status"] = "skipped-not-attempted"
    manifest_saved, manifest_error = _save_manifest(batch_dir, manifest)
    result = _blank_result(
        plan,
        "failed",
        f"基线保存失败（{stage}），未写入任何源码目标：{exc}",
    )
    result.update({
        "ok": False,
        "batch_id": batch_id,
        "batch_dir": str(batch_dir),
        "error": {"type": type(exc).__name__, "message": str(exc), "stage": stage},
        "tracking_limited": not manifest_saved,
        "summary": _summarize_manifest_items(manifest["items"]),
    })
    if not manifest_saved:
        result["manifest_error"] = manifest_error
    for item in result["items"]:
        if item["status"] in ("ready", "same", "exists"):
            item["status"] = "skipped-not-attempted"
            item["final_status"] = "skipped-not-attempted"
    return result


def apply_request(
    request: Any,
    conflict_policy: str = "skip",
    expected_plan: Any | None = None,
) -> dict[str, Any]:
    if conflict_policy not in ("skip", "backup-replace"):
        raise ValidationError("--conflict 仅支持 skip 或 backup-replace")
    normalized = normalize_request(request)
    prepared = _prepare_sources(normalized)
    plan = _make_plan(normalized, prepared)
    if expected_plan is not None:
        verify_expected_plan(plan, expected_plan)
    blocked = [item for item in prepared if item.status in ("pending", "invalid", "conflict")]
    if blocked:
        result = _blank_result(
            plan,
            "blocked",
            "存在待补充、无效或目标冲突的来源，整批未写入；请修正并重新预览",
        )
        result["ok"] = False
        result["blocked"] = [
            {"index": item.index, "status": item.status, "notes": list(item.notes)}
            for item in blocked
        ]
        return result
    _preflight_targets(plan, prepared)
    actionable = [item for item in prepared if item.status in ("ready", "same", "exists")]
    if not actionable:
        return _blank_result(plan, "no-write", "来源清单为空，未写入")

    root = normalized.root
    batch_id: str | None = None
    batch_dir: Path | None = None
    try:
        root.mkdir(parents=True, exist_ok=True)
        batches_dir = _prepare_metadata_dirs(root)
        batch_id = _new_batch_id(batches_dir)
        batch_dir = batches_dir / batch_id
        if not _is_within(os.path.realpath(batch_dir), os.path.realpath(root)):
            raise ValidationError("批次目录解析后位于项目目录之外，已拒绝写入")
        batch_dir.mkdir(parents=False, exist_ok=False)
    except ValidationError:
        raise
    except OSError as exc:
        result = _blank_result(plan, "failed", f"创建项目或批次目录失败，未写入源码：{exc}")
        result.update({
            "ok": False,
            "batch_id": batch_id,
            "batch_dir": str(batch_dir) if batch_dir else None,
            "error": {"type": type(exc).__name__, "message": str(exc), "stage": "batch-setup"},
            "tracking_limited": True,
            "manifest_error": "批次目录未建立，无法生成 manifest.json",
        })
        return result
    assert batch_id is not None and batch_dir is not None

    created_at = now_iso()
    manifest_items: list[dict[str, Any]] = [_manifest_item(item, None) for item in prepared]
    manifest: dict[str, Any] = {
        "version": 1,
        "tool": TOOL_NAME,
        "tool_version": TOOL_VERSION,
        "batch_id": batch_id,
        "created_at": created_at,
        "updated_at": created_at,
        "project": {"root": str(root), "name": normalized.project_name},
        "profile": normalized.profile,
        "routes": dict(normalized.routes),
        "cls_layout": normalized.cls_layout,
        "requirement": normalized.requirement.to_dict(),
        "requirement_snapshot": "requirement.json",
        "conflict_policy": conflict_policy,
        "status": "running",
        "stage": "initialized",
        "items": manifest_items,
        "summary": _summarize_manifest_items(manifest_items),
    }
    manifest_saved, manifest_error = _save_manifest(batch_dir, manifest)
    if not manifest_saved:
        return _baseline_failure_result(
            plan, manifest, batch_dir, batch_id, "manifest-init",
            OSError(manifest_error or "manifest.json 写入失败"),
        )

    originals_dir = batch_dir / "originals"
    try:
        originals_dir.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        return _baseline_failure_result(plan, manifest, batch_dir, batch_id, "originals-dir", exc)

    requirement_snapshot = dict(normalized.requirement.to_dict())
    requirement_snapshot["snapshot_at"] = created_at
    try:
        atomic_write_json(batch_dir / "requirement.json", requirement_snapshot)
    except OSError as exc:
        return _baseline_failure_result(
            plan, manifest, batch_dir, batch_id, "requirement-snapshot", exc
        )

    for item, manifest_item in zip(prepared, manifest_items):
        if not item.data:
            continue
        snapshot_rel = "originals/" + _safe_snapshot_name(item)
        try:
            atomic_write_bytes(batch_dir / Path(snapshot_rel), item.original_bytes)
        except OSError as exc:
            return _baseline_failure_result(plan, manifest, batch_dir, batch_id, "originals", exc)
        manifest_item["source"]["snapshot"] = snapshot_rel
    manifest["stage"] = "baseline-saved"
    manifest_saved, manifest_error = _save_manifest(batch_dir, manifest)
    if not manifest_saved:
        return _baseline_failure_result(
            plan, manifest, batch_dir, batch_id, "baseline-manifest-update",
            OSError(manifest_error or "manifest.json 更新失败"),
        )

    result_items = [
        dict(item, plan_status=item["status"], status=item["status"], final_status=item["status"])
        for item in plan["items"]
    ]
    result_by_index = {item["index"]: item for item in result_items}
    manifest_by_index = {item["index"]: item for item in manifest_items}
    written: list[dict[str, Any]] = []
    failure: Exception | None = None
    failed_index: int | None = None

    def set_final(result_item: dict[str, Any], status: str) -> None:
        result_item["status"] = status
        result_item["final_status"] = status

    for item in prepared:
        manifest_item = manifest_by_index[item.index]
        result_item = result_by_index[item.index]
        if item.status == "pending":
            continue
        if item.status == "invalid":
            continue
        if item.status == "conflict":
            continue
        if item.status == "same":
            manifest_item["status"] = "skipped-same"
            set_final(result_item, "skipped-same")
            continue
        if item.status == "exists" and conflict_policy == "skip":
            manifest_item["status"] = "skipped-existing"
            set_final(result_item, "skipped-existing")
            continue
        backup_rel = None
        had_backup = False
        try:
            target = _revalidate_target(root, item, create_parents=item.status != "exists")
            exists, current_sha = _read_target_state(target)
            if exists != item.target_exists or current_sha != item.target_sha256:
                raise PlanMismatchError(
                    f"目标 {item.destination} 在预览后发生变化，已拒绝写入，请重新预览"
                )
            if item.status == "exists":
                backup_rel = "backups/" + _safe_snapshot_name(item)
                backup_path = batch_dir / Path(backup_rel)
                atomic_write_bytes(backup_path, target.read_bytes())
                manifest_item["backup"] = backup_rel
                had_backup = True
            atomic_write_bytes(target, item.imported_bytes)
            final_status = "replaced" if item.status == "exists" else "written"
            manifest_item["status"] = final_status
            set_final(result_item, final_status)
            manifest_item["written_at"] = now_iso()
            written.append({
                "index": item.index,
                "target": target,
                "had_backup": had_backup,
                "backup_rel": backup_rel,
            })
        except Exception as exc:  # 文件系统错误或复核失败均进入回滚流程
            failure = exc
            failed_index = item.index
            manifest_item["status"] = "failed"
            set_final(result_item, "failed")
            manifest_item["error"] = str(exc)
            result_item["error"] = str(exc)
            break

    if failure is not None:
        rollback_errors: list[str] = []
        for record in reversed(written):
            item = prepared[record["index"]]
            manifest_item = manifest_by_index[record["index"]]
            result_item = result_by_index[record["index"]]
            try:
                if record["had_backup"] and record["backup_rel"]:
                    backup_path = batch_dir / Path(record["backup_rel"])
                    atomic_write_bytes(record["target"], backup_path.read_bytes())
                    manifest_item["status"] = "rolled-back"
                    set_final(result_item, "rolled-back")
                else:
                    if record["target"].exists():
                        if record["target"].is_dir():
                            raise OSError("回滚目标已变成目录")
                        record["target"].unlink()
                    manifest_item["status"] = "rolled-back"
                    set_final(result_item, "rolled-back")
            except OSError as exc:
                rollback_errors.append(f"#{record['index'] + 1} 回滚失败：{exc}")
                manifest_item["status"] = "rollback-failed"
                set_final(result_item, "rollback-failed")
                manifest_item["error"] = str(exc)
                result_item["error"] = str(exc)
        for item in prepared:
            manifest_item = manifest_by_index[item.index]
            result_item = result_by_index[item.index]
            if item.index == failed_index:
                continue
            if manifest_item["status"] in ("ready", "same", "exists"):
                manifest_item["status"] = "skipped-not-attempted"
                set_final(result_item, "skipped-not-attempted")
        if rollback_errors:
            batch_status = "rollback-incomplete"
        elif written:
            batch_status = "rolled-back"
        else:
            batch_status = "failed"
        manifest["status"] = batch_status
        manifest["stage"] = "rollback" if written else "source-write"
        manifest_saved, manifest_error = _save_manifest(batch_dir, manifest)
        result = _blank_result(plan, batch_status, "导入失败，已尽力回滚本批写入内容")
        result.update({
            "ok": False,
            "batch_id": batch_id,
            "batch_dir": str(batch_dir),
            "items": result_items,
            "summary": _summarize_manifest_items(manifest_items),
            "error": {"type": type(failure).__name__, "message": str(failure)},
            "rollback_errors": rollback_errors,
            "tracking_limited": not manifest_saved,
        })
        if not manifest_saved:
            result["manifest_error"] = manifest_error
        return result

    manifest["status"] = "completed"
    manifest["stage"] = "done"
    manifest_saved, manifest_error = _save_manifest(batch_dir, manifest)
    result = _blank_result(plan, "completed" if manifest_saved else "tracking-failed")
    result.update({
        "batch_id": batch_id,
        "batch_dir": str(batch_dir),
        "items": result_items,
        "summary": _summarize_manifest_items(manifest_items),
        "plan_summary": dict(plan["summary"]),
    })
    if not manifest_saved:
        result["ok"] = False
        result["tracking_limited"] = True
        result["manifest_error"] = manifest_error
        result["message"] = "源码目标已写入，但最终批次清单保存失败；请检查批次目录与文件状态。"
        result["error"] = {
            "type": "OSError",
            "message": manifest_error or "manifest.json 写入失败",
            "stage": "final-manifest",
        }
    return result


def read_json_file(path: Path) -> Any:
    try:
        raw = path.read_bytes()
    except OSError as exc:
        raise ValidationError(f"无法读取 {path}：{exc}") from exc
    text = None
    for bom, encoding in (
        (codecs.BOM_UTF8, "utf-8-sig"),
        (codecs.BOM_UTF32_LE, "utf-32"),
        (codecs.BOM_UTF32_BE, "utf-32"),
        (codecs.BOM_UTF16_LE, "utf-16"),
        (codecs.BOM_UTF16_BE, "utf-16"),
    ):
        if raw.startswith(bom):
            try:
                text = raw.decode(encoding)
            except UnicodeDecodeError:
                text = None
            break
    if text is None:
        for encoding in ("utf-8-sig", "gb18030"):
            try:
                text = raw.decode(encoding)
                break
            except UnicodeDecodeError:
                continue
    if text is None:
        raise ValidationError(f"{path} 不是可识别的 JSON 文本编码")
    try:
        return json.loads(text)
    except json.JSONDecodeError as exc:
        raise ValidationError(f"{path} 不是有效的 JSON：{exc}") from exc


def history(root: Any) -> dict[str, Any]:
    root_path = normalize_root(root)
    batches: list[dict[str, Any]] = []
    warnings: list[str] = []
    batches_dir = root_path / META_DIR / BATCHES_DIR
    if batches_dir.is_dir():
        for manifest_path in sorted(batches_dir.glob("*/manifest.json")):
            try:
                manifest = read_json_file(manifest_path)
            except ValidationError as exc:
                warnings.append(str(exc))
                continue
            if not isinstance(manifest, dict):
                warnings.append(f"批次清单不是对象：{manifest_path}")
                continue
            batches.append({
                "batch_id": manifest.get("batch_id") or manifest_path.parent.name,
                "created_at": manifest.get("created_at"),
                "status": manifest.get("status"),
                "project": manifest.get("project"),
                "profile": manifest.get("profile"),
                "requirement": manifest.get("requirement"),
                "summary": manifest.get("summary"),
                "items": manifest.get("items", []),
                "manifest_path": str(manifest_path),
            })
    batches.sort(key=lambda entry: str(entry.get("created_at") or ""))
    return {
        "ok": True,
        "command": "history",
        "tool": TOOL_NAME,
        "version": TOOL_VERSION,
        "root": str(root_path),
        "root_exists": root_path.exists(),
        "batches": batches,
        "warnings": warnings,
    }


class JsonArgumentParser(argparse.ArgumentParser):
    def error(self, message: str) -> None:  # type: ignore[override]
        raise ValidationError(message)


def build_parser() -> argparse.ArgumentParser:
    parser = JsonArgumentParser(prog="source_intake.py", add_help=True)
    subparsers = parser.add_subparsers(dest="command", required=True)
    plan_parser = subparsers.add_parser("plan", help="预览导入计划，不创建项目目录")
    plan_parser.add_argument("--manifest", required=True, help="请求 JSON 文件")
    apply_parser = subparsers.add_parser("apply", help="执行导入")
    apply_parser.add_argument("--manifest", required=True, help="请求 JSON 文件")
    apply_parser.add_argument(
        "--conflict", choices=("skip", "backup-replace"), default="skip",
        help="同名冲突处理：默认跳过或备份后替换",
    )
    apply_parser.add_argument("--expected-plan", help="预览时保存的 plan JSON")
    history_parser = subparsers.add_parser("history", help="列出项目批次")
    history_parser.add_argument("--root", required=True, help="项目目录")
    return parser


def _configure_stdout() -> None:
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except (AttributeError, ValueError):
        pass


def _print_json(value: Any) -> None:
    print(json.dumps(value, ensure_ascii=False, indent=2))


def main(argv: list[str] | None = None) -> int:
    _configure_stdout()
    command: str | None = None
    args: argparse.Namespace | None = None
    try:
        parser = build_parser()
        args = parser.parse_args(argv)
        command = args.command
        if command == "plan":
            request = read_json_file(Path(args.manifest))
            _print_json(build_plan(request))
            return 0
        if command == "apply":
            request = read_json_file(Path(args.manifest))
            expected = read_json_file(Path(args.expected_plan)) if args.expected_plan else None
            result = apply_request(request, args.conflict, expected)
            _print_json(result)
            return 0 if result.get("ok") else 1
        if command == "history":
            _print_json(history(args.root))
            return 0
        raise ValidationError(f"未知命令：{command}")
    except SourceIntakeError as exc:
        print(f"source-intake: {exc}", file=sys.stderr)
        _print_json({
            "ok": False,
            "command": command,
            "error": {"type": type(exc).__name__, "message": str(exc)},
        })
        return 2
    except Exception as exc:  # 防止诊断信息混入 stdout 的 JSON
        import traceback
        traceback.print_exc(file=sys.stderr)
        _print_json({
            "ok": False,
            "command": command,
            "error": {"type": type(exc).__name__, "message": str(exc)},
        })
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
