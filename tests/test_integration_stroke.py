from __future__ import annotations

import hashlib
import unittest
from pathlib import Path

from common import STROKE_ROOT, IntakeTestCase, si


def tree_snapshot(root: Path) -> dict[str, tuple[int, int, str]]:
    snapshot = {}
    for path in sorted(root.rglob("*")):
        if path.is_file():
            stat = path.stat()
            digest = hashlib.sha256(path.read_bytes()).hexdigest()
            snapshot[str(path.relative_to(root))] = (stat.st_size, stat.st_mtime_ns, digest)
    return snapshot


class StrokeProjectIntegrationTests(IntakeTestCase):
    def test_import_four_csp_and_four_js_without_touching_original(self):
        if not STROKE_ROOT.is_dir():
            self.skipTest(f"未找到集成样例目录：{STROKE_ROOT}")
        csp_files = sorted((STROKE_ROOT / "frontend" / "csp").glob("*.csp"))
        js_files = sorted((STROKE_ROOT / "frontend" / "scripts" / "dhcnewpro" / "dhcemc").glob("*.js"))
        if len(csp_files) != 4 or len(js_files) != 4:
            self.skipTest(f"样例文件数量不符合 4 CSP + 4 JS：{len(csp_files)}/{len(js_files)}")

        before = tree_snapshot(STROKE_ROOT)
        requirement = self.requirement(
            system="BOS",
            id="BOS-STROKE-2026-11-25",
            title="卒中项目源码导入验收",
            description="只读导入 4 个 CSP 与 4 个 JS 到隔离目录。",
        )
        request = self.request(
            [{"path": str(path)} for path in [*csp_files, *js_files]],
            requirement=requirement,
        )
        plan = self.build_plan(request)
        self.assertEqual(plan["summary"]["total"], 8)
        self.assertEqual(plan["summary"]["ready"], 8)
        expected_csp = {f"frontend/csp/{path.name}" for path in csp_files}
        expected_js = {f"frontend/scripts/dhcnewpro/dhcemc/{path.name}" for path in js_files}
        actual_csp = {item["destination"] for item in plan["items"][:4]}
        actual_js = {item["destination"] for item in plan["items"][4:]}
        self.assertEqual(actual_csp, expected_csp)
        self.assertEqual(actual_js, expected_js)

        result = self.apply(request, expected_plan=plan)
        self.assertTrue(result["ok"], result)
        self.assertEqual(result["status"], "completed")
        self.assertEqual(result["summary"], {"written": 8, "total": 8})

        all_targets = [
            path for path in self.root.rglob("*")
            if path.is_file() and si.META_DIR not in path.relative_to(self.root).parts
        ]
        self.assertEqual(len(all_targets), 8)
        for source in [*csp_files, *js_files]:
            if source in csp_files:
                target = self.root / "frontend" / "csp" / source.name
            else:
                target = self.root / "frontend" / "scripts" / "dhcnewpro" / "dhcemc" / source.name
            self.assertTrue(target.is_file(), target)
            self.assertEqual(target.read_bytes(), source.read_bytes(), target)

        manifest = self.latest_manifest()
        self.assertEqual(manifest["requirement"]["id"], "BOS-STROKE-2026-11-25")
        self.assertEqual(manifest["project"]["name"], "目标项目")
        self.assertEqual(len(manifest["items"]), 8)
        requirement_snapshot = self.read_json(
            self.root / si.META_DIR / si.BATCHES_DIR / manifest["batch_id"] / "requirement.json"
        )
        self.assertEqual(requirement_snapshot["id"], "BOS-STROKE-2026-11-25")
        self.assertEqual(requirement_snapshot["system"], "BOS")

        after = tree_snapshot(STROKE_ROOT)
        self.assertEqual(before, after, "原业务项目在导入过程中被修改")


if __name__ == "__main__":
    unittest.main()
