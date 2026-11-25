from __future__ import annotations

import json
import unittest

from common import IntakeTestCase


class CliTests(IntakeTestCase):
    def write_request(self, request, name: str = "request.json"):
        path = self.tmp / name
        path.write_text(json.dumps(request, ensure_ascii=False, indent=2), encoding="utf-8")
        return path

    def test_plan_is_json_only_and_does_not_create_root(self):
        request = self.request([{"text": "function cliPlan() { return 1; }\n", "filename": "cli.js"}])
        path = self.write_request(request)
        process = self.cli("plan", "--manifest", str(path))
        self.assertEqual(process.returncode, 0, process.stderr.decode("utf-8", "replace"))
        self.assertEqual(process.stderr, b"")
        payload = self.cli_json(process)
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["command"], "plan")
        self.assertEqual(payload["items"][0]["status"], "ready")
        self.assertEqual(payload["requirement"]["id"], "BOS-123")
        self.assertFalse(self.root.exists())
        self.assertNotIn("Traceback", process.stderr.decode("utf-8", "replace"))

    def test_missing_or_invalid_arguments_exit_nonzero_with_json(self):
        process = self.cli("plan")
        self.assertEqual(process.returncode, 2)
        payload = self.cli_json(process)
        self.assertFalse(payload["ok"])
        self.assertEqual(payload["error"]["type"], "ValidationError")
        self.assertTrue(process.stderr)

        bad = self.tmp / "bad.json"
        bad.write_text("{not json", encoding="utf-8")
        process = self.cli("plan", "--manifest", str(bad))
        self.assertEqual(process.returncode, 2)
        payload = self.cli_json(process)
        self.assertFalse(payload["ok"])
        self.assertIn("JSON", payload["error"]["message"])

        missing_root = self.tmp / "no-root.json"
        missing_root.write_text(json.dumps({"sources": []}), encoding="utf-8")
        process = self.cli("plan", "--manifest", str(missing_root))
        self.assertEqual(process.returncode, 2)
        self.assertFalse(self.cli_json(process)["ok"])

    def test_expected_plan_round_trip_apply_and_history(self):
        csp = self.write_source("page.csp", "<!-- page.csp 页面 -->\n<csp:method name=X>\n quit 1\n</csp:method>\n")
        js = self.write_source("page.js", "function page() { return 1; }\r\n")
        request = self.request(
            [{"path": csp}, {"path": js}],
            requirement=self.requirement(system="BOS", id="BOS-CLI-9", title="CLI 需求"),
        )
        request_path = self.write_request(request)
        plan_process = self.cli("plan", "--manifest", str(request_path))
        self.assertEqual(plan_process.returncode, 0)
        plan_file = self.tmp / "expected-plan.json"
        plan_file.write_bytes(plan_process.stdout)
        self.assertFalse(self.root.exists())
        parse_again = json.loads(plan_file.read_text(encoding="utf-8"))
        self.assertEqual(parse_again["command"], "plan")

        apply_process = self.cli(
            "apply", "--manifest", str(request_path), "--expected-plan", str(plan_file)
        )
        self.assertEqual(apply_process.returncode, 0, apply_process.stderr.decode("utf-8", "replace"))
        applied = self.cli_json(apply_process)
        self.assertTrue(applied["ok"])
        self.assertEqual(applied["status"], "completed")
        self.assertEqual([item["status"] for item in applied["items"]], ["written", "written"])
        self.assertTrue((self.root / "frontend/csp/page.csp").is_file())
        self.assertTrue((self.root / "frontend/scripts/dhcnewpro/dhcemc/page.js").is_file())

        history_process = self.cli("history", "--root", str(self.root))
        self.assertEqual(history_process.returncode, 0)
        history = self.cli_json(history_process)
        self.assertTrue(history["ok"])
        self.assertEqual(len(history["batches"]), 1)
        self.assertEqual(history["batches"][0]["requirement"]["id"], "BOS-CLI-9")
        self.assertEqual(history["batches"][0]["status"], "completed")

    def test_utf16_redirected_expected_plan_is_accepted(self):
        request = self.request([{"text": "function utf16Plan() { return 1; }\n", "filename": "utf16.js"}])
        request_path = self.write_request(request)
        plan_process = self.cli("plan", "--manifest", str(request_path))
        self.assertEqual(plan_process.returncode, 0)
        plan_file = self.tmp / "expected-utf16.json"
        plan_file.write_bytes(plan_process.stdout.decode("utf-8").encode("utf-16"))
        apply_process = self.cli(
            "apply", "--manifest", str(request_path), "--expected-plan", str(plan_file)
        )
        self.assertEqual(apply_process.returncode, 0, apply_process.stderr.decode("utf-8", "replace"))
        self.assertTrue(self.cli_json(apply_process)["ok"])

    def test_expected_plan_mismatch_exits_2_and_writes_nothing(self):
        source = self.write_source("mismatch.js", "function before() { return 1; }\n")
        request = self.request([{"path": source}])
        request_path = self.write_request(request)
        plan_process = self.cli("plan", "--manifest", str(request_path))
        self.assertEqual(plan_process.returncode, 0)
        plan_file = self.tmp / "expected.json"
        plan_file.write_bytes(plan_process.stdout)

        self.write_source("mismatch.js", "function after() { return 2; }\n")
        apply_process = self.cli(
            "apply", "--manifest", str(request_path), "--expected-plan", str(plan_file)
        )
        self.assertEqual(apply_process.returncode, 2)
        payload = self.cli_json(apply_process)
        self.assertFalse(payload["ok"])
        self.assertEqual(payload["error"]["type"], "PlanMismatchError")
        self.assertIn("重新预览", payload["error"]["message"])
        self.assertFalse(self.root.exists())

    def test_pending_sources_block_the_batch_with_nonzero_exit(self):
        request = self.request([{"text": "function noName() { return 1; }\n"}])
        request_path = self.write_request(request)
        plan_process = self.cli("plan", "--manifest", str(request_path))
        self.assertEqual(plan_process.returncode, 0)
        plan = self.cli_json(plan_process)
        self.assertEqual(plan["items"][0]["status"], "pending")
        self.assertIsNone(plan["items"][0]["destination"])

        apply_process = self.cli("apply", "--manifest", str(request_path))
        self.assertEqual(apply_process.returncode, 1)
        applied = self.cli_json(apply_process)
        self.assertFalse(applied["ok"])
        self.assertEqual(applied["status"], "blocked")
        self.assertFalse(self.root.exists())


if __name__ == "__main__":
    unittest.main()
