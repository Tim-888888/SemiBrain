"""Community-edition trace retention using the supported project API (stdlib only)."""
import argparse
import base64
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urlencode, urlsplit
from urllib.request import Request, urlopen


def eligible_ids(rows, cutoff):
    result = []
    for row in rows:
        stamp = datetime.fromisoformat(row["timestamp"].replace("Z", "+00:00"))
        if stamp.tzinfo is None or stamp >= cutoff:
            raise ValueError("Retention query returned a record outside the deletion window")
        if not isinstance(row["id"], str) or not row["id"]:
            raise ValueError("Invalid trace identifier")
        result.append(row["id"])
    return result


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--env", required=True, type=Path)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    env = dict(line.split("=", 1) for line in args.env.read_text().splitlines()
               if line and not line.startswith("#"))
    days = int(env.get("RETENTION_DAYS", "30"))
    if days < 7:
        raise ValueError("Retention must be at least seven days")
    origin = env["LANGFUSE_BASE_URL"].rstrip("/")
    parsed = urlsplit(origin)
    if parsed.scheme != "https" and not (parsed.scheme == "http" and parsed.hostname in {"127.0.0.1", "localhost", "::1"}):
        raise ValueError("Retention requires HTTPS or a local tunnel")
    token = base64.b64encode((env["LANGFUSE_PUBLIC_KEY"] + ":" + env["LANGFUSE_SECRET_KEY"]).encode()).decode()

    def request(method, path, body=None):
        data = None if body is None else json.dumps(body).encode()
        req = Request(origin + path, method=method, data=data,
                      headers={"Authorization": "Basic " + token, "Content-Type": "application/json"})
        with urlopen(req, timeout=30) as response:
            return json.load(response)

    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    query = urlencode({"fromTimestamp": "2020-01-01T00:00:00Z", "toTimestamp": cutoff.isoformat(), "limit": 100})
    # Freeze a bounded list before deletion; never page through a changing result set.
    ids = []
    for page in range(1, 51):
        rows = request("GET", "/api/public/traces?" + query + "&page=" + str(page))["data"]
        ids.extend(eligible_ids(rows, cutoff))
        if len(rows) < 100:
            break
    ids = list(dict.fromkeys(ids))
    if args.apply:
        for offset in range(0, len(ids), 100):
            request("DELETE", "/api/public/traces", {"traceIds": ids[offset:offset + 100]})
    print(json.dumps({"retention_days": days, "matched": len(ids), "applied": args.apply,
                      "cutoff": cutoff.isoformat(), "bounded_to": 5000}))


if __name__ == "__main__":
    main()
