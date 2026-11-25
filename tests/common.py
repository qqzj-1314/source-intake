from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

DEV_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS_DIR = DEV_ROOT / "skill" / "source-intake" / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

import source_intake as si  # noqa: E402

STROKE_ROOT = Path(r"D:\粤北卒中中心")


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def make_dir_link(target: Path, link: Path) -> bool:
    """优先 os.symlink，失败时用 Windows junction；返回是否创建成功。"""
    try:
        os.symlink(str(target), str(link), target_is_directory=True)
        return True
    except (OSError, NotImplementedError):
        pass
    if os.name == "nt":
        result = subprocess.run(
            ["cmd", "/c", "mklink", "/J", str(link), str(target)],
            capture_output=True,
            text=True,
            encoding="mbcs",
            errors="replace",
        )
        return result.returncode == 0 and link.exists()
    return False


class IntakeTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self._tempdir = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tempdir.name)
        self.root = self.tmp / "目标项目"
        self.source_dir = self.tmp / "来源"
        self.source_dir.mkdir()

    def tearDown(self) -> None:
        self._tempdir.cleanup()

    def write_source(self, name: str, data: bytes | str) -> str:
        path = self.source_dir / name
        path.parent.mkdir(parents=True, exist_ok=True)
        if isinstance(data, str):
            path.write_text(data, encoding="utf-8", newline="")
        else:
            path.write_bytes(data)
        return str(path)

    def requirement(self, **overrides):
        value = {
            "system": "manual",
            "id": "BOS-123",
            "title": "示例需求",
            "description": "需求描述",
            "url": "http://example.invalid/req/123",
            "updated_at": "",
        }
        value.update(overrides)
        return value

    def request(self, sources, *, root: Path | None = None, **overrides):
        value = {
            "root": str(root or self.root),
            "project_name": "目标项目",
            "profile": "hisui",
            "requirement": self.requirement(),
            "sources": sources,
        }
        value.update(overrides)
        return value

    def build_plan(self, request):
        return si.build_plan(request)

    def apply(self, request, **kwargs):
        return si.apply_request(request, **kwargs)

    def batch_manifests(self, root: Path | None = None) -> list[dict]:
        batches_dir = (root or self.root) / si.META_DIR / si.BATCHES_DIR
        manifests = []
        for path in sorted(batches_dir.glob("*/manifest.json")):
            manifests.append(json.loads(path.read_text(encoding="utf-8")))
        return manifests

    def latest_manifest(self, root: Path | None = None) -> dict:
        batches_dir = (root or self.root) / si.META_DIR / si.BATCHES_DIR
        paths = list(batches_dir.glob("*/manifest.json"))
        self.assertTrue(paths, "未找到批次清单")
        newest = max(paths, key=lambda item: item.stat().st_mtime_ns)
        return json.loads(newest.read_text(encoding="utf-8"))

    def read_json(self, path: Path):
        return json.loads(path.read_text(encoding="utf-8"))

    def cli(self, *args: str, timeout: int = 120) -> subprocess.CompletedProcess:
        return subprocess.run(
            [sys.executable, str(SCRIPTS_DIR / "source_intake.py"), *args],
            capture_output=True,
            timeout=timeout,
        )

    def cli_json(self, process: subprocess.CompletedProcess):
        return json.loads(process.stdout.decode("utf-8"))
