"""Local operational health snapshot; no credentials or trace payloads."""
import argparse
import json
import shutil
import subprocess
from datetime import datetime, timezone
from pathlib import Path


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--compose-dir", required=True, type=Path)
    parser.add_argument("--env", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    env = dict(line.split("=", 1) for line in args.env.read_text().splitlines()
               if line and not line.startswith("#"))
    cmd = ["docker", "compose", "--env-file", str(args.env), "-f", str(args.compose_dir / "compose.yaml")]
    result = subprocess.check_output(cmd + ["ps", "--format", "json"], text=True, timeout=20)
    rows = [json.loads(line) for line in result.splitlines() if line]
    disk = shutil.disk_usage(env["LANGFUSE_DATA_DIR"])
    expected = {"langfuse-" + name for name in ("web", "worker", "postgres", "redis", "minio", "clickhouse")}
    states = {row["Service"]: {"state": row["State"], "health": row.get("Health")} for row in rows}
    healthy = all(states.get(name) == {"state": "running", "health": "healthy"} for name in expected)
    healthy = healthy and disk.free / disk.total > 0.1
    snapshot = {"checked_at": datetime.now(timezone.utc).isoformat(), "healthy": healthy,
                "services": states, "data_disk_free_gib": round(disk.free / 1024 ** 3, 2)}
    args.output.write_text(json.dumps(snapshot, indent=2))
    print(json.dumps(snapshot))
    if not healthy:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
