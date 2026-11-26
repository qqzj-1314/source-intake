"""Black-box CLI/reader integration for work handoffs."""
from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parents[1] / "skill/source-intake/scripts"
sys.path.insert(0, str(SCRIPTS))
import requirement_context as rc


class HandoffCliTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name) / "需求包"
        self.root.mkdir()
        (self.root / "attachments").mkdir()
        self.original = {"README.md": b"# work package\r\n", "requirement.json": b'{"requirement":{"id":"CLI-QA"}}',
                         "修改说明.md": "原始补充说明\r\n保持接口。\r\n".encode("utf-8")}
        for name, data in self.original.items():
            (self.root / name).write_bytes(data)

    def run_cli(self, *args):
        result = subprocess.run([sys.executable, "-B", "-X", "utf8", str(SCRIPTS / "requirement_handoff.py"), *args],
                                capture_output=True, timeout=30)
        payload = json.loads(result.stdout.decode("utf-8"))
        self.assertNotIn(b"Traceback", result.stderr)
        return result.returncode, payload

    def record_input(self, **updates):
        fingerprint = rc.read_context(self.root)["handoff"]["material_snapshot"]["fingerprint"]
        value = {"author": "CLI fixture", "status": "in_progress", "summary": "已核对材料。\r\n下一步实现。",
                 "next_steps": "运行适当验证。", "expected_material_fingerprint": fingerprint}
        value.update(updates)
        path = Path(self.temp.name) / "record.json"
        path.write_bytes(json.dumps(value, ensure_ascii=False).encode("utf-8"))
        return path

    def test_read_is_side_effect_free_and_append_survives_new_process(self):
        code, empty = self.run_cli("read", "--directory", str(self.root))
        self.assertEqual(code, 0)
        self.assertEqual(empty["records"], [])
        self.assertFalse((self.root / "handoffs").exists())
        record = self.record_input()
        code, saved = self.run_cli("append", "--directory", str(self.root), "--input", str(record))
        self.assertEqual(code, 0, saved)
        self.assertTrue(saved["ok"])
        code, resumed = self.run_cli("read", "--directory", str(self.root))
        self.assertEqual(code, 0)
        self.assertEqual(resumed["records"][0]["summary"], "已核对材料。\r\n下一步实现。")
        context = rc.read_context(self.root)
        self.assertEqual(context["handoff"]["records"][0]["material_match"], "unchanged")
        self.assertEqual(context["prompt"], self.original["修改说明.md"].decode("utf-8"))
        for name, data in self.original.items():
            self.assertEqual((self.root / name).read_bytes(), data)

    def test_stale_material_view_rejects_append_without_creating_storage(self):
        record = self.record_input()
        (self.root / "attachments/new.txt").write_bytes(b"new requirement")
        code, payload = self.run_cli("append", "--directory", str(self.root), "--input", str(record))
        self.assertEqual(code, 1)
        self.assertFalse(payload["ok"])
        self.assertFalse((self.root / "handoffs").exists())

    def test_cli_returns_json_on_argument_json_and_size_errors(self):
        record = Path(self.temp.name) / "bad.json"
        for content in [b"{", b"x" * (128 * 1024 + 1), b"[]"]:
            record.write_bytes(content)
            code, payload = self.run_cli("append", "--directory", str(self.root), "--input", str(record))
            self.assertEqual(code, 1)
            self.assertFalse(payload["ok"])
        code, payload = self.run_cli("read", "--directory", str(self.root), "--limit", "0")
        self.assertEqual(code, 1)
        self.assertFalse(payload["ok"])

    def test_handoff_storage_error_does_not_hide_original_materials(self):
        (self.root / "handoffs").write_bytes(b"not a directory")
        context = rc.read_context(self.root)
        self.assertEqual(context["prompt"], self.original["修改说明.md"].decode("utf-8"))
        self.assertEqual(context["handoff"]["records"], [])
        self.assertTrue(context["handoff"]["warnings"])


if __name__ == "__main__":
    unittest.main()
