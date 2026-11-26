"""交接记录存储的独立测试（仅标准库）。"""
from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest import mock

SCRIPTS_DIR = Path(__file__).resolve().parents[1] / "skill" / "source-intake" / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

import handoff_store as hs  # noqa: E402

RECORD_KEYS = {
    "schema_version",
    "record_id",
    "created_at",
    "author",
    "status",
    "summary",
    "changed_files",
    "validation",
    "next_steps",
    "open_questions",
    "source_revision",
    "material_snapshot",
}

PROCESS_WORKER = r"""
import json
import sys

sys.path.insert(0, sys.argv[1])
import handoff_store

with open(sys.argv[3], "r", encoding="utf-8") as handle:
    payload = json.load(handle)

errors = []
for index in range(payload["count"]):
    data = dict(payload["data"])
    data["summary"] = payload["prefix"] + "-" + str(index)
    try:
        handoff_store.append_handoff(sys.argv[2], data, context=payload["context"])
    except Exception as exc:
        errors.append(repr(exc))
print(json.dumps({"errors": errors}, ensure_ascii=True))
"""


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _make_dir_link(target: Path, link: Path) -> bool:
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


def _make_file_link(target: Path, link: Path) -> bool:
    try:
        os.symlink(str(target), str(link))
        return True
    except (OSError, NotImplementedError):
        return False


class HandoffTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self._tempdir = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tempdir.name)
        self.pkg = self.tmp / "需求工作包"
        self.pkg.mkdir()
        self.requirement = {
            "id": "REQ-1",
            "title": "示例需求",
            "description": "按材料实现",
        }
        self.readme_bytes = "# 需求工作包\r\n\r\n说明文本\r\n".encode("utf-8")
        self.prompt_bytes = "第一行保留缩进\r\n第二行\n第四段\r\n".encode("utf-8")
        self.attachment_bytes = b"\x89PNG\r\n\x1a\nfake-image"
        self._write_material()
        self.context = self.make_context()

    def tearDown(self) -> None:
        self._tempdir.cleanup()

    def _write_material(self) -> None:
        (self.pkg / "requirement.json").write_bytes(
            json.dumps({"requirement": self.requirement}, ensure_ascii=False).encode("utf-8")
        )
        (self.pkg / "README.md").write_bytes(self.readme_bytes)
        (self.pkg / "修改说明.md").write_bytes(self.prompt_bytes)
        attachment = self.pkg / "attachments" / "图.PNG"
        attachment.parent.mkdir()
        attachment.write_bytes(self.attachment_bytes)

    def make_context(self, **overrides):
        value = {
            "schema_version": 1,
            "directory": str(self.pkg.resolve()),
            "requirement": self.requirement,
            "prompt": self.prompt_bytes.decode("utf-8"),
            "readme": self.readme_bytes.decode("utf-8"),
            "attachments": [{
                "name": "图.PNG",
                "relative_path": "attachments/图.PNG",
                "size": len(self.attachment_bytes),
                "sha256": _sha256(self.attachment_bytes),
                "kind": "image",
            }],
            "warnings": [],
        }
        value.update(overrides)
        return value

    def handoff_data(self, **overrides):
        value = {
            "author": "执行端",
            "status": "in_progress",
            "summary": "完成初版",
            "changed_files": ["src/a.py", "附件/图 1.PNG"],
            "validation": "单元测试通过",
            "next_steps": "继续复核",
            "open_questions": "",
            "source_revision": "abc123",
            "expected_material_fingerprint": hs.material_snapshot(self.context)["fingerprint"],
        }
        value.update(overrides)
        return value

    def append(self, data=None, *, context=None):
        if data is None:
            data = self.handoff_data()
        if context is None:
            context = self.context
        return hs.append_handoff(self.pkg, data, context=context)

    def read(self, *, context=None, limit=hs.DEFAULT_LIMIT):
        if context is None:
            context = self.context
        return hs.read_handoffs(self.pkg, context=context, limit=limit)

    def record_id(self, marker: str) -> str:
        return "20260101T000000000000Z-" + marker * 32

    def write_record(self, file_id: str, *, context=None, remove=(), **overrides):
        snapshot = hs.material_snapshot(self.context if context is None else context)
        record = {
            "schema_version": 1,
            "record_id": file_id,
            "created_at": "2026-01-01T00:00:00.000000Z",
            "author": "tester",
            "status": "ready_for_review",
            "summary": f"记录 {file_id[-1]}",
            "changed_files": [],
            "validation": "",
            "next_steps": "",
            "open_questions": "",
            "source_revision": "",
            "material_snapshot": snapshot,
        }
        record.update(overrides)
        for key in remove:
            record.pop(key, None)
        path = self.pkg / "handoffs" / f"{file_id}.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(
            json.dumps(record, ensure_ascii=False, sort_keys=True).encode("utf-8")
        )
        return path, record

    def write_raw(self, name: str, data: bytes | str) -> Path:
        path = self.pkg / "handoffs" / name
        path.parent.mkdir(parents=True, exist_ok=True)
        if isinstance(data, str):
            path.write_bytes(data.encode("utf-8"))
        else:
            path.write_bytes(data)
        return path

    def package_files(self, *, include_handoffs: bool = False) -> dict[str, bytes]:
        files: dict[str, bytes] = {}
        for path in self.pkg.rglob("*"):
            if not path.is_file() or path.is_symlink():
                continue
            relative = path.relative_to(self.pkg)
            if not include_handoffs and relative.parts[0] == "handoffs":
                continue
            files[str(relative)] = path.read_bytes()
        return files


class MaterialSnapshotTests(HandoffTestCase):
    def test_fingerprint_is_stable_and_ignores_operational_keys(self):
        attachments = [
            {"relative_path": "attachments/b.txt", "sha256": "b" * 64},
            {"relative_path": "attachments/a.txt", "sha256": "a" * 64},
        ]
        first = self.make_context(
            requirement={"id": "R", "title": "T", "description": "D"},
            attachments=list(attachments),
        )
        second = self.make_context(
            requirement={"description": "D", "title": "T", "id": "R"},
            attachments=[dict(attachments[0]), dict(attachments[1])],
        )
        second["directory"] = "D:/另一个目录"
        second["schema_version"] = 999
        second["handoff"] = {"records": [{"record_id": "ignored"}]}
        second_order = [item["relative_path"] for item in second["attachments"]]

        first_snapshot = hs.material_snapshot(first)
        second_snapshot = hs.material_snapshot(second)

        self.assertEqual(set(first_snapshot), {"fingerprint", "complete"})
        self.assertRegex(first_snapshot["fingerprint"], r"^[0-9a-f]{64}$")
        self.assertIs(first_snapshot["complete"], True)
        self.assertEqual(first_snapshot, second_snapshot)
        self.assertEqual(
            [item["relative_path"] for item in second["attachments"]],
            second_order,
            "计算指纹不应修改调用方列表",
        )
        self.assertEqual(second_order, ["attachments/b.txt", "attachments/a.txt"])
        self.assertEqual(first_snapshot, hs.material_snapshot(first))

    def test_material_changes_change_fingerprint(self):
        baseline = hs.material_snapshot(self.context)["fingerprint"]
        variants = {
            "requirement": self.make_context(requirement={"id": "REQ-2"}),
            "prompt": self.make_context(prompt="新提示"),
            "readme": self.make_context(readme="新说明"),
            "attachment_path": self.make_context(attachments=[
                {"relative_path": "attachments/其他.png", "sha256": "c" * 64},
            ]),
            "attachment_content": self.make_context(attachments=[
                {"relative_path": "attachments/图.PNG", "sha256": "d" * 64},
            ]),
            "warnings": self.make_context(warnings=["材料不完整"]),
        }
        for name, context in variants.items():
            with self.subTest(name=name):
                self.assertNotEqual(
                    hs.material_snapshot(context)["fingerprint"], baseline
                )

    def test_incomplete_snapshot_when_warnings_or_attachment_hash_missing(self):
        self.assertIs(hs.material_snapshot(self.context)["complete"], True)
        cases = {
            "warnings": self.make_context(warnings=["缺少 README"]),
            "missing_key": self.make_context(attachments=[
                {"relative_path": "attachments/无哈希.txt"},
            ]),
            "none_hash": self.make_context(attachments=[
                {"relative_path": "attachments/空哈希.txt", "sha256": None},
            ]),
            "bad_hash": self.make_context(attachments=[
                {"relative_path": "attachments/坏哈希.txt", "sha256": "not-a-sha"},
            ]),
        }
        for name, context in cases.items():
            with self.subTest(name=name):
                self.assertIs(hs.material_snapshot(context)["complete"], False)
        self.assertIs(
            hs.material_snapshot(self.make_context(attachments=[]))["complete"], True
        )

    def test_context_must_be_an_object(self):
        with self.assertRaises(ValueError):
            hs.material_snapshot([])  # type: ignore[arg-type]
        with self.assertRaises(ValueError):
            hs.material_snapshot({"attachments": "not-a-list"})


class AppendReadTests(HandoffTestCase):
    def test_append_and_read_roundtrip_contract(self):
        result = self.append()

        self.assertIs(result["ok"], True)
        path = Path(result["path"])
        self.assertTrue(path.is_absolute())
        self.assertEqual(path.parent, (self.pkg / "handoffs").resolve())
        record = result["record"]
        self.assertEqual(set(record), RECORD_KEYS)
        self.assertEqual(path.name, record["record_id"] + ".json")
        self.assertEqual(json.loads(path.read_text(encoding="utf-8")), record)
        self.assertEqual(record["material_snapshot"], hs.material_snapshot(self.context))
        self.assertNotIn("expected_material_fingerprint", record)
        self.assertTrue(record["created_at"].endswith("Z"))

        read = self.read()
        self.assertEqual(read["schema_version"], 1)
        self.assertEqual(read["material_snapshot"], hs.material_snapshot(self.context))
        self.assertFalse(read["truncated"])
        self.assertEqual(read["warnings"], [])
        self.assertEqual(len(read["records"]), 1)
        returned = read["records"][0]
        self.assertEqual(set(returned), RECORD_KEYS | {"material_match"})
        self.assertEqual(returned["record_id"], record["record_id"])
        self.assertEqual(returned["material_match"], "unchanged")

    def test_minimal_data_defaults_to_empty_fields(self):
        data = {
            "author": "a",
            "status": "blocked",
            "summary": "s",
            "expected_material_fingerprint": hs.material_snapshot(self.context)["fingerprint"],
        }
        record = self.append(data)["record"]
        self.assertEqual(record["changed_files"], [])
        self.assertEqual(record["validation"], "")
        self.assertEqual(record["next_steps"], "")
        self.assertEqual(record["open_questions"], "")
        self.assertEqual(record["source_revision"], "")

    def test_empty_read_does_not_create_handoffs_or_modify_material(self):
        before = self.package_files(include_handoffs=True)
        read = self.read()
        after = self.package_files(include_handoffs=True)

        self.assertEqual(after, before)
        self.assertFalse((self.pkg / "handoffs").exists())
        self.assertEqual(read["records"], [])
        self.assertEqual(read["warnings"], [])
        self.assertFalse(read["truncated"])
        self.assertEqual(read["material_snapshot"], hs.material_snapshot(self.context))

    def test_append_preserves_original_material_bytes_and_crlf(self):
        before = self.package_files()
        self.append()
        after = self.package_files()

        self.assertEqual(after, before)
        self.assertIn(b"\r\n", after["修改说明.md"])
        self.assertIn(b"\r\n", after["README.md"])
        self.assertEqual(
            json.loads(after["requirement.json"].decode("utf-8"))["requirement"],
            self.requirement,
        )

    def test_append_allows_incomplete_snapshot_and_keeps_complete_false(self):
        incomplete = self.make_context(warnings=["缺少附件"])
        fingerprint = hs.material_snapshot(incomplete)["fingerprint"]
        data = self.handoff_data(expected_material_fingerprint=fingerprint)
        result = self.append(data, context=incomplete)

        self.assertIs(result["record"]["material_snapshot"]["complete"], False)
        read = self.read(context=incomplete)
        self.assertEqual(len(read["records"]), 1)
        self.assertEqual(read["records"][0]["material_match"], "unknown")

    def test_material_match_states(self):
        appended = self.append()["record"]

        unchanged = self.read()["records"][0]
        self.assertEqual(unchanged["material_match"], "unchanged")

        changed_context = self.make_context(prompt="材料已变化")
        changed = self.read(context=changed_context)["records"][0]
        self.assertEqual(changed["material_match"], "changed")

        incomplete = self.make_context(warnings=["材料不完整"])
        incomplete_fingerprint = hs.material_snapshot(incomplete)["fingerprint"]
        self.append(
            self.handoff_data(expected_material_fingerprint=incomplete_fingerprint),
            context=incomplete,
        )
        same_incomplete = self.read(context=incomplete)["records"]
        by_id = {item["record_id"]: item for item in same_incomplete}
        self.assertEqual(
            by_id[appended["record_id"]]["material_match"], "changed"
        )
        incomplete_record_id = next(
            record_id for record_id in by_id if record_id != appended["record_id"]
        )
        self.assertEqual(by_id[incomplete_record_id]["material_match"], "unknown")

        complete_fingerprint = hs.material_snapshot(self.context)["fingerprint"]
        forged_complete_id = self.record_id("d")
        self.write_record(
            forged_complete_id,
            material_snapshot={
                "fingerprint": incomplete_fingerprint,
                "complete": True,
            },
        )
        forged_incomplete_id = self.record_id("c")
        self.write_record(
            forged_incomplete_id,
            material_snapshot={"fingerprint": complete_fingerprint, "complete": False},
        )

        incomplete_records = {
            item["record_id"]: item for item in self.read(context=incomplete)["records"]
        }
        self.assertEqual(
            incomplete_records[forged_complete_id]["material_match"], "unknown"
        )
        complete_records = {
            item["record_id"]: item for item in self.read(context=self.context)["records"]
        }
        self.assertEqual(
            complete_records[forged_incomplete_id]["material_match"], "unknown"
        )
        self.assertEqual(
            complete_records[appended["record_id"]]["material_match"], "unchanged"
        )

        other_incomplete = self.make_context(warnings=["另一种不完整"])
        other_records = {
            item["record_id"]: item
            for item in self.read(context=other_incomplete)["records"]
        }
        self.assertEqual(other_records[incomplete_record_id]["material_match"], "changed")

    def test_fingerprint_mismatch_writes_nothing(self):
        before = self.package_files(include_handoffs=True)
        bad_data = self.handoff_data(expected_material_fingerprint="0" * 64)
        with self.assertRaises(ValueError):
            self.append(bad_data)
        self.assertEqual(self.package_files(include_handoffs=True), before)
        self.assertFalse((self.pkg / "handoffs").exists())

        handoffs = self.pkg / "handoffs"
        handoffs.mkdir()
        keep = handoffs / "keep.txt"
        keep.write_bytes(b"keep")
        with self.assertRaises(ValueError):
            self.append(bad_data)
        self.assertEqual([path.name for path in handoffs.iterdir()], ["keep.txt"])
        self.assertEqual(keep.read_bytes(), b"keep")

    def test_limit_returns_newest_records_and_sets_truncated(self):
        ids = [self.record_id(marker) for marker in ("a", "b", "c")]
        for record_id in ids:
            self.write_record(record_id)

        limited = self.read(limit=2)
        self.assertEqual(
            [item["record_id"] for item in limited["records"]], [ids[2], ids[1]]
        )
        self.assertIs(limited["truncated"], True)
        self.assertEqual(limited["warnings"], [])

        full = self.read(limit=3)
        self.assertEqual(
            [item["record_id"] for item in full["records"]],
            [ids[2], ids[1], ids[0]],
        )
        self.assertIs(full["truncated"], False)

    def test_read_rejects_invalid_limit(self):
        for limit in (0, 101, True, "5", 1.5, None):
            with self.subTest(limit=limit):
                with self.assertRaises(ValueError):
                    hs.read_handoffs(self.pkg, context=self.context, limit=limit)

    def test_read_does_not_require_requirement_file(self):
        record = self.append()["record"]
        (self.pkg / "requirement.json").unlink()
        read = self.read()
        self.assertEqual([item["record_id"] for item in read["records"]], [record["record_id"]])

    def test_append_rejects_oversized_serialized_record_before_writing(self):
        data = self.handoff_data(
            summary="汉" * 8000,
            validation="汉" * 12000,
            next_steps="汉" * 8000,
            open_questions="汉" * 8000,
        )
        with self.assertRaises(ValueError):
            self.append(data)
        self.assertFalse((self.pkg / "handoffs").exists())


class InputValidationTests(HandoffTestCase):
    def test_rejects_invalid_business_values_without_creating_handoffs(self):
        valid = self.handoff_data()
        cases = {
            "author_blank": {"author": "   "},
            "author_long": {"author": "a" * 101},
            "author_type": {"author": None},
            "status_unknown": {"status": "done"},
            "summary_blank": {"summary": ""},
            "summary_long": {"summary": "a" * 8001},
            "validation_long": {"validation": "a" * 12001},
            "next_steps_long": {"next_steps": "a" * 8001},
            "open_questions_long": {"open_questions": "a" * 8001},
            "source_revision_long": {"source_revision": "a" * 201},
            "source_revision_none": {"source_revision": None},
            "changed_files_not_list": {"changed_files": "a.py"},
            "changed_files_too_many": {
                "changed_files": [f"f{index}.py" for index in range(101)]
            },
            "changed_file_too_long": {"changed_files": ["a" * 501]},
            "changed_file_not_str": {"changed_files": [1]},
            "expected_short": {"expected_material_fingerprint": "abc"},
            "expected_type": {"expected_material_fingerprint": None},
        }
        for name, patch in cases.items():
            data = dict(valid)
            data.update(patch)
            with self.subTest(name=name):
                with self.assertRaises(ValueError):
                    self.append(data)
        self.assertFalse((self.pkg / "handoffs").exists())

    def test_rejects_unknown_missing_and_non_object_payloads(self):
        valid = self.handoff_data()
        with self.assertRaises(ValueError):
            self.append([])  # type: ignore[arg-type]

        with_unknown = dict(valid)
        with_unknown["extra"] = 1
        with self.assertRaises(ValueError):
            self.append(with_unknown)

        for key in ("author", "status", "summary", "expected_material_fingerprint"):
            missing = dict(valid)
            missing.pop(key)
            with self.subTest(missing=key):
                with self.assertRaises(ValueError):
                    self.append(missing)
        self.assertFalse((self.pkg / "handoffs").exists())

    def test_rejects_absolute_drive_backslash_and_parent_paths(self):
        invalid_paths = [
            "/abs.py",
            "C:foo.py",
            "C:/foo.py",
            "a\\b.py",
            "../outside.py",
            "a/../b.py",
            "a/./b.py",
            "a//b.py",
            ".",
            "..",
            "",
            "bad\x00name.py",
        ]
        for changed_path in invalid_paths:
            with self.subTest(path=changed_path):
                data = self.handoff_data(changed_files=[changed_path])
                with self.assertRaises(ValueError):
                    self.append(data)
        self.assertFalse((self.pkg / "handoffs").exists())

    def test_accepts_normalized_relative_paths(self):
        record = self.append(
            self.handoff_data(changed_files=["src/a.py", "文档/说明 1.md", "a-b_c.txt"])
        )["record"]
        self.assertEqual(
            record["changed_files"], ["src/a.py", "文档/说明 1.md", "a-b_c.txt"]
        )


class WorkspaceBoundaryTests(HandoffTestCase):
    def test_missing_or_file_workspace_is_not_created(self):
        missing = self.tmp / "不存在的工作包"
        data = self.handoff_data()
        with self.assertRaises(ValueError):
            hs.read_handoffs(missing, context=self.context)
        with self.assertRaises(ValueError):
            hs.append_handoff(missing, data, context=self.context)
        self.assertFalse(missing.exists())

        file_path = self.tmp / "不是目录.txt"
        file_path.write_bytes(b"x")
        with self.assertRaises(ValueError):
            hs.read_handoffs(file_path, context=self.context)
        with self.assertRaises(ValueError):
            hs.append_handoff(file_path, data, context=self.context)

        with self.assertRaises(ValueError):
            hs.append_handoff(None, data, context=self.context)  # type: ignore[arg-type]

    def test_append_requires_requirement_json(self):
        (self.pkg / "requirement.json").unlink()
        with self.assertRaises(ValueError):
            self.append()
        self.assertFalse((self.pkg / "handoffs").exists())

    def test_append_rejects_invalid_requirement_json(self):
        invalid_values = {
            "broken": b"{",
            "not_object": b"[]",
            "empty_object": b"{}",
            "bad_requirement": json.dumps({"requirement": "x"}).encode("utf-8"),
            "unrelated": json.dumps({"other": 1}).encode("utf-8"),
        }
        for name, data in invalid_values.items():
            with self.subTest(name=name):
                (self.pkg / "requirement.json").write_bytes(data)
                with self.assertRaises(ValueError):
                    self.append()
                self.assertFalse((self.pkg / "handoffs").exists())

        (self.pkg / "requirement.json").write_bytes(b'{"id": "REQ-X"}')
        self.assertIs(self.append()["ok"], True)

    def test_append_accepts_requirement_object_or_identity_keys(self):
        variants = [
            {"requirement": {}},
            {"id": "REQ-2"},
            {"title": "标题"},
            {"description": "描述"},
        ]
        for value in variants:
            (self.pkg / "requirement.json").write_bytes(
                json.dumps(value, ensure_ascii=False).encode("utf-8")
            )
            with self.subTest(value=value):
                self.assertIs(self.append()["ok"], True)

    def test_requirement_json_directory_or_link_is_rejected(self):
        (self.pkg / "requirement.json").unlink()
        (self.pkg / "requirement.json").mkdir()
        with self.assertRaises(ValueError):
            self.append()
        self.assertFalse((self.pkg / "handoffs").exists())

        (self.pkg / "requirement.json").rmdir()
        outside = self.tmp / "外部需求.json"
        outside.write_bytes(json.dumps({"id": "OUT"}).encode("utf-8"))
        if not _make_file_link(outside, self.pkg / "requirement.json"):
            self.skipTest("当前环境无法创建文件符号链接")
        with self.assertRaises(ValueError):
            self.append()
        self.assertFalse((self.pkg / "handoffs").exists())

    def test_workspace_link_is_rejected(self):
        link = self.tmp / "工作包链接"
        if not _make_dir_link(self.pkg, link):
            self.skipTest("当前环境无法创建目录链接")
        with self.assertRaises(ValueError):
            hs.read_handoffs(link, context=self.context)
        with self.assertRaises(ValueError):
            hs.append_handoff(link, self.handoff_data(), context=self.context)
        self.assertFalse((self.pkg / "handoffs").exists())

    def test_handoffs_file_is_rejected_on_write_and_warned_on_read(self):
        handoffs = self.pkg / "handoffs"
        handoffs.write_bytes(b"not-a-directory")

        with self.assertRaises(ValueError):
            self.append()
        self.assertEqual(handoffs.read_bytes(), b"not-a-directory")

        read = hs.read_handoffs(self.pkg, context=self.context)
        self.assertEqual(read["records"], [])
        self.assertTrue(any("handoffs" in warning for warning in read["warnings"]))
        self.assertIs(read["truncated"], False)
        self.assertEqual(read["material_snapshot"], hs.material_snapshot(self.context))

    def test_handoffs_link_is_rejected_on_write_and_not_followed_on_read(self):
        outside = self.tmp / "外部交接"
        outside.mkdir()
        valid_record_id = self.record_id("e")
        record = {
            "schema_version": 1,
            "record_id": valid_record_id,
            "created_at": "2026-01-01T00:00:00.000000Z",
            "author": "outside",
            "status": "in_progress",
            "summary": "外部机密记录",
            "changed_files": [],
            "validation": "",
            "next_steps": "",
            "open_questions": "",
            "source_revision": "",
            "material_snapshot": hs.material_snapshot(self.context),
        }
        (outside / f"{valid_record_id}.json").write_bytes(
            json.dumps(record, ensure_ascii=False).encode("utf-8")
        )
        link = self.pkg / "handoffs"
        if not _make_dir_link(outside, link):
            self.skipTest("当前环境无法创建目录链接")

        with self.assertRaises(ValueError):
            self.append()
        read = hs.read_handoffs(self.pkg, context=self.context)
        self.assertEqual(read["records"], [])
        self.assertTrue(any("handoffs" in warning for warning in read["warnings"]))
        self.assertNotIn("外部机密记录", json.dumps(read, ensure_ascii=False))


class CorruptAndBudgetTests(HandoffTestCase):
    def test_corrupt_unknown_and_linked_entries_warn_but_keep_valid(self):
        valid_id = self.record_id("c")
        self.write_record(valid_id)

        self.write_raw("bad.json", b"{not-json")
        self.write_record(self.record_id("b"), schema_version=2)
        self.write_record(self.record_id("a"), record_id="different-id")
        self.write_record(self.record_id("d"), remove=("validation",))
        self.write_record(self.record_id("e"), extra_field=1)
        self.write_record(
            self.record_id("f"),
            material_snapshot={"fingerprint": "not-hex", "complete": True},
        )
        self.write_record(self.record_id("0"), changed_files="not-a-list")

        nested_dir = self.pkg / "handoffs" / "nested.json"
        nested_dir.mkdir()
        nested_id = self.record_id("9")
        nested_record = {
            "schema_version": 1,
            "record_id": nested_id,
            "created_at": "2026-01-01T00:00:00.000000Z",
            "author": "nested",
            "status": "in_progress",
            "summary": "不应读取的嵌套记录",
            "changed_files": [],
            "validation": "",
            "next_steps": "",
            "open_questions": "",
            "source_revision": "",
            "material_snapshot": hs.material_snapshot(self.context),
        }
        (nested_dir / f"{nested_id}.json").write_bytes(
            json.dumps(nested_record, ensure_ascii=False).encode("utf-8")
        )
        self.write_raw("unknown.txt", b"unknown")
        self.write_raw(".handoff-dead.tmp", json.dumps(nested_record, ensure_ascii=False))

        outside = self.tmp / "外部记录.json"
        outside_record = dict(nested_record)
        outside_record["summary"] = "不应读取的外部链接记录"
        outside.write_bytes(json.dumps(outside_record, ensure_ascii=False).encode("utf-8"))
        linked = _make_file_link(outside, self.pkg / "handoffs" / "linked.json")

        read = self.read(limit=100)
        self.assertEqual(
            [item["record_id"] for item in read["records"]], [valid_id]
        )
        self.assertEqual(read["records"][0]["material_match"], "unchanged")
        joined = "\n".join(read["warnings"])
        self.assertIn("损坏", joined)
        self.assertIn("格式无效", joined)
        self.assertIn("未知目录", joined)
        self.assertIn("未知文件", joined)
        if linked:
            self.assertIn("符号链接", joined)
        dumped = json.dumps(read, ensure_ascii=False)
        self.assertNotIn("不应读取的嵌套记录", dumped)
        self.assertNotIn("不应读取的外部链接记录", dumped)
        self.assertNotIn(".handoff-dead.tmp", joined)

    def test_entry_budget_limits_scan_and_keeps_newest(self):
        ids = [self.record_id(marker) for marker in ("a", "b", "c")]
        for record_id in ids:
            self.write_record(record_id)

        with mock.patch.object(hs, "MAX_RECORDS", 2):
            read = self.read()

        self.assertEqual(
            [item["record_id"] for item in read["records"]], [ids[2], ids[1]]
        )
        self.assertIs(read["truncated"], True)
        self.assertTrue(any("上限" in warning for warning in read["warnings"]))

    def test_total_read_budget_stops_and_warns(self):
        ids = [self.record_id(marker) for marker in ("a", "b", "c")]
        paths = [self.write_record(record_id)[0] for record_id in ids]
        budget = paths[2].stat().st_size

        with mock.patch.object(hs, "MAX_TOTAL_READ_BYTES", budget):
            read = self.read()

        self.assertEqual([item["record_id"] for item in read["records"]], [ids[2]])
        self.assertIs(read["truncated"], True)
        self.assertTrue(any("预算" in warning for warning in read["warnings"]))

    def test_oversized_json_is_skipped_with_warning(self):
        valid_id = self.record_id("a")
        self.write_record(valid_id)
        self.write_raw("huge.json", b"x" * (64 * 1024 + 1))

        read = self.read()
        self.assertEqual([item["record_id"] for item in read["records"]], [valid_id])
        self.assertTrue(any("64 KiB" in warning for warning in read["warnings"]))

    def test_append_rejects_when_capacity_is_full(self):
        ids = [self.record_id(marker) for marker in ("a", "b")]
        for record_id in ids:
            self.write_record(record_id)

        with mock.patch.object(hs, "MAX_RECORDS", 2):
            with self.assertRaises(ValueError):
                self.append()

        handoffs = self.pkg / "handoffs"
        self.assertEqual(
            sorted(path.name for path in handoffs.iterdir()),
            sorted(f"{record_id}.json" for record_id in ids),
        )
        self.assertEqual(list(handoffs.glob("*.tmp")), [])

    def test_append_rejects_when_capacity_scan_budget_is_exhausted(self):
        self.write_record(self.record_id("a"))
        with mock.patch.object(hs, "MAX_TOTAL_READ_BYTES", 1):
            with self.assertRaises(ValueError):
                self.append()
        handoffs = self.pkg / "handoffs"
        self.assertEqual(len(list(handoffs.glob("*.json"))), 1)
        self.assertEqual(list(handoffs.glob("*.tmp")), [])


class AtomicityTests(HandoffTestCase):
    def test_link_failure_leaves_no_record_or_temp(self):
        with mock.patch.object(hs.os, "link", side_effect=OSError("link unsupported")):
            with self.assertRaises(OSError):
                self.append()
        handoffs = self.pkg / "handoffs"
        self.assertEqual(list(handoffs.iterdir()), [])

    def test_link_not_implemented_fails_explicitly(self):
        with mock.patch.object(hs.os, "link", side_effect=NotImplementedError("no link")):
            with self.assertRaises(OSError):
                self.append()
        self.assertEqual(list((self.pkg / "handoffs").iterdir()), [])

    def test_open_failure_leaves_no_temp(self):
        real_open = open

        def failing_open(file, mode="r", *args, **kwargs):
            if "x" in mode:
                raise OSError("disk full")
            return real_open(file, mode, *args, **kwargs)

        with mock.patch("builtins.open", side_effect=failing_open):
            with self.assertRaises(OSError):
                self.append()
        handoffs = self.pkg / "handoffs"
        self.assertEqual(list(handoffs.iterdir()), [])

    def test_fsync_failure_cleans_its_own_temp(self):
        with mock.patch.object(hs.os, "fsync", side_effect=OSError("fsync failed")):
            with self.assertRaises(OSError):
                self.append()
        handoffs = self.pkg / "handoffs"
        self.assertEqual(list(handoffs.iterdir()), [])

    def test_collision_retries_without_overwriting_existing_target(self):
        real_link = os.link
        collision: dict[str, Path] = {}

        def racing_link(source, target, *args, **kwargs):
            target_path = Path(target)
            if "path" not in collision:
                collision["path"] = target_path
                target_path.write_bytes(b"existing-content")
                raise FileExistsError(str(target_path))
            return real_link(source, target, *args, **kwargs)

        with mock.patch.object(hs.os, "link", side_effect=racing_link):
            result = self.append()

        collision_path = collision["path"]
        self.assertEqual(collision_path.read_bytes(), b"existing-content")
        self.assertNotEqual(Path(result["path"]), collision_path)
        self.assertTrue(Path(result["path"]).is_file())
        handoffs = self.pkg / "handoffs"
        self.assertEqual(len(list(handoffs.glob("*.json"))), 2)
        self.assertEqual(list(handoffs.glob("*.tmp")), [])

    def test_repeated_collision_fails_and_keeps_other_files(self):
        handoffs = self.pkg / "handoffs"
        handoffs.mkdir()
        keep = handoffs / "keep.txt"
        keep.write_bytes(b"keep")

        with mock.patch.object(hs.os, "link", side_effect=FileExistsError("exists")):
            with self.assertRaises(OSError):
                self.append()

        self.assertEqual(keep.read_bytes(), b"keep")
        self.assertEqual(
            sorted(path.name for path in handoffs.iterdir()), ["keep.txt"]
        )

    def test_reader_ignores_tmp_files(self):
        record_id = self.record_id("a")
        path, _record = self.write_record(record_id)
        temp_path = path.with_name(".handoff-test.tmp")
        path.rename(temp_path)

        read = self.read()
        self.assertEqual(read["records"], [])
        self.assertEqual(read["warnings"], [])
        self.assertIs(read["truncated"], False)


class ConcurrencyTests(HandoffTestCase):
    def test_threads_append_without_losing_records(self):
        base = self.handoff_data()

        def worker(index: int) -> str:
            data = dict(base)
            data["summary"] = f"线程记录 {index}"
            return hs.append_handoff(
                self.pkg, data, context=self.context,
            )["path"]

        with ThreadPoolExecutor(max_workers=8) as pool:
            paths = list(pool.map(worker, range(16)))

        self.assertEqual(len(set(paths)), 16)
        read = self.read(limit=100)
        self.assertFalse(read["truncated"])
        self.assertEqual(len(read["records"]), 16)
        self.assertEqual(
            {record["summary"] for record in read["records"]},
            {f"线程记录 {index}" for index in range(16)},
        )
        self.assertEqual(list((self.pkg / "handoffs").glob("*.tmp")), [])

    def test_processes_append_without_losing_records(self):
        count = 3
        per_process = 3
        base = self.handoff_data()
        payload_paths = []
        for index in range(count):
            payload = {
                "count": per_process,
                "prefix": f"进程记录 {index}",
                "data": base,
                "context": self.context,
            }
            path = self.tmp / f"worker-{index}.json"
            path.write_bytes(
                json.dumps(payload, ensure_ascii=False).encode("utf-8")
            )
            payload_paths.append(path)

        env = dict(
            os.environ,
            PYTHONDONTWRITEBYTECODE="1",
            PYTHONIOENCODING="utf-8",
        )
        processes = [
            subprocess.Popen(
                [
                    sys.executable,
                    "-B",
                    "-c",
                    PROCESS_WORKER,
                    str(SCRIPTS_DIR),
                    str(self.pkg),
                    str(payload_path),
                ],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                env=env,
            )
            for payload_path in payload_paths
        ]
        outputs = []
        for process in processes:
            stdout, stderr = process.communicate(timeout=120)
            self.assertEqual(
                process.returncode,
                0,
                stderr.decode("utf-8", errors="replace"),
            )
            outputs.append(json.loads(stdout.decode("utf-8")))

        for output in outputs:
            self.assertEqual(output["errors"], [])

        read = self.read(limit=100)
        self.assertFalse(read["truncated"])
        self.assertEqual(len(read["records"]), count * per_process)
        expected = {
            f"进程记录 {process}-{index}"
            for process in range(count)
            for index in range(per_process)
        }
        self.assertEqual(
            {record["summary"] for record in read["records"]}, expected
        )
        self.assertEqual(list((self.pkg / "handoffs").glob("*.tmp")), [])


if __name__ == "__main__":
    unittest.main()
