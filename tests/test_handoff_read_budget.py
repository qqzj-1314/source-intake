"""Independent acceptance of failure-path and changing-file read budgets."""
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from common import SCRIPTS_DIR
import handoff_store as hs


class HandoffReadBudgetTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        (self.root / "handoffs").mkdir()
        (self.root / "requirement.json").write_bytes(b'{"id":"budget-fixture"}')
        self.context = {"requirement": {"id": "budget-fixture"}, "prompt": "", "readme": "", "attachments": [], "warnings": []}

    def test_read_failures_still_consume_reserved_budget(self):
        for i in range(5):
            (self.root / f"handoffs/{i}.json").write_bytes(b"{}")
        actual_open = open
        reads = []

        class FailingRead:
            def __init__(self, handle):
                self.handle = handle
            def __enter__(self):
                return self
            def __exit__(self, *args):
                self.handle.close()
            def fileno(self):
                return self.handle.fileno()
            def read(self, size):
                reads.append(size)
                self.handle.read(1)
                raise OSError("injected read failure after consuming bytes")

        def failing_open(path, *args, **kwargs):
            return FailingRead(actual_open(path, *args, **kwargs))

        with mock.patch.object(hs, "MAX_TOTAL_READ_BYTES", 20), mock.patch.object(hs, "MAX_RECORD_BYTES", 9), mock.patch("builtins.open", side_effect=failing_open):
            result = hs.read_handoffs(self.root, context=self.context)
        self.assertLessEqual(sum(reads), 20)
        self.assertTrue(result["truncated"])
        self.assertTrue(result["warnings"])

    def test_growth_after_stat_cannot_turn_partial_json_into_complete_record(self):
        saved = hs.append_handoff(self.root, {"author": "budget fixture", "status": "in_progress", "summary": "fixture",
                                    "expected_material_fingerprint": hs.material_snapshot(self.context)["fingerprint"]}, context=self.context)
        path = Path(saved["path"])
        payload = path.read_bytes()
        path.write_bytes(payload + b" " * 100)
        actual_inspect = hs._inspect

        def stale_size(candidate):
            kind, info, error = actual_inspect(candidate)
            if Path(candidate) == path:
                return kind, SimpleNamespace(st_size=1), error
            return kind, info, error

        with mock.patch.object(hs, "_inspect", side_effect=stale_size), mock.patch.object(hs, "MAX_TOTAL_READ_BYTES", len(payload) + 3):
            result = hs.read_handoffs(self.root, context=self.context)
        self.assertEqual(result["records"], [])
        self.assertTrue(result["warnings"])
        self.assertTrue(result["truncated"])


if __name__ == "__main__":
    unittest.main()
