#!/usr/bin/env python3
"""源码导入面板的原生 IO 桥：仅支持读取剪贴板和系统文件选择框。

由 Node 服务端用固定 Python 解释器和本文件绝对路径启动；
stdin 读一个 UTF-8 JSON 请求，stdout 只输出一个 JSON 对象，诊断写 stderr。
不监听剪贴板，不打开主窗口，不接受任意命令或任意文件读写。
"""

from __future__ import annotations

import argparse
import ctypes
import json
import os
import sys
from pathlib import Path
from typing import Any

CF_HDROP = 15
CF_UNICODETEXT = 13


def _configure_stdout() -> None:
    try:
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stdin.reconfigure(encoding="utf-8-sig")
    except (AttributeError, ValueError):
        pass


def _print_json(value: dict[str, Any]) -> None:
    print(json.dumps(value, ensure_ascii=False))


def read_clipboard() -> dict[str, Any]:
    if os.name != "nt":
        return {"ok": True, "kind": "empty", "note": "当前系统不支持 Windows 剪贴板读取。"}
    user32 = ctypes.WinDLL("user32", use_last_error=True)
    shell32 = ctypes.WinDLL("shell32", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    user32.IsClipboardFormatAvailable.argtypes = [ctypes.c_uint]
    user32.IsClipboardFormatAvailable.restype = ctypes.c_bool
    user32.OpenClipboard.argtypes = [ctypes.c_void_p]
    user32.OpenClipboard.restype = ctypes.c_bool
    user32.GetClipboardData.argtypes = [ctypes.c_uint]
    user32.GetClipboardData.restype = ctypes.c_void_p
    user32.CloseClipboard.argtypes = []
    user32.CloseClipboard.restype = ctypes.c_bool
    kernel32.GlobalLock.argtypes = [ctypes.c_void_p]
    kernel32.GlobalLock.restype = ctypes.c_void_p
    kernel32.GlobalUnlock.argtypes = [ctypes.c_void_p]
    kernel32.GlobalUnlock.restype = ctypes.c_bool
    shell32.DragQueryFileW.argtypes = [ctypes.c_void_p, ctypes.c_uint, ctypes.c_wchar_p, ctypes.c_uint]
    shell32.DragQueryFileW.restype = ctypes.c_uint

    notes: list[str] = []
    files: list[str] = []
    text = ""
    if not user32.OpenClipboard(None):
        raise OSError(ctypes.get_last_error(), "无法打开剪贴板")
    try:
        if user32.IsClipboardFormatAvailable(CF_HDROP):
            handle = user32.GetClipboardData(CF_HDROP)
            if handle:
                count = shell32.DragQueryFileW(handle, 0xFFFFFFFF, None, 0)
                for index in range(count):
                    length = shell32.DragQueryFileW(handle, index, None, 0)
                    if not length:
                        continue
                    buffer = ctypes.create_unicode_buffer(length + 1)
                    if shell32.DragQueryFileW(handle, index, buffer, length + 1):
                        files.append(buffer.value)
            else:
                notes.append("剪贴板文件列表不可用。")
        if not files and user32.IsClipboardFormatAvailable(CF_UNICODETEXT):
            handle = user32.GetClipboardData(CF_UNICODETEXT)
            if handle:
                pointer = kernel32.GlobalLock(handle)
                if pointer:
                    try:
                        text = ctypes.wstring_at(pointer)
                    finally:
                        kernel32.GlobalUnlock(handle)
            else:
                notes.append("剪贴板文本不可用。")
    finally:
        user32.CloseClipboard()

    note = "；".join(notes) if notes else None
    if files:
        return {"ok": True, "kind": "files", "files": files, **({"note": note} if note else {})}
    if text:
        return {"ok": True, "kind": "text", "text": text, **({"note": note} if note else {})}
    return {"ok": True, "kind": "empty", **({"note": note} if note else {})}


def _make_root():
    import tkinter as tk

    root = tk.Tk()
    root.withdraw()
    try:
        root.attributes("-topmost", True)
    except tk.TclError:
        pass
    return root


def _initial_dir(value: Any) -> str | None:
    if not isinstance(value, str) or not value.strip():
        return None
    path = Path(os.path.expanduser(value.strip()))
    if path.is_dir():
        return str(path)
    parent = path.parent
    while parent != parent.parent:
        if parent.is_dir():
            return str(parent)
        parent = parent.parent
    return None


def choose_directory(initial: Any) -> dict[str, Any]:
    from tkinter import filedialog

    root = _make_root()
    try:
        options = {"title": "选择项目目录（也可在面板中直接输入不存在的目录）", "mustexist": False}
        start = _initial_dir(initial)
        if start:
            options["initialdir"] = start
        selected = filedialog.askdirectory(parent=root, **options)
    finally:
        root.destroy()
    if not selected:
        return {"ok": True, "canceled": True, "path": None}
    return {"ok": True, "canceled": False, "path": str(Path(selected))}


def choose_files(initial: Any) -> dict[str, Any]:
    from tkinter import filedialog

    root = _make_root()
    try:
        options = {"title": "选择源码或 .txt 附件"}
        start = _initial_dir(initial)
        if start:
            options["initialdir"] = start
        selected = filedialog.askopenfilenames(parent=root, **options)
    finally:
        root.destroy()
    paths = [str(Path(item)) for item in selected if isinstance(item, str) and item]
    if not paths:
        return {"ok": True, "canceled": True, "paths": []}
    return {"ok": True, "canceled": False, "paths": paths}


COMMANDS = {
    "clipboard": lambda payload: read_clipboard(),
    "choose_directory": lambda payload: choose_directory(payload.get("initial")),
    "choose_files": lambda payload: choose_files(payload.get("initial")),
}


def main(argv: list[str] | None = None) -> int:
    _configure_stdout()
    parser = argparse.ArgumentParser(prog="native_io.py")
    parser.add_argument("command", choices=sorted(COMMANDS))
    try:
        args = parser.parse_args(argv)
        raw = sys.stdin.buffer.read() if sys.stdin is not None else b""
        payload = json.loads(raw.decode("utf-8-sig")) if raw.strip() else {}
        if not isinstance(payload, dict):
            raise ValueError("请求必须是对象")
        _print_json(COMMANDS[args.command](payload))
        return 0
    except Exception as exc:  # noqa: BLE001 - 返回给 Node 桥展示
        _print_json({"ok": False, "error": {"type": type(exc).__name__, "message": str(exc)}})
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
