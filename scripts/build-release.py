"""Package committed product files; never recursively archive the working folder."""
from __future__ import annotations

import hashlib
import importlib.util
import io
import json
import subprocess
import tarfile
import tempfile
import zipfile
from pathlib import Path


def add_file(archive, name, data):
    info = zipfile.ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0))
    info.create_system = 3
    info.external_attr = 0o644 << 16
    info.compress_type = zipfile.ZIP_DEFLATED
    archive.writestr(info, data)


def build() -> Path:
    root = Path(__file__).resolve().parents[1]
    version = json.loads((root / "plugin/package.json").read_text(encoding="utf-8"))["version"]
    native_manifest = root / ".claude-plugin/plugin.json"
    if native_manifest.exists() and json.loads(native_manifest.read_text(encoding="utf-8"))["version"] != version:
        raise SystemExit("Claude plugin manifest version must match plugin/package.json.")
    dirty = subprocess.check_output(["git", "status", "--porcelain", "--untracked-files=no"], cwd=root)
    if dirty.strip():
        raise SystemExit("Commit tracked changes before packaging.")
    try:
        subprocess.run(["node", str(root / "scripts/verify-standalone.mjs")], cwd=root, check=True, capture_output=True, text=True, encoding="utf-8")
    except (OSError, subprocess.CalledProcessError) as exc:
        detail = getattr(exc, "stdout", "") or str(exc)
        raise SystemExit("Standalone runtime verification failed; build and commit fresh artifacts first.\n" + detail)
    commit = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=root, text=True).strip()
    archive = subprocess.check_output(["git", "archive", "--format=tar", "HEAD"], cwd=root)
    output = root / "dist"
    output.mkdir(exist_ok=True)
    release = output / f"source-intake-{version}.zip"
    prefix = f"source-intake-{version}/"
    files = {}
    with tarfile.open(fileobj=io.BytesIO(archive)) as source, zipfile.ZipFile(release, "w", zipfile.ZIP_DEFLATED) as target:
        for item in source.getmembers():
            if not item.isfile():
                continue
            path = Path(item.name)
            if any(part in {"node_modules", ".local", ".git", ".source-intake", "__pycache__"} for part in path.parts):
                raise SystemExit(f"Unexpected private/generated path in Git: {item.name}")
            data = source.extractfile(item).read()
            add_file(target, prefix + item.name, data)
            files[item.name] = hashlib.sha256(data).hexdigest()
        add_file(target, prefix + "RELEASE.json", json.dumps({"version": version, "commit": commit, "files": files}, indent=2).encode("utf-8"))
    digest = hashlib.sha256(release.read_bytes()).hexdigest()
    release.with_suffix(".zip.sha256").write_bytes(f"{digest}  {release.name}\n".encode("ascii"))
    spec = importlib.util.spec_from_file_location("build_skill_artifact", root / "scripts/build-skill.py")
    skill_builder = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(skill_builder)
    with tempfile.TemporaryDirectory(prefix="source-intake-release-skill-") as stage:
        staged_skill = Path(stage) / "source-intake"
        with tarfile.open(fileobj=io.BytesIO(archive)) as source:
            for item in source.getmembers():
                if not item.isfile() or not item.name.startswith("skill/source-intake/"):
                    continue
                relative = Path(item.name.removeprefix("skill/source-intake/"))
                if relative.is_absolute() or ".." in relative.parts:
                    raise SystemExit("Unsafe Skill archive path")
                target = staged_skill / relative
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(source.extractfile(item).read())
        skill = skill_builder.build_skill(staged_skill, output, version)
    print(json.dumps({"archive": str(release), "sha256": digest, "files": len(files), "commit": commit, "skill": skill}))
    return release


if __name__ == "__main__":
    build()
