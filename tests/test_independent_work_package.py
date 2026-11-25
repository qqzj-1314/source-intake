"""Independent acceptance: observable files from requirement-only workflows."""
import json
from pathlib import Path
from unittest.mock import patch

from common import IntakeTestCase, si, make_dir_link


class RequirementPackageAcceptance(IntakeTestCase):
    def test_automatic_package_identity_survives_preview_and_apply(self):
        request = self.bundle(work_package={})
        plan = self.build_plan(request)
        self.assertTrue(self.apply(request, expected_plan=plan)["ok"])

    def test_requirements_junction_cannot_escape_project(self):
        self.root.mkdir()
        outside = self.tmp / "outside"
        outside.mkdir()
        if not make_dir_link(outside, self.root / "requirements"):
            self.skipTest("Directory links unavailable")
        with self.assertRaises(si.ValidationError):
            self.build_plan(self.bundle())
        self.assertEqual(list(outside.iterdir()), [])

    def bundle(self, sources=None, **overrides):
        request = self.request(sources or [], requirement=self.requirement(
            id="QA-图片文档", description="依据截图和文档实现需求。",
            prompt="  额外要求：保留原接口。\r\n先读材料，再修改。\n",
        ))
        request["work_package"] = {"token": "independent-qa"}
        request.update(overrides)
        return request

    def package_dir(self):
        dirs = list((self.root / "requirements").iterdir())
        self.assertEqual(len(dirs), 1)
        return dirs[0]

    def test_description_only_saves_verbatim_prompt_and_reading_entry(self):
        request = self.bundle()
        plan = self.build_plan(request)
        self.assertFalse(self.root.exists(), "preview must not create the destination")
        result = self.apply(request, expected_plan=plan)
        self.assertTrue(result["ok"], result)
        package = self.package_dir()
        self.assertTrue((package / "attachments").is_dir())
        self.assertEqual((package / "修改说明.md").read_bytes(), request["requirement"]["prompt"].encode())
        requirement = json.loads((package / "requirement.json").read_text(encoding="utf-8"))
        requirement = requirement.get("requirement", requirement)
        self.assertEqual(requirement["description"], request["requirement"]["description"])
        readme = (package / "README.md").read_text(encoding="utf-8")
        self.assertIn("修改说明.md", readme)
        self.assertIn("attachments", readme)

    def test_binary_document_and_image_are_copied_without_reinterpretation(self):
        original = b'\x89PNG\r\n\x1a\n\x00\xff\xfe\x80binary\x00'
        image = self.source_dir / "需求截图.png"
        image.write_bytes(original)
        document = self.source_dir / "需求.docx"
        document.write_bytes(b'PK\x03\x04\x00\xffdocument\x00')
        request = self.bundle([{"path": str(image), "role": "attachment"}, {"path": str(document), "role": "attachment"}])
        result = self.apply(request, expected_plan=self.build_plan(request))
        self.assertTrue(result["ok"], result)
        package = self.package_dir()
        self.assertEqual((package / "attachments" / image.name).read_bytes(), original)
        self.assertEqual((package / "attachments" / document.name).read_bytes(), document.read_bytes())

    def test_attachment_changed_after_preview_rejects_entire_batch(self):
        source = self.source_dir / "requirement.pdf"
        source.write_bytes(b'%PDF-1.7\nold')
        request = self.bundle([{"path": str(source), "role": "attachment"}])
        plan = self.build_plan(request)
        source.write_bytes(b'%PDF-1.7\nchanged')
        try:
            result = self.apply(request, expected_plan=plan)
        except si.PlanMismatchError:
            pass
        else:
            self.assertFalse(result["ok"], result)
        self.assertFalse(self.root.exists())

    def test_new_request_keeps_previously_created_materials(self):
        first = self.bundle()
        self.assertTrue(self.apply(first, expected_plan=self.build_plan(first))["ok"])
        previous = self.package_dir()
        manual = previous / "attachments" / "用户后放的文件.txt"
        manual.write_text("手工材料", encoding="utf-8")
        before = {str(p.relative_to(previous)): p.read_bytes() for p in previous.rglob('*') if p.is_file()}
        second = self.bundle(work_package={"token": "independent-second"})
        self.assertTrue(self.apply(second, expected_plan=self.build_plan(second))["ok"])
        after = {str(p.relative_to(previous)): p.read_bytes() for p in previous.rglob('*') if p.is_file()}
        self.assertEqual(after, before)
        self.assertEqual(len(list((self.root / "requirements").iterdir())), 2)

    def test_source_write_failure_does_not_leave_a_finished_requirement_package(self):
        source = self.write_source("demo.js", "var example = 1;\n")
        request = self.bundle([{"path": source}])
        plan = self.build_plan(request)
        writer = si.atomic_write_bytes
        target = self.root / "frontend/scripts/dhcnewpro/dhcemc/demo.js"

        def fail_source(path, data):
            if Path(path).resolve() == target.resolve():
                raise OSError("injected target write failure")
            return writer(path, data)

        with patch.object(si, "atomic_write_bytes", side_effect=fail_source):
            result = self.apply(request, expected_plan=plan)
        self.assertFalse(result["ok"])
        self.assertFalse(target.exists())
        packages = self.root / "requirements"
        self.assertFalse(packages.exists() and any(packages.iterdir()), "failed batch left a completed-looking requirement package")

    def test_package_write_failure_rolls_back_sources_and_partial_package(self):
        source = self.write_source("demo.js", "var example = 2;\n")
        request = self.bundle([{"path": source}])
        plan = self.build_plan(request)
        writer = si.atomic_write_bytes

        def fail_prompt(path, data):
            path = Path(path)
            if path.name == "修改说明.md" and "requirements" in path.parts:
                raise OSError("injected package write failure")
            return writer(path, data)

        with patch.object(si, "atomic_write_bytes", side_effect=fail_prompt):
            result = self.apply(request, expected_plan=plan)
        self.assertFalse(result["ok"])
        self.assertFalse((self.root / "frontend/scripts/dhcnewpro/dhcemc/demo.js").exists())
        packages = self.root / "requirements"
        self.assertFalse(packages.exists() and any(packages.iterdir()), "failed batch left partial requirement files")
