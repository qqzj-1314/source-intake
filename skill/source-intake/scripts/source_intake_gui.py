#!/usr/bin/env python3
"""源码需求导入助手：中文 Tkinter 界面（仅用标准库）。"""

from __future__ import annotations

import ctypes
import hashlib
import json
import os
import sys
import tkinter as tk
from dataclasses import dataclass, field
from pathlib import Path
from tkinter import filedialog, messagebox, ttk
from typing import Any, Callable

import source_intake as core

APP_TITLE = "源码需求导入助手"
CF_HDROP = 15
MAX_RECENT = 8

STATUS_TEXT = {
    "ready": "待导入",
    "same": "内容一致（跳过）",
    "exists": "同名冲突（默认跳过）",
    "pending": "待补充",
    "invalid": "无法导入",
    "conflict": "目标重复冲突",
    "written": "已写入",
    "replaced": "已备份替换",
    "skipped-same": "内容一致（已跳过）",
    "skipped-existing": "同名冲突（已跳过）",
    "failed": "失败",
    "rolled-back": "已回滚",
    "rollback-failed": "回滚失败",
    "skipped-not-attempted": "批次失败未处理",
    "no-write": "未写入",
    "": "待预览",
}

KIND_TEXT = {"csp": "CSP", "js": "JS", "css": "CSS", "cls": "CLS", "": ""}

ClipboardReader = Callable[[], tuple[str, Any, str | None]]


def default_settings_path() -> Path:
    base = os.environ.get("LOCALAPPDATA")
    if not base:
        base = str(Path.home() / "AppData" / "Local")
    return Path(base) / "SourceIntake" / "settings.json"


def canonical_root_key(root: str) -> str:
    text = os.path.expanduser(root.strip())
    return os.path.normcase(os.path.realpath(os.path.abspath(text)))


def read_clipboard_files() -> list[str] | None:
    """用 Win32 API 读取 CF_HDROP；无该格式时返回 None。"""
    if os.name != "nt":
        return None
    user32 = ctypes.WinDLL("user32", use_last_error=True)
    shell32 = ctypes.WinDLL("shell32", use_last_error=True)
    user32.IsClipboardFormatAvailable.argtypes = [ctypes.c_uint]
    user32.IsClipboardFormatAvailable.restype = ctypes.c_bool
    user32.OpenClipboard.argtypes = [ctypes.c_void_p]
    user32.OpenClipboard.restype = ctypes.c_bool
    user32.GetClipboardData.argtypes = [ctypes.c_uint]
    user32.GetClipboardData.restype = ctypes.c_void_p
    user32.CloseClipboard.argtypes = []
    user32.CloseClipboard.restype = ctypes.c_bool
    shell32.DragQueryFileW.argtypes = [ctypes.c_void_p, ctypes.c_uint, ctypes.c_wchar_p, ctypes.c_uint]
    shell32.DragQueryFileW.restype = ctypes.c_uint
    if not user32.IsClipboardFormatAvailable(CF_HDROP):
        return None
    if not user32.OpenClipboard(None):
        raise OSError(ctypes.get_last_error(), "无法打开剪贴板")
    try:
        handle = user32.GetClipboardData(CF_HDROP)
        if not handle:
            raise OSError(ctypes.get_last_error(), "无法读取剪贴板文件列表")
        count = shell32.DragQueryFileW(handle, 0xFFFFFFFF, None, 0)
        paths: list[str] = []
        for index in range(count):
            length = shell32.DragQueryFileW(handle, index, None, 0)
            if not length:
                continue
            buffer = ctypes.create_unicode_buffer(length + 1)
            if shell32.DragQueryFileW(handle, index, buffer, length + 1):
                paths.append(buffer.value)
        return paths
    finally:
        user32.CloseClipboard()


@dataclass
class SourceEntry:
    uid: str
    source_kind: str
    path: str | None = None
    text: str | None = None
    original_filename: str = ""
    filename_override: str = ""
    destination_override: str = ""
    preview: dict[str, Any] | None = None

    @property
    def effective_filename(self) -> str:
        if self.preview and self.preview.get("filename"):
            return str(self.preview["filename"])
        return self.filename_override

    @property
    def effective_destination(self) -> str:
        if self.preview and self.preview.get("destination"):
            return str(self.preview["destination"])
        return self.destination_override

    @property
    def effective_kind(self) -> str:
        if self.preview and self.preview.get("kind"):
            return str(self.preview["kind"])
        name = self.effective_filename or self.effective_destination
        return core.filename_extension_kind(name) or ""

    @property
    def display_source(self) -> str:
        return self.path or "粘贴内容"


class SettingsStore:
    def __init__(self, path: Path):
        self.path = path
        self.data: dict[str, Any] = {"version": 1, "recent_roots": [], "projects": {}}
        self.warning = ""
        self.load()

    def load(self) -> None:
        try:
            if not self.path.exists():
                return
            raw = json.loads(self.path.read_bytes().decode("utf-8-sig"))
            if not isinstance(raw, dict):
                raise ValueError("设置文件不是对象")
            recent = raw.get("recent_roots", [])
            projects = raw.get("projects", {})
            if not isinstance(recent, list) or not isinstance(projects, dict):
                raise ValueError("设置文件结构无效")
            self.data = {
                "version": 1,
                "recent_roots": [str(item) for item in recent if isinstance(item, str)][:MAX_RECENT],
                "projects": {str(key): value for key, value in projects.items() if isinstance(value, dict)},
            }
        except (OSError, ValueError, json.JSONDecodeError) as exc:
            self.warning = f"读取设置失败，将使用默认设置：{exc}"

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        core.atomic_write_json(self.path, self.data)

    def remember(self, root_text: str, project_name: str, profile: str, routes: dict[str, str]) -> None:
        if not root_text.strip():
            return
        key = canonical_root_key(root_text)
        try:
            display_root = str(Path(os.path.abspath(os.path.expanduser(root_text.strip()))))
        except OSError:
            display_root = root_text.strip()
        recent = [item for item in self.data["recent_roots"] if os.path.normcase(item) != os.path.normcase(display_root)]
        recent.insert(0, display_root)
        self.data["recent_roots"] = recent[:MAX_RECENT]
        self.data["projects"][key] = {
            "root": display_root,
            "project_name": project_name,
            "profile": profile,
            "routes": dict(routes),
        }

    def project_for(self, root_text: str) -> dict[str, Any] | None:
        if not root_text.strip():
            return None
        value = self.data["projects"].get(canonical_root_key(root_text))
        return value if isinstance(value, dict) else None


class SourceIntakeApp:
    def __init__(
        self,
        master: tk.Tk,
        *,
        settings_path: Path | str | None = None,
        clipboard_reader: ClipboardReader | None = None,
    ):
        self.master = master
        self.master.title(APP_TITLE)
        self.master.geometry("1220x800")
        self.master.minsize(980, 680)
        self.settings = SettingsStore(Path(settings_path) if settings_path else default_settings_path())
        self.clipboard_reader = clipboard_reader or self._read_clipboard_payload
        self.source_entries: list[SourceEntry] = []
        self._entry_seq = 0
        self._preview_state: dict[str, Any] | None = None
        self._loading = False
        self._build_ui()
        self._refresh_recent_values()
        if self.settings.warning:
            self.log(self.settings.warning)
        self.master.protocol("WM_DELETE_WINDOW", self.on_close)

    # ---------- UI ----------

    def _build_ui(self) -> None:
        style = ttk.Style(self.master)
        try:
            if "vista" in style.theme_names():
                style.theme_use("vista")
        except tk.TclError:
            pass
        style.configure("Treeview", rowheight=25)
        style.configure("Accent.TButton", font=("Microsoft YaHei UI", 9, "bold"))

        container = ttk.Frame(self.master, padding=10)
        container.pack(fill="both", expand=True)
        container.columnconfigure(0, weight=1)
        container.rowconfigure(4, weight=1)

        header = ttk.Frame(container)
        header.grid(row=0, column=0, sticky="ew", pady=(0, 8))
        ttk.Label(header, text=APP_TITLE, font=("Microsoft YaHei UI", 14, "bold")).pack(side="left")
        ttk.Label(
            header,
            text="  从剪贴板/文件/代码块建立源码层级，并保存原始快照与需求关联",
            foreground="#555555",
        ).pack(side="left", padx=(10, 0))

        form = ttk.LabelFrame(container, text="项目与需求", padding=8)
        form.grid(row=1, column=0, sticky="ew")
        form.columnconfigure(1, weight=1)
        form.columnconfigure(4, weight=1)

        self.root_dir_var = tk.StringVar()
        self.project_name_var = tk.StringVar()
        self.req_id_var = tk.StringVar()
        self.req_title_var = tk.StringVar()
        self.req_url_var = tk.StringVar()
        self.recent_var = tk.StringVar()

        ttk.Label(form, text="项目目录").grid(row=0, column=0, sticky="w", padx=(0, 6), pady=3)
        root_row = ttk.Frame(form)
        root_row.grid(row=0, column=1, columnspan=4, sticky="ew", pady=3)
        root_row.columnconfigure(0, weight=1)
        self.root_entry = ttk.Entry(root_row, textvariable=self.root_dir_var)
        self.root_entry.grid(row=0, column=0, sticky="ew")
        ttk.Button(root_row, text="选择目录…", command=self.on_browse_directory).grid(row=0, column=1, padx=(6, 12))
        ttk.Label(root_row, text="最近项目").grid(row=0, column=2, sticky="e", padx=(0, 4))
        self.recent_combo = ttk.Combobox(root_row, textvariable=self.recent_var, state="readonly", width=34)
        self.recent_combo.grid(row=0, column=3, sticky="ew")
        self.recent_combo.bind("<<ComboboxSelected>>", self.on_recent_selected)

        ttk.Label(form, text="项目名称").grid(row=1, column=0, sticky="w", padx=(0, 6), pady=3)
        ttk.Entry(form, textvariable=self.project_name_var).grid(row=1, column=1, sticky="ew", pady=3)
        ttk.Label(form, text="需求编号").grid(row=1, column=2, sticky="e", padx=(12, 6), pady=3)
        ttk.Entry(form, textvariable=self.req_id_var).grid(row=1, column=3, sticky="ew", pady=3)
        ttk.Label(form, text="需求标题").grid(row=1, column=4, sticky="e", padx=(12, 6), pady=3)
        ttk.Entry(form, textvariable=self.req_title_var).grid(row=1, column=5, sticky="ew", pady=3)
        ttk.Label(form, text="需求链接").grid(row=2, column=0, sticky="w", padx=(0, 6), pady=3)
        ttk.Entry(form, textvariable=self.req_url_var).grid(row=2, column=1, columnspan=2, sticky="ew", pady=3)
        ttk.Label(form, text="需求描述").grid(row=2, column=3, sticky="e", padx=(12, 6), pady=3)
        self.req_desc = tk.Text(form, height=3, wrap="word", font=("Microsoft YaHei UI", 9))
        self.req_desc.grid(row=2, column=4, columnspan=2, sticky="ew", pady=3)
        ttk.Label(
            form,
            text="需求信息均可留空；BOS 查询后续接入。",
            foreground="#777777",
        ).grid(row=3, column=1, columnspan=5, sticky="w", pady=(2, 0))

        options = ttk.LabelFrame(container, text="目录规则", padding=8)
        options.grid(row=2, column=0, sticky="ew", pady=8)
        for column in (1, 3, 5, 7):
            options.columnconfigure(column, weight=1)

        self.profile_var = tk.StringVar(value="hisui")
        self.conflict_var = tk.StringVar(value="skip")
        self.route_vars = {kind: tk.StringVar() for kind in core.KINDS}

        ttk.Label(options, text="布局").grid(row=0, column=0, sticky="w", padx=(0, 6))
        self.profile_combo = ttk.Combobox(
            options, textvariable=self.profile_var, state="readonly", width=10, values=("hisui", "flat")
        )
        self.profile_combo.grid(row=0, column=1, sticky="w")
        self.profile_combo.bind("<<ComboboxSelected>>", self.on_profile_changed)
        ttk.Label(options, text="HISUI 常用布局；flat 根目录平铺").grid(
            row=0, column=2, columnspan=3, sticky="w", padx=(8, 0)
        )
        ttk.Label(options, text="同名冲突").grid(row=0, column=5, sticky="e", padx=(8, 4))
        ttk.Radiobutton(options, text="默认跳过", value="skip", variable=self.conflict_var).grid(
            row=0, column=6, sticky="w"
        )
        ttk.Radiobutton(options, text="备份后替换", value="backup-replace", variable=self.conflict_var).grid(
            row=0, column=7, sticky="w"
        )

        route_labels = {"csp": "CSP 目录", "js": "JS 目录", "css": "CSS 目录", "cls": "CLS 根目录"}
        for idx, kind in enumerate(core.KINDS):
            ttk.Label(options, text=route_labels[kind]).grid(
                row=1 + idx // 2, column=(idx % 2) * 2, sticky="e", padx=(0 if idx % 2 == 0 else 20, 6), pady=(6, 0)
            )
            entry = ttk.Entry(options, textvariable=self.route_vars[kind])
            entry.grid(row=1 + idx // 2, column=(idx % 2) * 2 + 1, sticky="ew", pady=(6, 0))
            entry.bind("<KeyRelease>", self.on_route_edited)
        ttk.Label(
            options,
            text="CLS 按类名包层级建目录；flat 布局也默认保留包层级。",
            foreground="#777777",
        ).grid(row=3, column=0, columnspan=8, sticky="w", pady=(8, 0))
        self._apply_profile_routes("hisui")

        actions = ttk.Frame(container)
        actions.grid(row=3, column=0, sticky="new")
        actions.columnconfigure(2, weight=1)
        collect_frame = ttk.Frame(actions)
        collect_frame.grid(row=0, column=0, sticky="w")
        self.collect_button = ttk.Button(collect_frame, text="收集剪贴板", command=self.on_collect_clipboard)
        self.collect_button.pack(side="left")
        self.files_button = ttk.Button(collect_frame, text="选择文件…", command=self.on_choose_files)
        self.files_button.pack(side="left", padx=(6, 0))
        self.paste_button = ttk.Button(collect_frame, text="粘贴代码…", command=self.on_paste_code)
        self.paste_button.pack(side="left", padx=(6, 0))
        ttk.Button(collect_frame, text="修改选中项…", command=self.on_edit_selected).pack(side="left", padx=(18, 0))
        ttk.Button(collect_frame, text="删除选中项", command=self.on_delete_selected).pack(side="left", padx=(6, 0))
        ttk.Button(collect_frame, text="清空清单", command=self.on_clear_sources).pack(side="left", padx=(6, 0))

        right_actions = ttk.Frame(actions)
        right_actions.grid(row=0, column=1, sticky="e")
        self.preview_button = ttk.Button(right_actions, text="预览", command=self.on_preview)
        self.preview_button.pack(side="left")
        self.import_button = ttk.Button(
            right_actions, text="导入", style="Accent.TButton", command=self.on_import
        )
        self.import_button.pack(side="left", padx=(6, 0))
        ttk.Button(right_actions, text="打开目标目录", command=self.on_open_target).pack(side="left", padx=(6, 0))

        table_frame = ttk.Frame(container)
        table_frame.grid(row=4, column=0, sticky="nsew", pady=(10, 0))
        table_frame.columnconfigure(0, weight=1)
        table_frame.rowconfigure(0, weight=1)
        columns = ("source", "filename", "destination", "kind", "status")
        self.tree = ttk.Treeview(table_frame, columns=columns, show="headings", selectmode="extended")
        headings = {
            "source": ("来源", 270),
            "filename": ("识别文件名", 180),
            "destination": ("目标相对路径", 380),
            "kind": ("类型", 70),
            "status": ("状态", 170),
        }
        for key, (label, width) in headings.items():
            self.tree.heading(key, text=label)
            self.tree.column(key, width=width, minwidth=60, anchor="w", stretch=(key in ("source", "destination")))
        self.tree.tag_configure("ready", foreground="#1a6d2f")
        self.tree.tag_configure("written", foreground="#1a6d2f")
        self.tree.tag_configure("replaced", foreground="#1a6d2f")
        self.tree.tag_configure("pending", foreground="#9a5b00")
        self.tree.tag_configure("invalid", foreground="#b00020")
        self.tree.tag_configure("conflict", foreground="#b00020")
        self.tree.tag_configure("failed", foreground="#b00020")
        self.tree.tag_configure("rollback-failed", foreground="#b00020")
        self.tree.tag_configure("same", foreground="#666666")
        self.tree.tag_configure("skipped-same", foreground="#666666")
        self.tree.tag_configure("exists", foreground="#9a5b00")
        self.tree.tag_configure("skipped-existing", foreground="#9a5b00")
        self.tree.grid(row=0, column=0, sticky="nsew")
        y_scroll = ttk.Scrollbar(table_frame, orient="vertical", command=self.tree.yview)
        y_scroll.grid(row=0, column=1, sticky="ns")
        x_scroll = ttk.Scrollbar(table_frame, orient="horizontal", command=self.tree.xview)
        x_scroll.grid(row=1, column=0, sticky="ew")
        self.tree.configure(yscrollcommand=y_scroll.set, xscrollcommand=x_scroll.set)
        self.tree.bind("<Double-1>", lambda event: self.on_edit_selected())

        log_frame = ttk.LabelFrame(container, text="预览 / 导入日志", padding=6)
        log_frame.grid(row=5, column=0, sticky="nsew", pady=(8, 0))
        log_frame.columnconfigure(0, weight=1)
        log_frame.rowconfigure(0, weight=1)
        self.log_text = tk.Text(log_frame, height=6, wrap="word", state="disabled", font=("Microsoft YaHei UI", 9))
        self.log_text.grid(row=0, column=0, sticky="nsew")
        log_scroll = ttk.Scrollbar(log_frame, orient="vertical", command=self.log_text.yview)
        log_scroll.grid(row=0, column=1, sticky="ns")
        self.log_text.configure(yscrollcommand=log_scroll.set)

        self.status_var = tk.StringVar(value="就绪：收集来源后先预览，再导入。")
        status = ttk.Label(container, textvariable=self.status_var, relief="sunken", anchor="w", padding=(6, 3))
        status.grid(row=6, column=0, sticky="ew", pady=(8, 0))

        self._fill_request_defaults()
        self.refresh_tree()

    def _fill_request_defaults(self) -> None:
        self.req_desc.delete("1.0", "end")

    # ---------- 设置与目录 ----------

    def _refresh_recent_values(self) -> None:
        values = self.settings.data.get("recent_roots", [])
        self.recent_combo.configure(values=list(values))
        if not values:
            self.recent_var.set("")

    def _apply_profile_routes(self, profile: str) -> None:
        defaults = core.DEFAULT_ROUTES.get(profile, core.DEFAULT_ROUTES["hisui"])
        for kind in core.KINDS:
            self.route_vars[kind].set(defaults[kind])
        self.profile_var.set(profile)

    def _current_routes(self) -> dict[str, str]:
        return {kind: self.route_vars[kind].get().strip() for kind in core.KINDS}

    def _load_saved_project(self, root_text: str) -> None:
        project = self.settings.project_for(root_text)
        if not project:
            return
        self._loading = True
        try:
            if project.get("project_name"):
                self.project_name_var.set(str(project["project_name"]))
            profile = str(project.get("profile") or self.profile_var.get())
            routes = project.get("routes")
            if isinstance(routes, dict):
                self._apply_profile_routes(profile)
                for kind in core.KINDS:
                    if isinstance(routes.get(kind), str):
                        self.route_vars[kind].set(routes[kind])
            elif profile in core.DEFAULT_ROUTES:
                self._apply_profile_routes(profile)
        finally:
            self._loading = False
        self.log(f"已载入项目目录规则：{root_text}")

    def _remember_project(self) -> None:
        root_text = self.root_dir_var.get().strip()
        if not root_text:
            return
        self.settings.remember(
            root_text,
            self.project_name_var.get().strip(),
            self.profile_var.get(),
            self._current_routes(),
        )
        try:
            self.settings.save()
            self._refresh_recent_values()
        except OSError as exc:
            self.log(f"保存设置失败：{exc}")

    def on_browse_directory(self) -> None:
        initial = self.root_dir_var.get().strip() or os.path.expanduser("~")
        selected = filedialog.askdirectory(title="选择项目目录（也可直接输入不存在的目录）", initialdir=initial)
        if not selected:
            return
        self.set_root_directory(selected, load_saved=True)
        self._remember_project()

    def set_root_directory(self, path: str, *, load_saved: bool = True) -> None:
        self.root_dir_var.set(path)
        if not self.project_name_var.get().strip():
            name = Path(os.path.abspath(path)).name
            if name:
                self.project_name_var.set(name)
        if load_saved:
            self._load_saved_project(path)
        self._invalidate_preview("项目目录已变化，请重新预览")

    def on_recent_selected(self, _event: Any = None) -> None:
        value = self.recent_var.get().strip()
        if value:
            self.set_root_directory(value, load_saved=True)

    def on_profile_changed(self, _event: Any = None) -> None:
        if self._loading:
            return
        self._apply_profile_routes(self.profile_var.get())
        self._invalidate_preview("布局已切换，请重新预览")

    def on_route_edited(self, _event: Any = None) -> None:
        if self._loading:
            return
        self._invalidate_preview("目录规则已修改，请重新预览")

    # ---------- 来源 ----------

    def _new_entry(self, source_kind: str, **kwargs: Any) -> SourceEntry:
        self._entry_seq += 1
        return SourceEntry(uid=f"src-{self._entry_seq}", source_kind=source_kind, **kwargs)

    def add_paths(self, paths: list[str]) -> None:
        added = 0
        for path in paths:
            if not isinstance(path, str) or not path.strip():
                continue
            self.source_entries.append(self._new_entry("path", path=path.strip()))
            added += 1
        if added:
            self._invalidate_preview("来源清单已变化，请重新预览")
            self.refresh_tree()
            self.status_var.set(f"已添加 {added} 个文件来源，共 {len(self.source_entries)} 项。")
            self.log(f"添加文件来源：{added} 个")

    def add_text(self, text: str, *, filename: str = "", destination: str = "") -> bool:
        if not isinstance(text, str) or not text.strip():
            self.log("未添加空的粘贴内容")
            self.status_var.set("粘贴内容为空，未添加。")
            return False
        self.source_entries.append(
            self._new_entry(
                "text",
                text=text,
                original_filename=filename.strip(),
                filename_override=filename.strip(),
                destination_override=destination.strip(),
            )
        )
        self._invalidate_preview("来源清单已变化，请重新预览")
        self.refresh_tree()
        self.status_var.set(f"已添加粘贴内容，共 {len(self.source_entries)} 项。")
        self.log(f"添加粘贴内容：{filename.strip() or '未命名'}（{len(text)} 字符）")
        return True

    def _source_label(self, entry: SourceEntry) -> str:
        return entry.path or entry.original_filename or "粘贴内容"

    def refresh_tree(self) -> None:
        selected = set(self.tree.selection())
        for iid in self.tree.get_children():
            self.tree.delete(iid)
        for entry in self.source_entries:
            preview = entry.preview or {}
            filename = entry.effective_filename or "—"
            destination = entry.effective_destination or "—"
            kind = KIND_TEXT.get(entry.effective_kind, entry.effective_kind or "—")
            status = preview.get("status", "")
            status_label = STATUS_TEXT.get(status, status or "待预览")
            self.tree.insert(
                "", "end", iid=entry.uid,
                values=(self._source_label(entry), filename, destination, kind, status_label),
                tags=(status,) if status else (),
            )
            if entry.uid in selected:
                self.tree.selection_add(entry.uid)

    def _selected_entries(self) -> list[SourceEntry]:
        selection = set(self.tree.selection())
        return [entry for entry in self.source_entries if entry.uid in selection]

    def on_choose_files(self) -> None:
        initial = self.root_dir_var.get().strip() or os.path.expanduser("~")
        paths = filedialog.askopenfilenames(title="选择源码或 .txt 附件", initialdir=initial)
        if paths:
            self.add_paths(list(paths))

    def on_paste_code(self) -> None:
        self._open_paste_dialog("")

    def _open_paste_dialog(self, initial_text: str) -> None:
        dialog = tk.Toplevel(self.master)
        dialog.title("粘贴代码")
        dialog.transient(self.master)
        dialog.grab_set()
        dialog.geometry("760x520")
        frame = ttk.Frame(dialog, padding=10)
        frame.pack(fill="both", expand=True)
        frame.columnconfigure(1, weight=1)
        frame.rowconfigure(2, weight=1)
        ttk.Label(frame, text="文件名（可选）").grid(row=0, column=0, sticky="w", padx=(0, 6), pady=3)
        filename_var = tk.StringVar()
        ttk.Entry(frame, textvariable=filename_var).grid(row=0, column=1, sticky="ew", pady=3)
        ttk.Label(
            frame,
            text="一次收集一段源码；可保留最外层代码围栏。缺少文件名时可先添加，稍后在清单中补充。",
            foreground="#666666",
        ).grid(row=1, column=0, columnspan=2, sticky="w", pady=(0, 6))
        text_widget = tk.Text(frame, wrap="none", font=("Consolas", 10), undo=True)
        text_widget.grid(row=2, column=0, columnspan=2, sticky="nsew")
        scroll = ttk.Scrollbar(frame, orient="vertical", command=text_widget.yview)
        scroll.grid(row=2, column=2, sticky="ns")
        text_widget.configure(yscrollcommand=scroll.set)
        if initial_text:
            text_widget.insert("1.0", initial_text)

        buttons = ttk.Frame(frame)
        buttons.grid(row=3, column=0, columnspan=3, sticky="e", pady=(8, 0))

        def confirm() -> None:
            text = text_widget.get("1.0", "end-1c")
            filename = filename_var.get().strip()
            dialog.grab_release()
            dialog.destroy()
            if self.add_text(text, filename=filename):
                self.log("提示：如内容含多个代码围栏，核心会标记待补充，请一次收集一段。")

        def cancel() -> None:
            dialog.grab_release()
            dialog.destroy()

        ttk.Button(buttons, text="取消", command=cancel).pack(side="right")
        ttk.Button(buttons, text="确定", command=confirm).pack(side="right", padx=(0, 6))
        text_widget.focus_set()

    def on_edit_selected(self) -> None:
        entries = self._selected_entries()
        if not entries:
            messagebox.showinfo("修改来源", "请先在清单中选择一项。")
            return
        entry = entries[0]
        self._open_edit_dialog(entry)

    def _open_edit_dialog(self, entry: SourceEntry) -> None:
        dialog = tk.Toplevel(self.master)
        dialog.title("修改来源")
        dialog.transient(self.master)
        dialog.grab_set()
        dialog.resizable(False, False)
        frame = ttk.Frame(dialog, padding=12)
        frame.pack(fill="both", expand=True)
        ttk.Label(frame, text="来源").grid(row=0, column=0, sticky="w")
        ttk.Label(frame, text=self._source_label(entry), wraplength=520).grid(row=0, column=1, sticky="w")
        ttk.Label(frame, text="识别类型").grid(row=1, column=0, sticky="w", pady=(6, 0))
        ttk.Label(frame, text=KIND_TEXT.get(entry.effective_kind, entry.effective_kind or "未识别")).grid(
            row=1, column=1, sticky="w", pady=(6, 0)
        )
        ttk.Label(frame, text="文件名").grid(row=2, column=0, sticky="w", pady=(8, 0))
        filename_var = tk.StringVar(value=entry.effective_filename)
        filename_entry = ttk.Entry(frame, textvariable=filename_var, width=58)
        filename_entry.grid(row=2, column=1, sticky="ew", pady=(8, 0))
        ttk.Label(frame, text="目标相对路径").grid(row=3, column=0, sticky="w", pady=(8, 0))
        destination_var = tk.StringVar(value=entry.effective_destination)
        ttk.Entry(frame, textvariable=destination_var, width=58).grid(row=3, column=1, sticky="ew", pady=(8, 0))
        ttk.Label(
            frame,
            text="文件名可只写名称；目标路径留空时按目录规则自动生成。两者都以扩展名决定类型。",
            foreground="#666666",
            wraplength=560,
        ).grid(row=4, column=0, columnspan=2, sticky="w", pady=(8, 0))
        buttons = ttk.Frame(frame)
        buttons.grid(row=5, column=0, columnspan=2, sticky="e", pady=(12, 0))

        def confirm() -> None:
            filename = filename_var.get().strip()
            destination = destination_var.get().strip()
            dialog.grab_release()
            dialog.destroy()
            self._apply_entry_edit(entry, filename, destination)

        def cancel() -> None:
            dialog.grab_release()
            dialog.destroy()

        ttk.Button(buttons, text="取消", command=cancel).pack(side="right")
        ttk.Button(buttons, text="确定", command=confirm).pack(side="right", padx=(0, 6))
        filename_entry.focus_set()

    def _apply_entry_edit(self, entry: SourceEntry, filename: str, destination: str) -> None:
        entry.filename_override = filename.strip()
        entry.destination_override = destination.strip()
        self._invalidate_preview("来源信息已修改，请重新预览")
        self.refresh_tree()
        self.status_var.set("来源信息已更新，请重新预览。")

    def on_delete_selected(self) -> None:
        entries = self._selected_entries()
        if not entries:
            messagebox.showinfo("删除来源", "请先在清单中选择要删除的项。")
            return
        selected_ids = {entry.uid for entry in entries}
        self.source_entries = [entry for entry in self.source_entries if entry.uid not in selected_ids]
        self._invalidate_preview("来源清单已变化，请重新预览")
        self.refresh_tree()
        self.status_var.set(f"已删除 {len(entries)} 项来源。")
        self.log(f"删除来源：{len(entries)} 项")

    def on_clear_sources(self) -> None:
        if not self.source_entries:
            return
        if not messagebox.askyesno("清空清单", "确定清空全部待导入来源吗？不会删除已导入文件。"):
            return
        self.source_entries = []
        self._invalidate_preview("来源清单已变化，请重新预览")
        self.refresh_tree()
        self.status_var.set("清单已清空。")
        self.log("已清空待导入清单")

    # ---------- 剪贴板 ----------

    def _read_clipboard_payload(self) -> tuple[str, Any, str | None]:
        note = None
        try:
            files = read_clipboard_files()
            if files:
                return "files", files, None
        except OSError as exc:
            note = f"读取剪贴板文件列表失败：{exc}"
        try:
            text = self.master.clipboard_get()
        except tk.TclError:
            text = ""
        if text:
            return "text", text, note
        return "empty", None, note

    def on_collect_clipboard(self) -> None:
        try:
            kind, payload, note = self.clipboard_reader()
        except Exception as exc:
            messagebox.showerror("收集剪贴板", f"读取剪贴板失败：{exc}")
            return
        if note:
            self.log(note)
        if kind == "files" and payload:
            self.add_paths([str(item) for item in payload])
            self.status_var.set(f"已从剪贴板收集 {len(payload)} 个文件来源。")
            self.log(f"剪贴板文件列表：{len(payload)} 个")
            return
        if kind == "text" and isinstance(payload, str) and payload.strip():
            self.add_text(payload)
            self.status_var.set("已从剪贴板收集文本，可在清单中补充文件名。")
            self.log("从剪贴板收集文本（未启动后台监听）")
            return
        messagebox.showinfo("收集剪贴板", "剪贴板中没有可用的文件列表或文本。")

    # ---------- 预览与导入 ----------

    def _build_request(self) -> dict[str, Any]:
        root = self.root_dir_var.get().strip()
        if not root:
            raise core.ValidationError("请先选择或输入项目目录")
        sources = []
        for entry in self.source_entries:
            spec: dict[str, Any] = {}
            if entry.source_kind == "path":
                spec["path"] = entry.path
            else:
                spec["text"] = entry.text
            if entry.filename_override.strip():
                spec["filename"] = entry.filename_override.strip()
            if entry.destination_override.strip():
                spec["destination"] = entry.destination_override.strip()
            sources.append(spec)
        if not sources:
            raise core.ValidationError("待导入清单为空")
        return {
            "root": root,
            "project_name": self.project_name_var.get().strip(),
            "profile": self.profile_var.get(),
            "routes": self._current_routes(),
            "cls_layout": "package",
            "requirement": {
                "system": "manual",
                "id": self.req_id_var.get().strip(),
                "title": self.req_title_var.get().strip(),
                "description": self.req_desc.get("1.0", "end-1c").strip(),
                "url": self.req_url_var.get().strip(),
                "updated_at": "",
            },
            "sources": sources,
        }

    def _signature(self, request: dict[str, Any]) -> str:
        payload = json.dumps(request, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()

    def _invalidate_preview(self, message: str = "") -> None:
        if self._preview_state is not None:
            self._preview_state = None
            for entry in self.source_entries:
                entry.preview = None
            self.refresh_tree()
            if message:
                self.log(message)

    def _apply_plan_to_entries(self, plan: dict[str, Any]) -> None:
        for entry, item in zip(self.source_entries, plan.get("items", [])):
            entry.preview = dict(item)
        self.refresh_tree()

    def run_preview(self) -> dict[str, Any] | None:
        try:
            request = self._build_request()
        except core.ValidationError as exc:
            self.status_var.set(str(exc))
            messagebox.showwarning("无法预览", str(exc))
            return None
        try:
            plan = core.build_plan(request)
        except core.SourceIntakeError as exc:
            self._preview_state = None
            self.log(f"预览失败：{exc}")
            self.status_var.set(f"预览失败：{exc}")
            messagebox.showerror("预览失败", str(exc))
            return None
        except Exception as exc:
            self._preview_state = None
            self.log(f"预览出现异常：{exc}")
            messagebox.showerror("预览失败", f"发生未预期错误：{exc}")
            return None
        if not request.get("project_name"):
            request["project_name"] = plan.get("project_name") or ""
            self.project_name_var.set(request["project_name"])
        self._preview_state = {"plan": plan, "signature": self._signature(request), "request": request}
        self._apply_plan_to_entries(plan)
        summary = plan.get("summary", {})
        self.log(
            "预览："
            f"共 {summary.get('total', 0)} 项，待导入 {summary.get('ready', 0)}，"
            f"内容一致 {summary.get('same', 0)}，同名冲突 {summary.get('exists', 0)}，"
            f"待补充 {summary.get('pending', 0)}，无效 {summary.get('invalid', 0)}，"
            f"目标重复 {summary.get('conflict', 0)}。"
        )
        for item in plan.get("items", []):
            notes = "；".join(item.get("notes") or [])
            if notes:
                self.log(f"#{item['index'] + 1} {STATUS_TEXT.get(item['status'], item['status'])}：{notes}")
        for warning in plan.get("warnings", []):
            self.log("警告：" + warning)
        self.log("预览不会创建项目目录；确认后点击“导入”。" if not plan.get("root_exists") else "预览完成，确认后点击“导入”。")
        self._remember_project()
        pending_like = summary.get("pending", 0) + summary.get("invalid", 0) + summary.get("conflict", 0)
        self.status_var.set(
            f"预览完成：待导入 {summary.get('ready', 0)} 项"
            + (f"，需处理 {pending_like} 项" if pending_like else "")
            + "。"
        )
        if pending_like:
            messagebox.showwarning(
                "预览完成",
                f"有 {pending_like} 项待补充、无效或目标冲突，整批导入会被阻止。\n"
                "请查看日志，修正后重新预览。",
            )
        else:
            messagebox.showinfo("预览完成", "全部来源已解析，确认后点击“导入”。")
        return plan

    def run_import(self) -> dict[str, Any] | None:
        if self._preview_state is None:
            messagebox.showwarning("需要预览", "请先点击“预览”检查目标路径和状态，再执行导入。")
            return None
        try:
            request = self._build_request()
        except core.ValidationError as exc:
            messagebox.showwarning("无法导入", str(exc))
            return None
        if self._signature(request) != self._preview_state["signature"]:
            self._invalidate_preview("清单或配置已变化，请重新预览")
            messagebox.showwarning("需要重新预览", "清单或配置在预览后发生变化，请重新预览。")
            return None
        expected = self._preview_state["plan"]
        expected_summary = expected.get("summary", {})
        blocked_count = (
            expected_summary.get("pending", 0)
            + expected_summary.get("invalid", 0)
            + expected_summary.get("conflict", 0)
        )
        if blocked_count:
            messagebox.showwarning(
                "批次未导入",
                f"清单中有 {blocked_count} 项待补充、无效或目标冲突。\n"
                "为避免只导入部分来源，本次不会写入任何文件。请修正后重新预览。",
            )
            self.status_var.set("存在未决项，整批未导入。")
            self.log("导入被阻止：存在待补充、无效或冲突项，整批未写入。")
            return None
        try:
            result = core.apply_request(
                request,
                conflict_policy=self.conflict_var.get(),
                expected_plan=expected,
            )
        except core.PlanMismatchError as exc:
            self._preview_state = None
            for entry in self.source_entries:
                if entry.preview:
                    entry.preview = dict(entry.preview, status="pending")
            self.refresh_tree()
            self.log(f"导入被拒绝：{exc}")
            self.status_var.set("预览后来源或目标已变化，请重新预览。")
            messagebox.showerror("请重新预览", str(exc))
            return None
        except core.SourceIntakeError as exc:
            self.log(f"导入失败：{exc}")
            self.status_var.set(f"导入失败：{exc}")
            messagebox.showerror("导入失败", str(exc))
            return None
        except Exception as exc:
            self.log(f"导入出现异常：{exc}")
            messagebox.showerror("导入失败", f"发生未预期错误：{exc}")
            return None

        for entry, item in zip(self.source_entries, result.get("items", [])):
            entry.preview = dict(item)
        self.refresh_tree()
        self.project_name_var.set(result.get("project_name") or self.project_name_var.get())
        summary = result.get("summary", {})
        self.log(
            f"导入结果：{result.get('status')}，批次 {result.get('batch_id') or '—'}，"
            f"批次目录 {result.get('batch_dir') or '—'}"
        )
        self.log("结果统计：" + json.dumps(summary, ensure_ascii=False))
        for item in result.get("items", []):
            status = item.get("status", "")
            if status in ("failed", "rollback-failed") or item.get("error"):
                self.log(f"#{item['index'] + 1} {STATUS_TEXT.get(status, status)}：{item.get('error') or ''}")
        self._remember_project()
        self._preview_state = None
        if not result.get("ok"):
            self.status_var.set("导入失败，已按日志处理回滚。")
            messagebox.showerror(
                "导入失败",
                f"批次 {result.get('batch_id')}：{result.get('message') or ''}\n"
                f"{result.get('error', {}).get('message', '')}\n请查看导入日志和批次清单。",
            )
            return result
        if result.get("status") == "no-write":
            self.status_var.set("没有可写入的条目。")
            messagebox.showwarning("没有写入", result.get("message") or "没有可写入的条目。")
            return result
        self.status_var.set(
            f"导入完成：写入/替换 {summary.get('written', 0) + summary.get('replaced', 0)} 项，"
            f"跳过 {summary.get('skipped-same', 0) + summary.get('skipped-existing', 0)} 项。"
        )
        remaining = summary.get("pending", 0) + summary.get("invalid", 0) + summary.get("conflict", 0)
        detail = (
            f"批次 {result.get('batch_id')}\n"
            f"写入 {summary.get('written', 0)}，替换 {summary.get('replaced', 0)}，"
            f"内容一致跳过 {summary.get('skipped-same', 0)}，同名跳过 {summary.get('skipped-existing', 0)}。"
        )
        if remaining:
            detail += f"\n另有 {remaining} 项未导入（待补充/无效/冲突），请查看清单和日志。"
        messagebox.showinfo("导入完成", detail)
        return result

    def on_preview(self) -> None:
        self.run_preview()

    def on_import(self) -> None:
        self.run_import()

    def on_open_target(self) -> None:
        root_text = self.root_dir_var.get().strip()
        if not root_text:
            messagebox.showinfo("打开目标目录", "请先选择或输入项目目录。")
            return
        target = Path(os.path.abspath(os.path.expanduser(root_text)))
        if not target.exists():
            messagebox.showinfo("打开目标目录", f"目录尚不存在，导入时会创建：\n{target}")
            return
        if not target.is_dir():
            messagebox.showerror("打开目标目录", f"当前路径是文件，不是目录：\n{target}")
            return
        try:
            os.startfile(str(target))  # type: ignore[attr-defined]
        except (OSError, AttributeError) as exc:
            messagebox.showerror("打开目标目录", f"无法打开目录：{exc}")

    # ---------- 通用 ----------

    def log(self, message: str) -> None:
        self.log_text.configure(state="normal")
        self.log_text.insert("end", message.rstrip() + "\n")
        self.log_text.see("end")
        self.log_text.configure(state="disabled")

    def on_close(self) -> None:
        self._remember_project()
        self.master.destroy()


def main() -> int:
    if os.name == "nt":
        try:
            ctypes.windll.shcore.SetProcessDpiAwareness(1)
        except Exception:
            pass
    try:
        root = tk.Tk()
    except tk.TclError as exc:
        print(f"无法初始化 Tkinter：{exc}", file=sys.stderr)
        return 1
    app = SourceIntakeApp(root)
    root.mainloop()
    del app
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
