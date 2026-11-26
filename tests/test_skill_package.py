import hashlib
import importlib.util
import json
import os
import subprocess
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path

from common import make_dir_link

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "build-skill.py"
_spec = importlib.util.spec_from_file_location("build_skill_module", SCRIPT)
build_module = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(build_module)
build_skill = build_module.build_skill


class SkillPackageTests(unittest.TestCase):
    def test_portable_skill_preserves_license_bytes(self):
        self.write("SKILL.md", self.skill_md())
        license_bytes = b"MIT License\nCopyright (c) fixture\n"
        self.write("LICENSE", license_bytes)
        result = build_skill(self.source, self.output, "1.0.0")
        with zipfile.ZipFile(result["skill"]) as archive:
            self.assertEqual(archive.read("demo-skill/LICENSE"), license_bytes)

    def test_skill_artifact_never_overwrites_full_project_archive(self):
        self.write("SKILL.md", self.skill_md())
        self.output.mkdir(parents=True, exist_ok=True)
        full_project = self.output / 'source-intake-1.2.3.zip'
        full_project.write_bytes(b'full project archive sentinel')
        result = build_skill(self.source, self.output, '1.2.3')
        self.assertEqual(full_project.read_bytes(), b'full project archive sentinel')
        self.assertNotEqual(Path(result['zip']), full_project)
        self.assertEqual(Path(result['skill_sha256_file']).read_text(encoding='ascii'), f"{result['sha256']}  {Path(result['skill']).name}\n")

    def setUp(self) -> None:
        self._tempdir = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tempdir.name)
        self.source = self.tmp / "skill-src"
        self.source.mkdir()
        self.output = self.tmp / "out"

    def tearDown(self) -> None:
        self._tempdir.cleanup()

    def write(self, relative: str, data: bytes | str) -> Path:
        path = self.source / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data.encode("utf-8") if isinstance(data, str) else data)
        return path

    def skill_md(self, name: str = "demo-skill", description: str = "演示技能，保留 Unicode 内容。") -> str:
        return f"---\nname: {name}\ndescription: {description}\n---\n\n# Demo 标题\n"

    def names(self, path: str | Path) -> list[str]:
        with zipfile.ZipFile(path) as archive:
            return archive.namelist()

    def test_archive_layout_and_raw_bytes_are_preserved(self):
        skill = self.skill_md()
        self.write("SKILL.md", skill)
        self.write("agents/openai.yaml", "interface:\n  display_name: \"演示\"\n")
        self.write("references/cli.md", "# 文档\r\n中文行\r\n")
        payload = bytes(range(256)) + b"\r\n\x00\xff"
        self.write("references/sample.bin", payload)
        self.write("scripts/tool.py", "print('中文')\r\n")

        result = build_skill(self.source, self.output, "1.2.3")
        skill_path = Path(result["skill"])
        zip_path = Path(result["zip"])
        self.assertEqual(skill_path.name, "source-intake-skill-1.2.3.skill")
        self.assertEqual(zip_path.name, "source-intake-skill-1.2.3.zip")
        self.assertEqual(result["files"], 5)
        self.assertEqual(skill_path.read_bytes(), zip_path.read_bytes())

        digest = hashlib.sha256(skill_path.read_bytes()).hexdigest()
        self.assertEqual(result["sha256"], digest)
        sha_path = Path(result["sha256_file"])
        self.assertEqual(sha_path.read_text(encoding="ascii"), f"{digest}  {zip_path.name}\n")

        expected = {
            "demo-skill/SKILL.md",
            "demo-skill/agents/openai.yaml",
            "demo-skill/references/cli.md",
            "demo-skill/references/sample.bin",
            "demo-skill/scripts/tool.py",
        }
        names = self.names(skill_path)
        self.assertEqual(set(names), expected)
        self.assertEqual(len(names), len(expected))
        self.assertEqual({name.split("/")[0] for name in names}, {"demo-skill"})
        self.assertTrue(all("\\" not in name and not name.startswith("/") for name in names))
        with zipfile.ZipFile(skill_path) as archive:
            self.assertEqual(archive.read("demo-skill/SKILL.md"), skill.encode("utf-8"))
            self.assertEqual(archive.read("demo-skill/references/sample.bin"), payload)
            self.assertEqual(archive.read("demo-skill/references/cli.md"), "# 文档\r\n中文行\r\n".encode("utf-8"))
            self.assertEqual(archive.read("demo-skill/scripts/tool.py"), "print('中文')\r\n".encode("utf-8"))

    def test_invalid_frontmatter_is_rejected(self):
        cases = {
            "missing-skill": None,
            "bad-name": "---\nname: Bad_Name\ndescription: 描述\n---\n",
            "double-hyphen": "---\nname: bad--name\ndescription: 描述\n---\n",
            "no-name": "---\ndescription: 只有描述\n---\n",
            "no-description": "---\nname: demo-skill\n---\n",
            "unclosed": "---\nname: demo-skill\ndescription: 描述\n",
        }
        for label, content in cases.items():
            with self.subTest(case=label):
                source = self.tmp / label
                source.mkdir()
                if content is not None:
                    (source / "SKILL.md").write_text(content, encoding="utf-8")
                out = self.tmp / f"out-{label}"
                with self.assertRaises(ValueError):
                    build_skill(source, out, "1.0.0")
                self.assertFalse(out.exists())

    def test_generated_hidden_and_vendor_files_are_excluded(self):
        self.write("SKILL.md", self.skill_md())
        self.write("scripts/tool.py", "ok")
        self.write("scripts/__pycache__/tool.cpython-311.pyc", b"pyc")
        self.write("scripts/legacy.pyc", b"pyc")
        self.write("scripts/node_modules/pkg/index.js", "vendor")
        self.write("scripts/.source-intake/batches/b.json", "{}")
        self.write("agents/.tmp/secret.txt", "secret")
        self.write("agents/.DS_Store", b"x")

        result = build_skill(self.source, self.output, "1.0.0")
        self.assertEqual(
            set(self.names(result["skill"])),
            {"demo-skill/SKILL.md", "demo-skill/scripts/tool.py"},
        )

    def test_links_are_not_followed(self):
        self.write("SKILL.md", self.skill_md())
        self.write("references/cli.md", "ok")
        self.write("scripts/ok.py", "ok")
        outside = self.tmp / "outside"
        outside.mkdir()
        (outside / "secret.txt").write_text("SECRET", encoding="utf-8")
        if not make_dir_link(outside, self.source / "references" / "external"):
            self.skipTest("directory links unavailable")
        try:
            os.symlink(str(outside / "secret.txt"), str(self.source / "scripts" / "leak.py"))
        except (OSError, NotImplementedError):
            pass

        result = build_skill(self.source, self.output, "1.0.0")
        names = self.names(result["skill"])
        self.assertEqual(
            set(names),
            {"demo-skill/SKILL.md", "demo-skill/references/cli.md", "demo-skill/scripts/ok.py"},
        )
        self.assertFalse(any("secret" in name or "external" in name or "leak" in name for name in names))

    def test_output_inside_source_is_never_archived(self):
        self.write("SKILL.md", self.skill_md())
        self.write("scripts/tool.py", "ok")
        output = self.source / "scripts" / "out"

        first = build_skill(self.source, output, "3.0.0")
        second = build_skill(self.source, output, "3.0.0")
        expected = {"demo-skill/SKILL.md", "demo-skill/scripts/tool.py"}
        self.assertEqual(set(self.names(first["skill"])), expected)
        self.assertEqual(set(self.names(second["skill"])), expected)
        self.assertEqual(first["sha256"], second["sha256"])
        self.assertTrue((output / "source-intake-skill-3.0.0.skill").is_file())

    def test_cli_builds_portable_archives_with_verifiable_hash(self):
        self.write("SKILL.md", self.skill_md())
        self.write("scripts/tool.py", "print('ok')")
        command = [
            sys.executable, "-B", "-X", "utf8", str(SCRIPT),
            "--source", str(self.source), "--output", str(self.output), "--version", "2.0.0",
        ]
        process = subprocess.run(command, capture_output=True)
        self.assertEqual(process.returncode, 0, process.stderr.decode("utf-8", "replace"))
        result = json.loads(process.stdout.decode("utf-8"))
        skill_path = Path(result["skill"])
        zip_path = Path(result["zip"])
        self.assertTrue(skill_path.is_file())
        self.assertTrue(zip_path.is_file())
        digest = hashlib.sha256(skill_path.read_bytes()).hexdigest()
        self.assertEqual(result["sha256"], digest)
        self.assertEqual(skill_path.read_bytes(), zip_path.read_bytes())
        self.assertEqual(Path(result["sha256_file"]).read_text(encoding="ascii"), f"{digest}  {zip_path.name}\n")

        missing_version = subprocess.run(command[:-2], capture_output=True)
        self.assertNotEqual(missing_version.returncode, 0)


if __name__ == "__main__":
    unittest.main()
