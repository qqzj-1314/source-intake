"""Package committed product files; never recursively archive the working folder."""
from __future__ import annotations

import hashlib
import io
import json
import subprocess
import tarfile
import zipfile
from pathlib import Path


def build() -> Path:
    root = Path(__file__).resolve().parents[1]
    version = json.loads((root / "plugin/package.json").read_text(encoding="utf-8"))["version"]
    dirty = subprocess.check_output(["git", "status", "--porcelain", "--untracked-files=no"], cwd=root)
    if dirty.strip():
        raise SystemExit("Commit tracked changes before packaging.")
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
            target.writestr(prefix + item.name, data)
            files[item.name] = hashlib.sha256(data).hexdigest()
        target.writestr(prefix + "RELEASE.json", json.dumps({"version": version, "commit": commit, "files": files}, indent=2))
    digest = hashlib.sha256(release.read_bytes()).hexdigest()
    release.with_suffix(".zip.sha256").write_text(f"{digest}  {release.name}\n", encoding="ascii")
    print(json.dumps({"archive": str(release), "sha256": digest, "files": len(files), "commit": commit}))
    return release


if __name__ == "__main__":
    build()
