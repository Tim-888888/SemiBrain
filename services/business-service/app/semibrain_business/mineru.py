"""MinerU batch-upload adaptation, retaining source ZIP structure and actual locations."""

import base64
import http.client
import io
import ipaddress
import json
import os
import socket
import ssl
import time
import zipfile
from urllib.parse import urljoin, urlsplit
from uuid import uuid4

import httpx


def pinned_https(method, url, body=None, limit=64 * 1024**2, redirects=3):
    # Pin the validated address for TLS while retaining hostname/SNI verification.
    parts = urlsplit(url)
    if (
        parts.scheme != "https"
        or not parts.hostname
        or parts.username
        or parts.password
        or parts.port not in {None, 443}
    ):
        raise ValueError("PROVIDER_URL_DENIED")
    addresses = socket.getaddrinfo(parts.hostname, 443, type=socket.SOCK_STREAM)
    if not addresses or any(not ipaddress.ip_address(a[4][0]).is_global for a in addresses):
        raise ValueError("PROVIDER_URL_DENIED")
    tcp = socket.create_connection(addresses[0][4], timeout=30)
    conn = http.client.HTTPSConnection(parts.hostname, timeout=30)
    conn.sock = ssl.create_default_context().wrap_socket(tcp, server_hostname=parts.hostname)
    try:
        conn.request(method, parts.path + ("?" + parts.query if parts.query else ""), body=body)
        response = conn.getresponse()
        if response.status in {301, 302, 303, 307, 308}:
            if method != "GET" or redirects <= 0:
                raise ValueError("PROVIDER_REDIRECT_DENIED")
            return pinned_https(
                "GET",
                urljoin(url, response.getheader("Location", "")),
                limit=limit,
                redirects=redirects - 1,
            )
        if not 200 <= response.status < 300:
            raise ValueError("PROVIDER_TRANSFER_FAILED")
        payload = response.read(limit + 1)
        if len(payload) > limit:
            raise ValueError("PROVIDER_RESULT_TOO_LARGE")
        return payload
    finally:
        conn.close()


def extract_zip(content):
    from semibrain_business.parsing import validate_archive

    validate_archive(content)
    with zipfile.ZipFile(io.BytesIO(content)) as archive:
        names = archive.namelist()
        markdown_files = sorted(
            [n for n in names if n.endswith(".md")], key=lambda n: (n.count("/"), n)
        )
        if not markdown_files:
            raise ValueError("RESULT_MARKDOWN_MISSING")
        markdown = archive.read(markdown_files[0]).decode("utf-8")
        images = {
            n: base64.b64encode(archive.read(n)).decode()
            for n in names
            if n.lower().endswith((".png", ".jpg", ".jpeg", ".webp"))
        }
        blocks = []
        for name in names:
            if name.endswith("_content_list.json"):
                items = json.loads(archive.read(name))
                for item in items:
                    loc = {k: item[k] for k in ["page_idx", "bbox"] if k in item}
                    if "bbox" in loc:
                        loc["coordinate_system"] = "mineru_native"
                    text = item.get("text") or item.get("table_body") or ""
                    if loc:
                        blocks.append(
                            {"kind": item.get("type", "unknown"), "text": text, "location": loc}
                        )
                break
        return {
            "content": markdown,
            "images": images,
            "blocks": blocks,
            "metadata": {"provider": "MinerU", "has_native_locations": bool(blocks)},
        }


def parse_mineru(content, progress):
    key = os.environ["SEMIBRAIN_MINERU_API_KEY"]
    base = "https://mineru.net/api/v4"
    with httpx.Client(
        timeout=30, follow_redirects=False, headers={"Authorization": "Bearer " + key}
    ) as client:
        response = client.post(
            base + "/file-urls/batch",
            json={
                "files": [{"name": "source.pdf", "data_id": str(uuid4())}],
                "model_version": "vlm",
                "is_ocr": True,
                "enable_formula": True,
                "enable_table": True,
                "language": "ch",
            },
        )
        response.raise_for_status()
        applied = response.json()
        if applied.get("code") != 0:
            raise ValueError("PROVIDER_APPLY_FAILED")
        batch = applied["data"]["batch_id"]
        progress(batch)
        pinned_https("PUT", applied["data"]["file_urls"][0], content)
        for _ in range(90):
            response = client.get(base + "/extract-results/batch/" + batch)
            response.raise_for_status()
            payload = response.json()
            if payload.get("code") != 0:
                raise ValueError("PROVIDER_POLL_FAILED")
            items = payload["data"].get("extract_result", [])
            if isinstance(items, dict):
                items = [items]
            if items and items[0].get("state") == "failed":
                raise ValueError("PROVIDER_PARSE_FAILED")
            if items and items[0].get("state") == "done":
                return extract_zip(pinned_https("GET", items[0]["full_zip_url"]))
            time.sleep(2)
        raise ValueError("PROVIDER_DEADLINE_EXCEEDED")
