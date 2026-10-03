"""Consistent cold backup of the independent demo stack; never stops SemiBrain."""
import argparse
import fcntl
import hashlib
import json
import os
import subprocess
import tarfile
from datetime import datetime, timezone
from pathlib import Path


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--compose-dir", required=True, type=Path)
    parser.add_argument("--env", required=True, type=Path)
    parser.add_argument("--destination", required=True, type=Path)
    args = parser.parse_args()
    os.umask(0o077)
    env = dict(line.split("=", 1) for line in args.env.read_text().splitlines()
               if line and not line.startswith("#"))
    data = Path(env["LANGFUSE_DATA_DIR"]).resolve()
    secrets = Path(env["LANGFUSE_SECRET_DIR"]).resolve()
    destination = args.destination.resolve()
    if data == destination or data in destination.parents or data == Path("/"):
        raise ValueError("Backup destination must be outside the data directory")
    destination.mkdir(parents=True, exist_ok=True)
    with (destination / ".lock").open("w") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        compose = ["docker", "compose", "--env-file", str(args.env), "-f", str(args.compose_dir / "compose.yaml")]
        # Explicit service names constrain the maintenance window to Langfuse.
        services = ["web", "worker", "postgres", "redis", "minio", "clickhouse"]
        running = subprocess.check_output(compose + ["ps", "--status", "running", "--services"], text=True).split()
        if not set(services).issubset(running):
            raise RuntimeError("Start and verify the full Langfuse stack before backup")
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        path = destination / ("langfuse-" + stamp + ".tar.gz")
        partial = path.with_suffix(".partial")
        try:
            subprocess.run(compose + ["stop", "-t", "45", "web", "worker"], check=True)
            subprocess.run(compose + ["stop", "-t", "45", *services[2:]], check=True)
            with tarfile.open(partial, "w:gz", compresslevel=1) as archive:
                archive.add(data, arcname="data")
                archive.add(secrets, arcname="secrets")
                archive.add(args.compose_dir, arcname="deployment")
            partial.replace(path)
        finally:
            subprocess.run(compose + ["up", "-d"], check=True)
        with path.open("rb") as source:
            checksum = hashlib.file_digest(source, "sha256").hexdigest()
        path.with_suffix(path.suffix + ".sha256").write_text(checksum + "  " + path.name + "\n")
        # Keep four weekly backup generations; never recurse or prune arbitrary paths.
        backups = sorted(destination.glob("langfuse-????????T??????Z.tar.gz"))
        for old in backups[:-4]:
            old.unlink()
            old.with_suffix(old.suffix + ".sha256").unlink(missing_ok=True)
        print(json.dumps({"archive": str(path), "sha256": checksum, "bytes": path.stat().st_size}))


if __name__ == "__main__":
    main()
