from __future__ import annotations

import codecs
import json
import unittest
from pathlib import Path
from unittest import mock

from common import IntakeTestCase, make_dir_link, sha256, si


class BytePreservationTests(IntakeTestCase):
    def test_bom_crlf_and_gb18030_bytes_are_preserved(self):
        js_bytes = codecs.BOM_UTF8 + "var 名称 = 1;\r\n// 中文注释\r\n".encode("utf-8")
        css_bytes = "/* 中文样式 */\r\nbody { color: red; }\r\n".encode("gb18030")
        js_path = self.write_source("app.js", js_bytes)
        css_path = self.write_source("style.css", css_bytes)
        request = self.request([
            {"path": js_path},
            {"path": css_path},
        ])
        plan = self.build_plan(request)
        self.assertEqual([item["status"] for item in plan["items"]], ["ready", "ready"])
        self.assertEqual(plan["items"][0]["encoding"], "utf-8-sig")
        self.assertTrue(plan["items"][0]["bom"])
        self.assertEqual(plan["items"][0]["line_endings"], "crlf")
        self.assertEqual(plan["items"][1]["encoding"], "gb18030")
        self.assertEqual(plan["items"][1]["line_endings"], "crlf")

        result = self.apply(request)
        self.assertTrue(result["ok"], result)
        js_target = self.root / "frontend/scripts/dhcnewpro/dhcemc/app.js"
        css_target = self.root / "frontend/scripts/dhcnewpro/dhcemc/css/style.css"
        self.assertEqual(js_target.read_bytes(), js_bytes)
        self.assertEqual(css_target.read_bytes(), css_bytes)

        manifest = self.latest_manifest()
        batch_dir = self.root / si.META_DIR / si.BATCHES_DIR / manifest["batch_id"]
        for item, expected in zip(manifest["items"], (js_bytes, css_bytes)):
            snapshot = item["source"]["snapshot"]
            self.assertTrue(snapshot)
            self.assertEqual((batch_dir / snapshot).read_bytes(), expected)
            self.assertEqual(item["original_sha256"], sha256(expected))
            self.assertEqual(item["imported_sha256"], sha256(expected))
            self.assertEqual(item["status"], "written")


class RecognitionAndRoutingTests(IntakeTestCase):
    def test_csp_comment_cls_package_and_real_files(self):
        csp_text_path = self.write_source(
            "附件1.txt",
            "<!-- doc.fromtext.csp 页面注释 -->\n<csp:method name=OnPreHTTP arguments=\"\" returntype=%Boolean>\n quit 1\n</csp:method>\n",
        )
        cls_text = "Class DOC.EMDoc.BL.Demo Extends %RegisteredObject\n{\n}\n"
        real_csp = self.write_source("real.page.csp", "<csp:method name=X>\n quit 1\n</csp:method>\n")
        real_cls = self.write_source("Legacy.cls", cls_text)
        request = self.request([
            {"path": csp_text_path},
            {"text": cls_text},
            {"path": real_csp},
            {"path": real_cls},
        ])
        plan = self.build_plan(request)
        self.assertEqual([item["status"] for item in plan["items"]], ["ready"] * 4)
        self.assertEqual(plan["items"][0]["kind"], "csp")
        self.assertEqual(plan["items"][0]["destination"], "frontend/csp/doc.fromtext.csp")
        self.assertEqual(plan["items"][1]["kind"], "cls")
        self.assertEqual(plan["items"][1]["destination"], "backend/DOC/EMDoc/BL/Demo.cls")
        self.assertEqual(plan["items"][2]["destination"], "frontend/csp/real.page.csp")
        self.assertEqual(plan["items"][3]["package"], ["DOC", "EMDoc", "BL"])
        self.assertEqual(plan["items"][3]["destination"], "backend/DOC/EMDoc/BL/Legacy.cls")
        result = self.apply(request)
        self.assertTrue(result["ok"], result)
        for rel, content in (
            ("frontend/csp/doc.fromtext.csp", None),
            ("backend/DOC/EMDoc/BL/Demo.cls", cls_text.encode("utf-8")),
            ("frontend/csp/real.page.csp", None),
            ("backend/DOC/EMDoc/BL/Legacy.cls", cls_text.encode("utf-8")),
        ):
            self.assertTrue((self.root / rel).is_file(), rel)
            if content is not None:
                self.assertEqual((self.root / rel).read_bytes(), content)

    def test_flat_profile_keeps_cls_package_and_records_layout(self):
        request = self.request(
            [
                {"text": "Class DOC.EMDoc.BL.Flat Extends %RegisteredObject\n{\n}\n"},
                {"text": "<csp:method name=X>\n quit 1\n</csp:method>\n", "filename": "flat.csp"},
            ],
            profile="flat",
        )
        plan = self.build_plan(request)
        self.assertEqual(plan["cls_layout"], "package")
        self.assertEqual(plan["routes"], {"csp": "", "js": "", "css": "", "cls": ""})
        self.assertEqual(plan["items"][0]["destination"], "DOC/EMDoc/BL/Flat.cls")
        self.assertEqual(plan["items"][1]["destination"], "flat.csp")
        result = self.apply(request)
        self.assertTrue(result["ok"], result)
        manifest = self.latest_manifest()
        self.assertEqual(manifest["cls_layout"], "package")
        self.assertTrue((self.root / "DOC/EMDoc/BL/Flat.cls").is_file())


class NamingTests(IntakeTestCase):
    def test_unnamed_js_stays_pending_and_is_never_written(self):
        request = self.request([{"text": "function pending() { return 2; }\n"}])
        plan = self.build_plan(request)
        self.assertEqual(plan["items"][0]["status"], "pending")
        self.assertEqual(plan["items"][0]["kind"], "js")
        self.assertIsNone(plan["items"][0]["destination"])
        result = self.apply(request)
        self.assertFalse(result["ok"])
        self.assertEqual(result["status"], "blocked")
        self.assertFalse(self.root.exists())
        self.assertFalse((self.tmp / "frontend").exists())

        named = self.request([
            {"text": "function pending() { return 2; }\n", "filename": "pending.js"},
        ])
        plan = self.build_plan(named)
        self.assertEqual(plan["items"][0]["status"], "ready")
        self.assertEqual(
            plan["items"][0]["destination"],
            "frontend/scripts/dhcnewpro/dhcemc/pending.js",
        )

    def test_unnamed_js_gets_unique_csp_reference_only(self):
        page = (
            "<!-- page.csp 测试页面 -->\n"
            "<link href=\"../css/site.css\" rel=\"stylesheet\">\n"
            "<script src=\"../scripts/only.js\"></script>\n"
        )
        request = self.request([
            {"text": page},
            {"text": "function only() { return 1; }\n"},
        ])
        plan = self.build_plan(request)
        self.assertEqual(plan["items"][1]["status"], "ready")
        self.assertEqual(plan["items"][1]["filename"], "only.js")
        result = self.apply(request)
        self.assertTrue(result["ok"], result)
        self.assertTrue((self.root / "frontend/scripts/dhcnewpro/dhcemc/only.js").is_file())
        self.assertFalse((self.root / "frontend/scripts/dhcnewpro/dhcemc/css/site.css").exists())
        self.assertTrue((self.root / "frontend/csp/page.csp").is_file())
        self.assertEqual(len(list((self.root / "frontend").rglob("*.*"))), 2)

    def test_ambiguous_csp_references_do_not_guess(self):
        page = (
            "<!-- page.csp 页面 -->\n"
            "<script src=\"../scripts/a.js\"></script>\n"
            "<script src=\"../scripts/b.js\"></script>\n"
        )
        plan = self.build_plan(self.request([
            {"text": page},
            {"text": "function unnamed() { return 1; }\n"},
            {"text": "function other() { return 2; }\n"},
        ]))
        self.assertEqual(plan["items"][1]["status"], "pending")
        self.assertEqual(plan["items"][2]["status"], "pending")
        self.assertIsNone(plan["items"][1]["filename"])
        self.assertTrue(any("CSP 引用候选" in note for note in plan["items"][1]["notes"]))

        single = self.build_plan(self.request([
            {"text": page},
            {"text": "function unnamed() { return 1; }\n"},
        ]))
        self.assertEqual(single["items"][1]["status"], "pending")


class PasteFenceTests(IntakeTestCase):
    def test_single_fence_is_stripped_but_original_snapshot_is_exact(self):
        inner = 'var s = "&amp; \\\\";\r\n// 保留反斜杠与实体\r\n'
        full = "```javascript\r\n" + inner + "```"
        request = self.request([{"text": full, "filename": "raw.js"}])
        plan = self.build_plan(request)
        item = plan["items"][0]
        self.assertTrue(item["fence_stripped"])
        self.assertEqual(item["fence_language"], "js")
        self.assertEqual(item["imported_sha256"], sha256(inner.encode("utf-8")))
        self.assertEqual(item["original_sha256"], sha256(full.encode("utf-8")))
        result = self.apply(request)
        self.assertTrue(result["ok"], result)
        target = self.root / "frontend/scripts/dhcnewpro/dhcemc/raw.js"
        self.assertEqual(target.read_bytes(), inner.encode("utf-8"))
        manifest = self.latest_manifest()
        batch_dir = self.root / si.META_DIR / si.BATCHES_DIR / manifest["batch_id"]
        snapshot = batch_dir / manifest["items"][0]["source"]["snapshot"]
        self.assertEqual(snapshot.read_bytes(), full.encode("utf-8"))
        self.assertEqual(manifest["items"][0]["imported_sha256"], sha256(target.read_bytes()))

    def test_fence_filename_is_used_when_explicit_filename_is_missing(self):
        full = "```js filename=demo.js\r\nfunction fenced() { return 1; }\r\n```"
        plan = self.build_plan(self.request([{"text": full}]))
        item = plan["items"][0]
        self.assertEqual(item["status"], "ready")
        self.assertEqual(item["fence_filename"], "demo.js")
        self.assertEqual(item["filename"], "demo.js")
        self.assertEqual(
            item["destination"],
            "frontend/scripts/dhcnewpro/dhcemc/demo.js",
        )

        css_fence = "```style.css\n.demo { color: green; }\n```"
        plan = self.build_plan(self.request([{"text": css_fence}]))
        item = plan["items"][0]
        self.assertEqual(item["kind"], "css")
        self.assertEqual(item["filename"], "style.css")
        self.assertEqual(item["status"], "ready")

    def test_root_package_cls_is_routed_without_package_directory(self):
        text = "Class RootOnly Extends %RegisteredObject\n{\n}\n"
        plan = self.build_plan(self.request([{"text": text}]))
        item = plan["items"][0]
        self.assertEqual(item["kind"], "cls")
        self.assertEqual(item["package"], [])
        self.assertEqual(item["filename"], "RootOnly.cls")
        self.assertEqual(item["destination"], "backend/RootOnly.cls")
        self.assertEqual(item["status"], "ready")

    def test_multiple_fences_require_a_single_snippet(self):
        full = "```js\nvar a = 1;\n```\n\n```js\nvar b = 2;\n```"
        plan = self.build_plan(self.request([{"text": full, "filename": "two.js"}]))
        self.assertEqual(plan["items"][0]["status"], "pending")
        self.assertTrue(any("代码围栏" in note for note in plan["items"][0]["notes"]))
        result = self.apply(self.request([{"text": full, "filename": "two.js"}]))
        self.assertFalse(result["ok"])
        self.assertEqual(result["status"], "blocked")
        self.assertFalse(self.root.exists())


class PathSafetyTests(IntakeTestCase):
    def test_unicode_equivalent_destinations_are_portable_conflicts(self):
        request = self.request([{'text': 'var value=1;', 'filename': 'caf\u00e9.js'}, {'text': 'var value=2;', 'filename': 'cafe\u0301.js'}])
        plan = si.build_plan(request)
        self.assertEqual([item['status'] for item in plan['items']], ['conflict', 'conflict'])
        self.assertFalse(self.root.exists())

    def test_traversal_absolute_unc_reserved_and_metadata_targets_are_invalid(self):
        bad_destinations = [
            "../evil.js",
            "..\\evil.js",
            "C:/evil.js",
            "C:evil.js",
            "//server/share/evil.js",
            "\\\\server\\share\\evil.js",
            "CON.js",
            "sub/AUX.txt",
            "a?.js",
            ".source-intake/x.js",
            "sub/../x.js",
            "sub/../../x.js",
            "sub/",
        ]
        sources = [
            {"text": "function bad() { return 1; }\n", "destination": destination}
            for destination in bad_destinations
        ]
        request = self.request(sources)
        plan = self.build_plan(request)
        self.assertEqual([item["status"] for item in plan["items"]], ["invalid"] * len(sources))
        for item in plan["items"]:
            self.assertIsNone(item["destination"])
            self.assertTrue(item["notes"])
        result = self.apply(request)
        self.assertFalse(result["ok"])
        self.assertEqual(result["status"], "blocked")
        self.assertFalse(self.root.exists())
        self.assertFalse((self.tmp / "evil.js").exists())

    def test_directory_target_is_reported_clearly(self):
        target_dir = self.root / "frontend/scripts/dhcnewpro/dhcemc/app.js"
        target_dir.mkdir(parents=True)
        request = self.request([{
            "text": "function dirTarget() { return 1; }\n",
            "filename": "app.js",
        }])
        plan = self.build_plan(request)
        self.assertEqual(plan["items"][0]["status"], "invalid")
        self.assertTrue(any("目录" in note for note in plan["items"][0]["notes"]))
        result = self.apply(request)
        self.assertFalse(result["ok"])
        self.assertEqual(result["status"], "blocked")
        self.assertTrue(target_dir.is_dir())

    def test_symlink_or_junction_escape_is_rejected(self):
        self.root.mkdir()
        outside = self.tmp / "外部目录"
        outside.mkdir()
        link = self.root / "linked"
        if not make_dir_link(outside, link):
            self.skipTest("当前环境无法创建符号链接或 junction")
        request = self.request([{
            "text": "function escape() { return 1; }\n",
            "filename": "evil.js",
            "destination": "linked/evil.js",
        }])
        plan = self.build_plan(request)
        self.assertEqual(plan["items"][0]["status"], "invalid")
        self.assertTrue(any("项目目录之外" in note or "链接" in note for note in plan["items"][0]["notes"]))
        result = self.apply(request)
        self.assertFalse(result["ok"])
        self.assertEqual(result["status"], "blocked")
        self.assertFalse((outside / "evil.js").exists())

    def test_link_created_after_preview_is_refused(self):
        self.root.mkdir()
        outside = self.tmp / "预览后外部"
        outside.mkdir()
        request = self.request([{
            "text": "function later() { return 1; }\n",
            "filename": "later.js",
            "destination": "sub/later.js",
        }])
        expected = self.build_plan(request)
        self.assertEqual(expected["items"][0]["status"], "ready")
        if not make_dir_link(outside, self.root / "sub"):
            self.skipTest("当前环境无法创建符号链接或 junction")
        with self.assertRaises(si.PlanMismatchError):
            self.apply(request, expected_plan=expected)
        self.assertFalse((outside / "later.js").exists())

    def test_case_insensitive_duplicate_targets_are_conflicts(self):
        request = self.request([
            {"text": "function one() { return 1; }\n", "destination": "Dir/App.js"},
            {"text": "function two() { return 2; }\n", "destination": "dir/app.js"},
        ])
        plan = self.build_plan(request)
        self.assertEqual([item["status"] for item in plan["items"]], ["conflict", "conflict"])
        self.assertTrue(all("同一目标" in note for item in plan["items"] for note in item["notes"]))
        result = self.apply(request)
        self.assertFalse(result["ok"])
        self.assertEqual(result["status"], "blocked")
        self.assertFalse(self.root.exists())


class ConflictAndPreviewTests(IntakeTestCase):
    def test_identical_target_is_skipped(self):
        content = b"function same() { return 1; }\r\n"
        source = self.write_source("same.js", content)
        target = self.root / "frontend/scripts/dhcnewpro/dhcemc/same.js"
        target.parent.mkdir(parents=True)
        target.write_bytes(content)
        request = self.request([{"path": source}])
        plan = self.build_plan(request)
        self.assertEqual(plan["items"][0]["status"], "same")
        result = self.apply(request)
        self.assertTrue(result["ok"], result)
        self.assertEqual(result["items"][0]["status"], "skipped-same")
        self.assertEqual(target.read_bytes(), content)
        manifest = self.latest_manifest()
        self.assertEqual(manifest["items"][0]["status"], "skipped-same")
        self.assertEqual(manifest["status"], "completed")

    def test_same_name_skips_by_default_and_backup_replaces_on_request(self):
        old_content = b"function old() { return 1; }\r\n"
        new_content = b"function new() { return 2; }\r\n"
        source = self.write_source("app.js", new_content)
        target = self.root / "frontend/scripts/dhcnewpro/dhcemc/app.js"
        target.parent.mkdir(parents=True)
        target.write_bytes(old_content)
        request = self.request([{"path": source}])

        plan = self.build_plan(request)
        self.assertEqual(plan["items"][0]["status"], "exists")
        skipped = self.apply(request)
        self.assertTrue(skipped["ok"], skipped)
        self.assertEqual(skipped["items"][0]["status"], "skipped-existing")
        self.assertEqual(target.read_bytes(), old_content)

        replaced = self.apply(request, conflict_policy="backup-replace")
        self.assertTrue(replaced["ok"], replaced)
        self.assertEqual(replaced["items"][0]["status"], "replaced")
        self.assertEqual(target.read_bytes(), new_content)
        manifest = self.latest_manifest()
        item = manifest["items"][0]
        self.assertEqual(item["status"], "replaced")
        self.assertTrue(item["backup"])
        backup_path = self.root / si.META_DIR / si.BATCHES_DIR / manifest["batch_id"] / item["backup"]
        self.assertEqual(backup_path.read_bytes(), old_content)
        self.assertEqual(manifest["conflict_policy"], "backup-replace")

    def test_source_and_target_changes_after_preview_refuse_writes(self):
        original = "function v1() { return 1; }\n"
        source = self.write_source("guard.js", original)
        request = self.request([{"path": source}])
        expected = self.build_plan(request)
        self.write_source("guard.js", "function v2() { return 2; }\n")
        with self.assertRaises(si.PlanMismatchError):
            self.apply(request, expected_plan=expected)
        self.assertFalse(self.root.exists())

        self.write_source("guard.js", original)
        expected = self.build_plan(request)
        target = self.root / "frontend/scripts/dhcnewpro/dhcemc/guard.js"
        target.parent.mkdir(parents=True)
        target.write_bytes(b"function external() { return 9; }\r\n")
        with self.assertRaises(si.PlanMismatchError):
            self.apply(request, expected_plan=expected)
        self.assertEqual(target.read_bytes(), b"function external() { return 9; }\r\n")
        self.assertFalse(self.batch_manifests())


class BatchAndFailureTests(IntakeTestCase):
    def test_batch_tracks_requirement_hashes_and_history(self):
        csp = self.write_source(
            "doc.demo.csp",
            "<!-- doc.demo.csp 测试 -->\r\n<csp:method name=X>\r\n quit 1\r\n</csp:method>\r\n",
        )
        js = self.write_source("demo.js", "function demo() { return 1; }\r\n")
        css_text = "/* 粘贴样式 */\n.demo { color: blue; }\n"
        request = self.request(
            [
                {"path": csp},
                {"path": js},
                {"text": css_text, "filename": "demo.css"},
            ],
            requirement=self.requirement(system="BOS", id="BOS-777", title="中风需求"),
        )
        plan = self.build_plan(request)
        self.assertEqual(plan["summary"]["ready"], 3)
        result = self.apply(request, expected_plan=plan)
        self.assertTrue(result["ok"], result)
        self.assertEqual(result["status"], "completed")
        self.assertEqual(result["summary"], {"written": 3, "total": 3})
        manifest = self.latest_manifest()
        self.assertEqual(manifest["status"], "completed")
        self.assertEqual(manifest["requirement"]["system"], "BOS")
        self.assertEqual(manifest["requirement"]["id"], "BOS-777")
        self.assertEqual(len(manifest["items"]), 3)
        self.assertTrue(manifest["created_at"])
        self.assertTrue(all(item["written_at"] for item in manifest["items"]))
        self.assertTrue(all(item["imported_sha256"] == sha256((self.root / item["destination"]).read_bytes()) for item in manifest["items"]))
        batch_dir = self.root / si.META_DIR / si.BATCHES_DIR / manifest["batch_id"]
        requirement_snapshot = self.read_json(batch_dir / "requirement.json")
        self.assertEqual(requirement_snapshot["id"], "BOS-777")
        self.assertIn("snapshot_at", requirement_snapshot)
        self.assertTrue((batch_dir / "originals").is_dir())

        history = si.history(str(self.root))
        self.assertTrue(history["ok"])
        self.assertEqual(len(history["batches"]), 1)
        self.assertEqual(history["batches"][0]["requirement"]["id"], "BOS-777")
        self.assertEqual(history["batches"][0]["status"], "completed")

    def test_requirement_snapshot_failure_blocks_targets_and_is_tracked(self):
        source = self.write_source("baseline.js", "function baseline() { return 1; }\n")
        request = self.request([{"path": source}])
        real_json = si.atomic_write_json

        def flaky_json(path, value):
            if Path(path).name == "requirement.json":
                raise OSError("模拟需求快照失败")
            return real_json(path, value)

        with mock.patch.object(si, "atomic_write_json", side_effect=flaky_json):
            result = self.apply(request)

        self.assertFalse(result["ok"])
        self.assertEqual(result["status"], "failed")
        self.assertEqual(result["error"]["stage"], "requirement-snapshot")
        target = self.root / "frontend/scripts/dhcnewpro/dhcemc/baseline.js"
        self.assertFalse(target.exists())
        manifest = self.latest_manifest()
        self.assertEqual(manifest["status"], "failed")
        self.assertEqual(manifest["stage"], "requirement-snapshot")
        self.assertEqual(manifest["items"][0]["status"], "skipped-not-attempted")

    def test_original_snapshot_failure_blocks_targets_and_is_tracked(self):
        source = self.write_source("baseline.js", "function baseline() { return 1; }\n")
        request = self.request([{"path": source}])
        real_write = si.atomic_write_bytes

        def flaky(path, data):
            path = Path(path)
            if path.parent.name == "originals":
                raise OSError("模拟原始快照失败")
            return real_write(path, data)

        with mock.patch.object(si, "atomic_write_bytes", side_effect=flaky):
            result = self.apply(request)

        self.assertFalse(result["ok"])
        self.assertEqual(result["error"]["stage"], "originals")
        target = self.root / "frontend/scripts/dhcnewpro/dhcemc/baseline.js"
        self.assertFalse(target.exists())
        manifest = self.latest_manifest()
        self.assertEqual(manifest["status"], "failed")
        self.assertEqual(manifest["stage"], "originals")
        self.assertEqual(manifest["items"][0]["status"], "skipped-not-attempted")

    def test_manifest_initial_failure_reports_tracking_limitation(self):
        source = self.write_source("baseline.js", "function baseline() { return 1; }\n")
        request = self.request([{"path": source}])
        real_json = si.atomic_write_json

        def flaky_json(path, value):
            if Path(path).name == "manifest.json":
                raise OSError("模拟清单初始化失败")
            return real_json(path, value)

        with mock.patch.object(si, "atomic_write_json", side_effect=flaky_json):
            result = self.apply(request)

        self.assertFalse(result["ok"])
        self.assertEqual(result["status"], "failed")
        self.assertTrue(result["tracking_limited"])
        self.assertIn("manifest_error", result)
        self.assertFalse((self.root / "frontend/scripts/dhcnewpro/dhcemc/baseline.js").exists())
        batch_dir = Path(result["batch_dir"])
        self.assertTrue(batch_dir.is_dir())
        self.assertFalse((batch_dir / "manifest.json").exists())

    def test_final_manifest_failure_does_not_report_success(self):
        source = self.write_source("final.js", "function finalCheck() { return 1; }\n")
        request = self.request([{"path": source}])
        real_json = si.atomic_write_json
        manifest_writes = {"count": 0}

        def flaky_json(path, value):
            if Path(path).name == "manifest.json":
                manifest_writes["count"] += 1
                if manifest_writes["count"] == 3:
                    raise OSError("模拟最终清单保存失败")
            return real_json(path, value)

        with mock.patch.object(si, "atomic_write_json", side_effect=flaky_json):
            result = self.apply(request)

        self.assertFalse(result["ok"])
        self.assertEqual(result["status"], "tracking-failed")
        self.assertTrue(result["tracking_limited"])
        self.assertTrue((self.root / "frontend/scripts/dhcnewpro/dhcemc/final.js").is_file())
        self.assertTrue(any(
            item["status"] == "written" for item in result["items"]
        ))

    def test_write_failure_rolls_back_written_files_and_is_tracked(self):
        source_a = self.write_source("a.js", "function a() { return 1; }\n")
        source_b = self.write_source("b.js", "function b() { return 2; }\n")
        request = self.request([{"path": source_a}, {"path": source_b}])
        real_write = si.atomic_write_bytes

        def flaky(path, data):
            if Path(path).name == "b.js" and Path(path).parent.name == "dhcemc":
                raise OSError("模拟写入失败")
            return real_write(path, data)

        with mock.patch.object(si, "atomic_write_bytes", side_effect=flaky):
            result = self.apply(request)

        self.assertFalse(result["ok"])
        self.assertEqual(result["status"], "rolled-back")
        self.assertFalse((self.root / "frontend/scripts/dhcnewpro/dhcemc/a.js").exists())
        self.assertFalse((self.root / "frontend/scripts/dhcnewpro/dhcemc/b.js").exists())
        manifest = self.latest_manifest()
        self.assertEqual(manifest["status"], "rolled-back")
        statuses = {item["filename"]: item["status"] for item in manifest["items"]}
        self.assertEqual(statuses["a.js"], "rolled-back")
        self.assertEqual(statuses["b.js"], "failed")
        self.assertTrue(any("模拟写入失败" in (item.get("error") or "") for item in manifest["items"]))

    def test_write_failure_restores_replaced_file_from_backup(self):
        old_content = b"function old() { return 0; }\r\n"
        new_content = b"function new() { return 1; }\r\n"
        source_a = self.write_source("a.js", new_content)
        source_b = self.write_source("b.js", "function b() { return 2; }\n")
        target_a = self.root / "frontend/scripts/dhcnewpro/dhcemc/a.js"
        target_a.parent.mkdir(parents=True)
        target_a.write_bytes(old_content)
        request = self.request([{"path": source_a}, {"path": source_b}])
        real_write = si.atomic_write_bytes

        def flaky(path, data):
            if Path(path).name == "b.js" and Path(path).parent.name == "dhcemc":
                raise OSError("模拟替换后失败")
            return real_write(path, data)

        with mock.patch.object(si, "atomic_write_bytes", side_effect=flaky):
            result = self.apply(request, conflict_policy="backup-replace")

        self.assertFalse(result["ok"])
        self.assertEqual(result["status"], "rolled-back")
        self.assertEqual(target_a.read_bytes(), old_content)
        manifest = self.latest_manifest()
        statuses = {item["filename"]: item["status"] for item in manifest["items"]}
        self.assertEqual(statuses["a.js"], "rolled-back")
        self.assertTrue(manifest["items"][0]["backup"])
        backup_path = self.root / si.META_DIR / si.BATCHES_DIR / manifest["batch_id"] / manifest["items"][0]["backup"]
        self.assertEqual(backup_path.read_bytes(), old_content)


if __name__ == "__main__":
    unittest.main()
