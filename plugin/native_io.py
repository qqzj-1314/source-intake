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


def dib_to_png(data: bytes) -> bytes:
    """Convert standard Windows BGR DIB screenshots without third-party libraries."""
    import struct
    import zlib
    if len(data) < 40:
        raise ValueError("截图DIB头不完整")
    header, width, height, planes, depth, compression = struct.unpack_from('<IiiHHI', data)
    if header < 40 or header > len(data) or planes != 1 or depth not in (24, 32) or compression not in (0, 3):
        raise ValueError("暂不支持此截图格式，请保存为图片后选择文件")
    if width <= 0 or height == 0 or width * abs(height) > 20_000_000:
        raise ValueError("截图尺寸过大或无效")
    offset = header
    if compression == 3:
        if depth != 32 or len(data) < 52:
            raise ValueError("不支持的截图颜色掩码")
        masks = struct.unpack_from('<III', data, 40)
        if masks != (0xff0000, 0xff00, 0xff):
            raise ValueError("不支持的截图颜色掩码")
        if header == 40:
            offset += 12
    colors = struct.unpack_from('<I', data, 32)[0]
    offset += colors * 4
    stride = ((width * depth + 31) // 32) * 4
    rows = abs(height)
    if offset + stride * rows > len(data):
        raise ValueError("截图像素不完整")
    raw = bytearray()
    step = depth // 8
    for y in range(rows):
        row = y if height < 0 else rows - 1 - y
        pixels = memoryview(data)[offset + row * stride:offset + row * stride + width * step]
        raw.append(0)
        for x in range(0, len(pixels), step):
            raw.extend((pixels[x + 2], pixels[x + 1], pixels[x]))
    def chunk(kind, payload):
        return struct.pack('>I', len(payload)) + kind + payload + struct.pack('>I', zlib.crc32(kind + payload) & 0xffffffff)
    return b'\x89PNG\r\n\x1a\n' + chunk(b'IHDR', struct.pack('>IIBBBBB', width, rows, 8, 2, 0, 0, 0)) + chunk(b'IDAT', zlib.compress(raw)) + chunk(b'IEND', b'')


def read_screenshot() -> dict[str, Any]:
    import base64
    if os.name != 'nt':
        return {"ok": True, "note": "请将截图保存为图片后选择文件。"}
    user = ctypes.WinDLL('user32', use_last_error=True)
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    user.OpenClipboard.argtypes = [ctypes.c_void_p]
    user.OpenClipboard.restype = ctypes.c_bool
    user.CloseClipboard.argtypes = []
    user.GetClipboardData.argtypes = [ctypes.c_uint]
    user.GetClipboardData.restype = ctypes.c_void_p
    user.IsClipboardFormatAvailable.argtypes = [ctypes.c_uint]
    user.RegisterClipboardFormatW.argtypes = [ctypes.c_wchar_p]
    user.RegisterClipboardFormatW.restype = ctypes.c_uint
    kernel.GlobalSize.argtypes = [ctypes.c_void_p]
    kernel.GlobalSize.restype = ctypes.c_size_t
    kernel.GlobalLock.argtypes = [ctypes.c_void_p]
    kernel.GlobalLock.restype = ctypes.c_void_p
    kernel.GlobalUnlock.argtypes = [ctypes.c_void_p]
    if not user.OpenClipboard(None):
        raise OSError('无法打开剪贴板')
    try:
        png_format = user.RegisterClipboardFormatW('PNG')
        for fmt in (png_format, 17, 8):
            if not fmt or not user.IsClipboardFormatAvailable(fmt):
                continue
            handle = user.GetClipboardData(fmt)
            if not handle:
                continue
            size = kernel.GlobalSize(handle)
            if not 0 < size <= 80_000_000:
                raise ValueError('截图过大，请保存为图片后选择文件')
            pointer = kernel.GlobalLock(handle)
            if not pointer:
                continue
            try:
                data = ctypes.string_at(pointer, size)
            finally:
                kernel.GlobalUnlock(handle)
            png = data if fmt == png_format else dib_to_png(data)
            if len(png) > 20_000_000 or not png.startswith(b'\x89PNG\r\n\x1a\n'):
                raise ValueError('截图格式无效或过大，请使用选择文件')
            return {"ok": True, "png_base64": base64.b64encode(png).decode('ascii')}
        return {"ok": True, "note": "剪贴板中没有截图，请复制截图或选择图片文件。"}
    finally:
        user.CloseClipboard()


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
        options = {"title": "选择源码或需求附件", "filetypes": [("所有文件", "*.*")]}
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
    "screenshot": lambda payload: read_screenshot(),
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
