"""Verify source provenance, JSON artifacts and public engineering boundaries."""

import hashlib
import json
import subprocess
from pathlib import Path

root = Path(__file__).resolve().parents[2]
vendor = root / "vendor/weknora-docreader"
provenance = json.loads((vendor / "provenance.json").read_text())
assert hashlib.sha256((vendor / "LICENSE").read_bytes()).hexdigest() == provenance["license_sha256"]
for item in provenance["files"]:
    source = vendor / "src" / item["path"]
    assert hashlib.sha256(source.read_bytes()).hexdigest() == item["sha256"], item["path"]
paths = (
    subprocess.check_output(
        ["git", "ls-files", "--cached", "--others", "--exclude-standard"], cwd=root
    )
    .decode()
    .splitlines()
)
for name in paths:
    path = Path(name)
    assert not set(path.parts) & {"private", "acceptance", "references", ".ops", "data"}, name
    assert not (path.name.startswith(".env") and path.name != ".env.example"), name
    assert path.suffix.lower() not in {".pem", ".key", ".p12", ".pfx"}, name
    if path.suffix == ".json":
        json.loads((root / path).read_text(encoding="utf-8"))
print(
    json.dumps(
        {
            "upstream_source_hashes": len(provenance["files"]),
            "public_files_checked": len(paths),
            "status": "passed",
        }
    )
)
