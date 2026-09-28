"""Bounded parser adaptation inside the business Worker, with explicit attempt evidence."""

from __future__ import annotations

import csv
import hashlib
import io
import logging
import multiprocessing
import os
import re
import signal
import time
import zipfile
from collections import Counter
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from threading import Event
from typing import Any

from markdown_it import MarkdownIt
from pydantic import BaseModel, Field

UPSTREAM_COMMIT = "c40a9dd1940f85d85a59787bd532eb0f290cb176"
SUPPORTED = {"pdf", "docx", "md", "csv"}


class Block(BaseModel):
    kind: str
    text: str
    location: dict[str, Any]


class Attempt(BaseModel):
    engine: str
    status: str
    elapsed_ms: int = 0
    code: str | None = None
    provider_job_id: str | None = None


class ParseResult(BaseModel):
    schema_version: str = "1.0"
    status: str
    source_hash: str
    markdown: str = ""
    blocks: list[Block] = Field(default_factory=list)
    images: dict[str, str] = Field(default_factory=dict, repr=False)
    image_refs: list[dict[str, Any]] = Field(default_factory=list)
    parser_manifest: dict[str, Any] = Field(default_factory=dict)
    quality_findings: list[str] = Field(default_factory=list)
    attempts: list[Attempt] = Field(default_factory=list)


@dataclass(frozen=True)
class ParseProfile:
    engines: tuple[str, ...] = ()
    timeout_seconds: float = 120
    allow_external: bool = False
    max_bytes: int = 32 * 1024**2
    memory_bytes: int = 2 * 1024**3


def validate_archive(content: bytes, max_unpacked: int = 128 * 1024**2) -> None:
    with zipfile.ZipFile(io.BytesIO(content)) as archive:
        if (
            len(archive.infolist()) > 4096
            or sum(i.file_size for i in archive.infolist()) > max_unpacked
        ):
            raise ValueError("ARCHIVE_LIMIT")
        names = set()
        for info in archive.infolist():
            name = info.filename.replace("\\", "/")
            path = PurePosixPath(name)
            if path.is_absolute() or ".." in path.parts or ":" in path.parts[0] or name in names:
                raise ValueError("UNSAFE_ARCHIVE_PATH")
            names.add(name)
            if info.flag_bits & 1:
                raise ValueError("ENCRYPTED_ARCHIVE")
            if info.compress_size and info.file_size / info.compress_size > 200:
                raise ValueError("ARCHIVE_RATIO_LIMIT")


def _csv_content(content: bytes):
    try:
        decoded = content.decode("utf-8-sig")
        encoding = "utf-8-sig"
    except UnicodeDecodeError:
        decoded = content.decode("gb18030")
        encoding = "gb18030"
    try:
        dialect = csv.Sniffer().sniff(decoded[:8192], delimiters=",;\t|")
    except csv.Error:
        dialect = csv.excel
    reader = csv.reader(io.StringIO(decoded), dialect)
    rows = []
    locations = []
    previous = 0
    for row in reader:
        if len(rows) > 10000 or len(row) > 512:
            raise ValueError("CSV_LIMIT")
        rows.append(row)
        locations.append(
            {"record": len(rows), "line_start": previous + 1, "line_end": reader.line_num}
        )
        previous = reader.line_num
    if not rows:
        raise ValueError("EMPTY_CONTENT")
    width = len(rows[0])
    if any(len(row) != width for row in rows):
        raise ValueError("CSV_RAGGED_ROWS")

    def escape(value):
        return value.replace("\\", "\\\\").replace("|", "\\|").replace("\n", "<br>")

    markdown = "\n".join(
        ["| " + " | ".join(map(escape, rows[0])) + " |", "| " + " | ".join(["---"] * width) + " |"]
        + ["| " + " | ".join(map(escape, row)) + " |" for row in rows[1:]]
    )
    return {
        "content": markdown,
        "images": {},
        "metadata": {"encoding": encoding, "delimiter": dialect.delimiter},
        "blocks": [
            {"kind": "table_row", "text": json_line(row), "location": loc}
            for row, loc in zip(rows, locations)
        ],
    }


def json_line(row):
    import json

    return json.dumps(row, ensure_ascii=False)


def _source_blocks(content: bytes, extension: str) -> list[dict]:
    blocks = []
    if extension == "md":
        text = content.decode("utf-8-sig")
        lines = text.splitlines()
        for token in MarkdownIt().parse(text):
            if token.map and token.nesting != -1:
                a, b = token.map
                blocks.append(
                    dict(
                        kind=token.type,
                        text="\n".join(lines[a:b]),
                        location={"source": "original", "line_start": a + 1, "line_end": b},
                    )
                )
    elif extension == "pdf":
        from pypdf import PdfReader

        for index, page in enumerate(PdfReader(io.BytesIO(content)).pages):
            blocks.append(
                dict(
                    kind="page",
                    text=page.extract_text() or "",
                    location={"source": "original", "page_index": index},
                )
            )
    elif extension == "docx":
        from docx import Document
        from docx.table import Table

        for index, item in enumerate(Document(io.BytesIO(content)).iter_inner_content()):
            if isinstance(item, Table):
                for row_index, row in enumerate(item.rows):
                    blocks.append(
                        dict(
                            kind="table_row",
                            text=json_line([cell.text for cell in row.cells]),
                            location={
                                "source": "original",
                                "body_element": index,
                                "row": row_index,
                            },
                        )
                    )
            else:
                blocks.append(
                    dict(
                        kind="paragraph",
                        text=item.text,
                        location={"source": "original", "body_element": index},
                    )
                )
    return blocks


def _execute_engine(engine: str, content: bytes, extension: str, progress):
    if engine == "mineru_cloud":
        from semibrain_business.mineru import parse_mineru

        return parse_mineru(content, progress)
    if engine == "csv_python":
        return _csv_content(content)
    if engine == "weknora_markdown":
        from docreader.parser.markdown_parser import MarkdownParser

        parser = MarkdownParser(file_name="source.md")
    elif engine == "weknora_markitdown":
        from docreader.parser.markitdown_parser import MarkitdownParser

        parser = MarkitdownParser(file_name="source." + extension)
    elif engine == "weknora_docx":
        from docreader.parser.docx_parser import DocxParser

        parser = DocxParser(file_name="source.docx")
    elif engine == "weknora_pdf":
        from docreader.parser.pdf_parser import PDFParser

        parser = PDFParser(file_name="source.pdf")
    else:
        raise ValueError("ENGINE_UNAVAILABLE")
    document = parser.parse(content)
    result = {
        "content": document.content,
        "images": document.images,
        "metadata": document.metadata,
        "blocks": _source_blocks(content, extension),
    }
    if extension == "pdf" and engine == "weknora_pdf":
        native_text = "\n\n".join(block["text"] for block in result["blocks"])
        pattern = r"(?<!\w)[+-]?\d+(?:\.\d+)?(?:[Ee][+-]?\d+)?%?"
        converted = re.sub(r"!\[[^\]]*\]\([^)]*\)", "", document.content)
        if Counter(re.findall(pattern, native_text)) != Counter(re.findall(pattern, converted)):
            result["metadata"]["numeric_text_mismatch"] = True
            if not document.metadata.get("scanned_page_count", 0):
                image_links = re.findall(r"!\[[^\]]*\]\([^)]*\)", document.content)
                result["content"] = native_text + "\n\n" + "\n\n".join(image_links)
                result["metadata"]["text_compatibility_fallback"] = "pypdf"
    if extension == "csv":
        # Retain upstream conversion only when the original record values survive.
        native = _csv_content(content)
        source_rows = list(csv.reader(io.StringIO(content.decode("utf-8-sig"))))
        if any(value and value not in document.content for row in source_rows for value in row):
            raise ValueError("CSV_VALUES_LOST")
        result["blocks"] = native["blocks"]
    return result


def _child(pipe, engine, content, extension, memory_bytes):
    try:
        logging.disable(logging.CRITICAL)
        if os.name == "posix":
            import resource

            os.setsid()
            resource.setrlimit(resource.RLIMIT_AS, (memory_bytes, memory_bytes))
            resource.setrlimit(resource.RLIMIT_CPU, (120, 125))
        result = _execute_engine(
            engine, content, extension, lambda job: pipe.send({"progress": job})
        )
        pipe.send({"result": result})
    except Exception as exc:
        code = (
            str(exc)
            if isinstance(exc, ValueError) and re.fullmatch("[A-Z_]+", str(exc))
            else type(exc).__name__
        )
        pipe.send({"error": code})
    finally:
        pipe.close()


def _stop(process):
    if process.is_alive():
        if os.name == "posix":
            try:
                os.killpg(process.pid, signal.SIGTERM)
            except ProcessLookupError:
                pass
        else:
            process.terminate()
        process.join(timeout=2)
        if process.is_alive():
            process.kill()
    process.join(timeout=2)


def parse_asset(
    path: Path,
    *,
    allowed_root: Path,
    profile: ParseProfile | None = None,
    cancel: Event | None = None,
) -> ParseResult:
    profile = profile or ParseProfile()
    path = path.resolve(strict=True)
    if not path.is_relative_to(allowed_root.resolve(strict=True)):
        raise PermissionError("ASSET_OUTSIDE_AUTHORIZED_ROOT")
    if path.stat().st_size > profile.max_bytes:
        raise ValueError("FILE_TOO_LARGE")
    content = path.read_bytes()
    digest = hashlib.sha256(content).hexdigest()
    extension = path.suffix.lower().lstrip(".")
    result = ParseResult(
        status="failed",
        source_hash=digest,
        parser_manifest={
            "adapter_version": "1.0",
            "weknora_commit": UPSTREAM_COMMIT,
            "source_bytes": len(content),
        },
    )
    try:
        if extension not in SUPPORTED:
            raise ValueError("UNSUPPORTED_FORMAT")
        if extension == "docx":
            validate_archive(content)
        if extension == "pdf" and not content.startswith(b"%PDF-"):
            raise ValueError("INVALID_PDF_SIGNATURE")
    except Exception as exc:
        result.quality_findings = [str(exc) if isinstance(exc, ValueError) else type(exc).__name__]
        return result
    engines = (
        profile.engines
        or {
            "pdf": ("mineru_cloud", "weknora_pdf"),
            "docx": ("weknora_markitdown", "weknora_docx"),
            "md": ("weknora_markdown",),
            "csv": ("weknora_markitdown", "csv_python"),
        }[extension]
    )
    deadline = time.monotonic() + profile.timeout_seconds
    context = multiprocessing.get_context("spawn")
    for engine in engines:
        if cancel and cancel.is_set():
            result.status = "cancelled"
            result.quality_findings.append("CANCELLED_BEFORE_EXECUTION")
            break
        if engine == "mineru_cloud" and not profile.allow_external:
            result.attempts.append(
                Attempt(engine=engine, status="skipped", code="EXTERNAL_DATA_DENIED")
            )
            continue
        if engine == "mineru_cloud" and not os.getenv("SEMIBRAIN_MINERU_API_KEY"):
            result.attempts.append(
                Attempt(engine=engine, status="unavailable", code="PROVIDER_NOT_CONFIGURED")
            )
            continue
        receiver, sender = context.Pipe(duplex=False)
        process = context.Process(
            target=_child, args=(sender, engine, content, extension, profile.memory_bytes)
        )
        started = time.monotonic()
        process.start()
        sender.close()
        attempt = Attempt(engine=engine, status="failed")
        result.attempts.append(attempt)
        message = {}
        try:
            while True:
                if cancel and cancel.is_set():
                    attempt.code = "CANCELLED"
                    result.status = "cancelled"
                    break
                if time.monotonic() >= deadline:
                    attempt.code = "DEADLINE_EXCEEDED"
                    break
                if receiver.poll(0.05):
                    try:
                        message = receiver.recv()
                    except EOFError:
                        attempt.code = "WORKER_EXITED"
                        break
                    if "progress" in message:
                        attempt.provider_job_id = message["progress"]
                        continue
                    break
                if not process.is_alive():
                    attempt.code = "WORKER_EXITED"
                    break
        finally:
            _stop(process)
            receiver.close()
            attempt.elapsed_ms = int((time.monotonic() - started) * 1000)
        if attempt.code in {"CANCELLED", "DEADLINE_EXCEEDED"}:
            result.quality_findings.append(attempt.code)
            if attempt.provider_job_id:
                result.quality_findings.append("PROVIDER_JOB_MAY_CONTINUE")
            break
        if "error" in message:
            attempt.code = message["error"]
            continue
        if "result" not in message:
            continue
        output = message["result"]
        markdown = output.get("content", "")
        images = output.get("images", {})
        meta = output.get("metadata", {})
        readable = re.sub(r"!\[[^\]]*\]\([^)]*\)", "", markdown).strip()
        if not readable and not images:
            attempt.code = "EMPTY_CONTENT"
            continue
        result.markdown = markdown
        result.images = images
        result.blocks = [Block.model_validate(b) for b in output.get("blocks", [])]
        result.parser_manifest.update({"engine": engine, "metadata": meta})
        if meta.get("numeric_text_mismatch"):
            result.quality_findings.append("NUMERIC_TEXT_MISMATCH")
        if meta.get("text_compatibility_fallback"):
            result.quality_findings.append("NATIVE_TEXT_COMPATIBILITY_FALLBACK")
        if (not readable and images) or meta.get("scanned_page_count", 0) > 0:
            result.status = "needs_attention"
            result.quality_findings.append("OCR_REQUIRED")
        else:
            result.status = "staged"
        if not result.blocks:
            result.quality_findings.append("DETAILED_LOCATIONS_UNAVAILABLE")
        attempt.status = "succeeded"
        break
    if hashlib.sha256(path.read_bytes()).hexdigest() != digest:
        raise RuntimeError("SOURCE_CHANGED_DURING_PARSE")
    return result
