"""需求工作包上下文读取器的独立测试（仅标准库）。"""
from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parents[1] / "skill" / "source-intake" / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

import requirement_context as rc  # noqa: E402

SCRIPT = SCRIPTS_DIR / "requirement_context.py"


class RequirementContextTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tempdir = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tempdir.name)
        self.pkg = self.tmp / "需求工作包"
        self.pkg.mkdir()

    def tearDown(self) -> None:
        self._tempdir.cleanup()

    def write(self, relative: str, data: bytes | str) -> Path:
        path = self.pkg / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        if isinstance(data, str):
            path.write_bytes(data.encode("utf-8"))
        else:
            path.write_bytes(data)
        return path

    def cli(self, directory: Path | None = None) -> subprocess.CompletedProcess:
        env = dict(os.environ, PYTHONDONTWRITEBYTECODE="1", PYTHONIOENCODING="utf-8")
        return subprocess.run(
            [sys.executable, "-B", "-X", "utf8", str(SCRIPT), "--directory", str(directory or self.pkg)],
            capture_output=True,
            env=env,
        )

    def test_reads_bom_crlf_prompt_nested_requirement_and_later_attachments(self):
        readme = "# 需求工作包\n\n这是说明。\n"
        prompt = "  第一行保留缩进\r\n第二行\n第四段\r\n"
        requirement = {"id": "REQ-1", "title": "示例需求", "description": "按材料实现"}
        self.write("README.md", b"\xef\xbb\xbf" + readme.encode("utf-8"))
        self.write("修改说明.md", b"\xef\xbb\xbf" + prompt.encode("utf-8"))
        self.write("requirement.json", json.dumps(
            {"version": 1, "requirement": requirement, "attachments": []},
            ensure_ascii=False,
        ))
        image = b"\x89PNG\r\n\x1a\nfake-image"
        self.write("attachments/初稿图.PNG", image)

        before = {
            str(path.relative_to(self.pkg)): path.read_bytes()
            for path in self.pkg.rglob("*") if path.is_file()
        }
        first = rc.read_context(self.pkg)
        after = {
            str(path.relative_to(self.pkg)): path.read_bytes()
            for path in self.pkg.rglob("*") if path.is_file()
        }
        self.assertEqual(after, before, "读取上下文不应创建或改写工作包内容")
        self.assertEqual(set(first), {
            "schema_version", "directory", "requirement", "prompt",
            "readme", "attachments", "warnings",
        })
        self.assertEqual(first["schema_version"], 1)
        self.assertEqual(first["directory"], str(self.pkg.resolve()))
        self.assertEqual(first["requirement"], requirement)
        self.assertEqual(first["prompt"], prompt, "提示词必须原样保留 CRLF")
        self.assertEqual(first["readme"], readme)
        self.assertEqual(first["warnings"], [])
        self.assertEqual(len(first["attachments"]), 1)
        self.assertEqual(first["attachments"][0]["relative_path"], "attachments/初稿图.PNG")
        self.assertEqual(first["attachments"][0]["kind"], "image")
        self.assertEqual(first["attachments"][0]["sha256"], hashlib.sha256(image).hexdigest())

        later = b"%PDF-1.7\nlater material"
        self.write("attachments/后来补充/材料.pdf", later)
        second = rc.read_context(self.pkg)
        by_path = {item["relative_path"]: item for item in second["attachments"]}
        self.assertEqual(set(by_path), {"attachments/初稿图.PNG", "attachments/后来补充/材料.pdf"})
        self.assertEqual(by_path["attachments/后来补充/材料.pdf"]["kind"], "document")
        self.assertEqual(by_path["attachments/后来补充/材料.pdf"]["size"], len(later))
        self.assertEqual(by_path["attachments/后来补充/材料.pdf"]["sha256"], hashlib.sha256(later).hexdigest())

    def test_external_directory_junction_and_file_symlink_are_not_followed(self):
        outside = self.tmp / "工作包外"
        outside.mkdir()
        secret = outside / "机密.txt"
        secret.write_text("外部机密内容", encoding="utf-8")
        self.write("attachments/正常附件.txt", b"inside")
        link = self.pkg / "attachments" / "外链目录"
        if os.name == "nt":
            created = subprocess.run(
                ["cmd", "/c", "mklink", "/J", str(link), str(outside)],
                capture_output=True,
            ).returncode == 0 and link.exists()
        else:
            try:
                os.symlink(outside, link, target_is_directory=True)
                created = True
            except OSError:
                created = False
        if not created:
            self.skipTest("当前环境无法创建目录链接")
        file_link = self.pkg / "attachments" / "外链文件.txt"
        try:
            os.symlink(secret, file_link)
        except OSError:
            pass

        context = rc.read_context(self.pkg)
        self.assertEqual(
            [item["relative_path"] for item in context["attachments"]],
            ["attachments/正常附件.txt"],
        )
        dumped = json.dumps(context, ensure_ascii=False)
        self.assertNotIn("机密.txt", dumped)
        self.assertNotIn("外部机密内容", dumped)
        self.assertTrue(any("链接" in warning or "junction" in warning for warning in context["warnings"]))

    def test_invalid_requirement_json_returns_error_json_and_exit_one(self):
        self.write("requirement.json", '{"requirement": {')
        context = rc.read_context(self.pkg)
        self.assertIs(context.get("ok"), False)
        self.assertEqual(context["error"]["type"], "InvalidRequirementJson")
        self.assertIn("JSON", context["error"]["message"])

        process = self.cli()
        self.assertEqual(process.returncode, 1, process.stderr.decode("utf-8", errors="replace"))
        payload = json.loads(process.stdout.decode("utf-8"))
        self.assertIs(payload.get("ok"), False)
        self.assertEqual(payload["schema_version"], 1)
        self.assertEqual(payload["error"]["type"], "InvalidRequirementJson")
        self.assertIn("JSON", payload["error"]["message"])

    def test_missing_directory_and_file_path_are_fatal_errors(self):
        missing = self.tmp / "不存在的需求包"
        context = rc.read_context(missing)
        self.assertIs(context.get("ok"), False)
        self.assertEqual(context["error"]["type"], "DirectoryNotFound")
        self.assertFalse(missing.exists(), "错误路径不能被创建")
        self.assertEqual(self.cli(missing).returncode, 1)

        file_path = self.tmp / "不是目录.txt"
        file_path.write_text("file", encoding="utf-8")
        context = rc.read_context(file_path)
        self.assertIs(context.get("ok"), False)
        self.assertEqual(context["error"]["type"], "NotADirectory")
        process = self.cli(file_path)
        self.assertEqual(process.returncode, 1)
        payload = json.loads(process.stdout.decode("utf-8"))
        self.assertEqual(payload["error"]["type"], "NotADirectory")

    def test_missing_optional_files_are_warnings_and_exit_zero(self):
        context = rc.read_context(self.pkg)
        self.assertNotIn("ok", context)
        self.assertEqual(context["requirement"], {})
        self.assertEqual(context["prompt"], "")
        self.assertEqual(context["readme"], "")
        self.assertEqual(context["attachments"], [])
        joined = "\n".join(context["warnings"])
        for name in ("README.md", "修改说明.md", "requirement.json", "attachments"):
            self.assertIn(name, joined)

        process = self.cli()
        self.assertEqual(process.returncode, 0, process.stderr.decode("utf-8", errors="replace"))
        payload = json.loads(process.stdout.decode("utf-8"))
        self.assertEqual(payload["warnings"], context["warnings"])

    def test_body_size_attachment_count_and_hash_limits(self):
        self.write("修改说明.md", b"a" * (rc.MAX_BODY_BYTES + 10))
        self.write("requirement.json", '{"requirement": {"id": "REQ-CAP"}}')
        large = self.pkg / "attachments" / "000_large.bin"
        large.parent.mkdir()
        with open(large, "wb") as handle:
            handle.truncate(rc.MAX_HASH_BYTES + 1)
        for index in range(201):
            self.write(f"attachments/f{index:03}.txt", b"x")

        context = rc.read_context(self.pkg)
        self.assertEqual(len(context["prompt"]), rc.MAX_BODY_BYTES)
        self.assertEqual(len(context["attachments"]), rc.MAX_ATTACHMENTS)
        by_name = {item["name"]: item for item in context["attachments"]}
        self.assertEqual(by_name["000_large.bin"]["size"], rc.MAX_HASH_BYTES + 1)
        self.assertIsNone(by_name["000_large.bin"]["sha256"])
        self.assertEqual(by_name["f000.txt"]["sha256"], hashlib.sha256(b"x").hexdigest())
        joined = "\n".join(context["warnings"])
        self.assertIn("已截断", joined)
        self.assertIn("64 MiB", joined)
        self.assertIn("上限 200", joined)


if __name__ == "__main__":
    unittest.main()
