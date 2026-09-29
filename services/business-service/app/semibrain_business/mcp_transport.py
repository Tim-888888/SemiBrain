"""Bounded official MCP Streamable HTTP client; no stdio, sampling or host roots."""

import asyncio
import json
import os
import re
from urllib.parse import urlsplit

from jsonschema import Draft202012Validator
from semibrain_common.runtime import canonical, digest

from semibrain_business.cancellation import ACTIVE_QUERY, QueryCancelled
from semibrain_business.safe_fetch import WebError


def endpoints():
    # Deployment-owned registry: a model or a web administrator cannot invent a URL.
    value = json.loads(os.getenv("SEMIBRAIN_MCP_ENDPOINTS_JSON", "{}"))
    if not isinstance(value, dict):
        raise WebError("MCP_ENDPOINT_CONFIGURATION_INVALID")
    return value


def endpoint(reference):
    row = endpoints().get(reference)
    if not isinstance(row, dict):
        raise WebError("MCP_ENDPOINT_UNCONFIGURED")
    url = row.get("url", "")
    parsed = urlsplit(url)
    if (parsed.scheme not in {"https", "http"} or not parsed.hostname or parsed.username
            or parsed.password or parsed.query or parsed.fragment
            or parsed.scheme == "http" and not row.get("allow_private")):
        raise WebError("MCP_ENDPOINT_CONFIGURATION_INVALID")
    ref = row.get("secret_ref")
    if ref and not re.fullmatch(r"SEMIBRAIN_MCP_[A-Z0-9_]+_TOKEN", ref):
        raise WebError("MCP_CREDENTIAL_REFERENCE_INVALID")
    secret = os.getenv(ref, "") if ref else ""
    if ref and not secret:
        raise WebError("MCP_CREDENTIAL_UNCONFIGURED")
    if row.get("data_origin", "authorized_business") not in {"public", "synthetic", "authorized_business"}:
        raise WebError("MCP_ENDPOINT_CONFIGURATION_INVALID")
    return {**row, "url": url}, secret


def validate_schema(schema):
    if (not isinstance(schema, dict) or schema.get("type") != "object"
            or len(canonical(schema).encode()) > 16000):
        raise WebError("MCP_SCHEMA_UNSUPPORTED")

    def visit(item, depth=0):
        if depth > 24:
            raise WebError("MCP_SCHEMA_UNSUPPORTED")
        if isinstance(item, dict):
            for key, value in item.items():
                if key in {"$ref", "$dynamicRef"} and (not isinstance(value, str) or not value.startswith("#/")):
                    raise WebError("MCP_SCHEMA_REMOTE_REFERENCE_DENIED")
                visit(value, depth + 1)
        elif isinstance(item, list):
            for value in item:
                visit(value, depth + 1)
    visit(schema)
    try:
        Draft202012Validator.check_schema(schema)
    except Exception:
        raise WebError("MCP_SCHEMA_INVALID") from None
    return schema


def tool_spec(tool):
    value = tool.model_dump(mode="json", by_alias=True) if hasattr(tool, "model_dump") else tool
    name = value.get("name")
    if not isinstance(name, str) or not name or len(name) > 160 or any(ord(c) < 32 for c in name):
        raise WebError("MCP_TOOL_NAME_INVALID")
    schema = validate_schema(value.get("inputSchema"))
    return {"name": name, "description": str(value.get("description") or "")[:1000],
            "input_schema": schema, "schema_hash": digest(canonical(schema))}


def validate_arguments(schema, arguments):
    if len(canonical(arguments).encode()) > 24000:
        raise WebError("MCP_ARGUMENTS_TOO_LARGE")
    try:
        Draft202012Validator(schema).validate(arguments)
    except Exception:
        raise WebError("MCP_ARGUMENT_SCHEMA_MISMATCH") from None


def safe_result(result, secret=""):
    value = result.model_dump(mode="json", by_alias=True)
    if value.get("isError"):
        raise WebError("MCP_REMOTE_TOOL_ERROR")
    # Resource links/images are not automatically fetched or registered as local assets.
    parts = [str(item.get("text", "")) for item in value.get("content", []) if item.get("type") == "text"]
    if value.get("structuredContent") is not None:
        parts.append(canonical(value["structuredContent"]))
    content = "\n\n".join(parts)
    if secret:
        content = content.replace(secret, "[credential removed]")
    if not content.strip():
        raise WebError("MCP_NO_TEXT_RESULT")
    if len(content.encode()) > 32000:
        raise WebError("MCP_RESULT_TOO_LARGE")
    return content


async def exchange(service, *, name=None, arguments=None, schema_hash=None):
    import httpx2
    from mcp import Client
    from mcp.client.streamable_http import streamable_http_client

    config, secret = endpoint(service["endpoint_ref"])
    target = httpx2.URL(config["url"])

    class LimitedStream(httpx2.AsyncByteStream):
        def __init__(self, inner):
            self.inner = inner

        async def __aiter__(self):
            size = 0
            async for chunk in self.inner:
                size += len(chunk)
                if size > 1024 * 1024:
                    raise WebError("MCP_RESPONSE_TOO_LARGE")
                yield chunk

        async def aclose(self):
            await self.inner.aclose()

    class LimitedTransport(httpx2.AsyncBaseTransport):
        def __init__(self):
            self.inner = httpx2.AsyncHTTPTransport()

        async def handle_async_request(self, request):
            response = await self.inner.handle_async_request(request)
            response.stream = LimitedStream(response.stream)
            return response

        async def aclose(self):
            await self.inner.aclose()

    async def request_guard(request):
        # Also covers SDK-managed 307/308 redirects: secrets never leave this exact endpoint.
        if request.url != target:
            raise WebError("MCP_REDIRECT_DENIED")

    headers = {"Authorization": "Bearer " + secret} if secret else {}
    async with httpx2.AsyncClient(headers=headers, timeout=service["timeout_seconds"],
                                 trust_env=False, follow_redirects=False,
                                 transport=LimitedTransport(),
                                 event_hooks={"request": [request_guard]}) as http:
        async with Client(streamable_http_client(config["url"], http_client=http),
                          read_timeout_seconds=service["timeout_seconds"], cache=None) as client:
            tools, cursor = [], None
            for _ in range(5):
                page = await client.list_tools(cursor=cursor, cache_mode="refresh")
                tools.extend(tool_spec(tool) for tool in page.tools)
                cursor = page.next_cursor
                if len(tools) > 100:
                    raise WebError("MCP_CATALOG_TOO_LARGE")
                if not cursor:
                    break
            if cursor or len({tool["name"] for tool in tools}) != len(tools):
                raise WebError("MCP_CATALOG_CONFLICT")
            if secret and secret in canonical(tools):
                raise WebError("MCP_CREDENTIAL_IN_REMOTE_CATALOG")
            if name is None:
                return tools
            item = next((tool for tool in tools if tool["name"] == name), None)
            if not item or item["schema_hash"] != schema_hash:
                raise WebError("MCP_SCHEMA_CHANGED_REDISCOVER")
            validate_arguments(item["input_schema"], arguments)
            result = await client.call_tool(name, arguments)
            return {"text": safe_result(result, secret),
                    "data_origin": config.get("data_origin", "authorized_business")}


async def bounded(service, **kwargs):
    task = asyncio.create_task(exchange(service, **kwargs))
    scope = ACTIVE_QUERY.get()
    try:
        async with asyncio.timeout(service["timeout_seconds"] + 5):
            while not task.done():
                if scope and scope.cancelled.is_set():
                    raise QueryCancelled("MCP_CANCELLED")
                await asyncio.sleep(.1)
            return await task
    finally:
        if not task.done():
            task.cancel()
        await asyncio.gather(task, return_exceptions=True)


def remote(service, **kwargs):
    try:
        return asyncio.run(bounded(service, **kwargs))
    except (WebError, QueryCancelled):
        raise
    except TimeoutError:
        raise WebError("MCP_TIMEOUT") from None
    except Exception as exc:
        # SDK/transport error strings may contain authentication headers or remote content.
        pending = [exc]
        while pending:
            item = pending.pop()
            if isinstance(item, (WebError, QueryCancelled)):
                raise item from None
            if isinstance(item, TimeoutError):
                raise WebError("MCP_TIMEOUT") from None
            pending.extend(getattr(item, "exceptions", []))
        raise WebError("MCP_CONNECTION_FAILED") from None
