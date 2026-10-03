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
        stamp = datetime.fromisoformat(row["startTime"].replace("Z", "+00:00"))
        if stamp.tzinfo is None or stamp >= cutoff:
            raise ValueError("Retention query returned a record outside the deletion window")
        if not isinstance(row["traceId"], str) or not row["traceId"]:
            raise ValueError("Invalid trace identifier")
        if row.get("endTime"):
            result.append(row["traceId"])
    return result


def trace_expired(result, cutoff):
    # Never delete a partially inspected, unfinished, or recently active trace.
    if result.get("meta", {}).get("cursor") or not result["data"]:
        return False
    for row in result["data"]:
        if not row.get("endTime"):
            return False
        for key in ("startTime", "endTime"):
            stamp = datetime.fromisoformat(row[key].replace("Z", "+00:00"))
            if stamp.tzinfo is None or stamp >= cutoff:
                return False
    return True


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
    query = {"fromStartTime": "2020-01-01T00:00:00Z", "toStartTime": cutoff.isoformat(),
             "fields": "core", "limit": 100}
    # Freeze a bounded list before deletion; never page through a changing result set.
    ids = []
    for _ in range(50):
        result = request("GET", "/api/public/v2/observations?" + urlencode(query))
        rows = result["data"]
        ids.extend(eligible_ids(rows, cutoff))
        cursor = result.get("meta", {}).get("cursor")
        if not cursor:
            break
        query["cursor"] = cursor
    ids = list(dict.fromkeys(ids))
    # A trace with an old span and newer activity is not expired as a whole.
    safe = []
    for trace_id in ids[:500]:
        complete = request("GET", "/api/public/v2/observations?" + urlencode({
            "traceId": trace_id, "fromStartTime": "2020-01-01T00:00:00Z",
            "toStartTime": datetime.now(timezone.utc).isoformat(), "fields": "core", "limit": 1000}))
        if trace_expired(complete, cutoff):
            safe.append(trace_id)
    ids = safe
    if args.apply:
        for offset in range(0, len(ids), 100):
            request("DELETE", "/api/public/traces", {"traceIds": ids[offset:offset + 100]})
    print(json.dumps({"retention_days": days, "matched": len(ids), "applied": args.apply,
                      "cutoff": cutoff.isoformat(), "bounded_to": 500}))


if __name__ == "__main__":
    main()
