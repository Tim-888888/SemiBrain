"""Development fixtures only; no sealed evaluation inputs are used."""

import json
import resource
import tempfile
import time
from pathlib import Path

from docx import Document
from reportlab.pdfgen import canvas
from semibrain_business.parsing import parse_asset

if __name__ == "__main__":
    rows = []
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        (root / "guide.md").write_text("# Process guide\n\nYield is 91.5%.", encoding="utf-8")
        (root / "values.csv").write_bytes(
            '批次;备注;数值\nLot-a;"first\nsecond";=1+1\nLot-A;普通;2.5\n'.encode("gb18030")
        )
        doc = Document()
        doc.add_paragraph("Process guide 91.5%")
        doc.save(root / "guide.docx")
        pdf = canvas.Canvas(str(root / "guide.pdf"))
        pdf.drawString(50, 700, "Process guide 91.5 percent")
        pdf.save()
        for source in sorted(root.iterdir()):
            started = time.monotonic()
            result = parse_asset(source, allowed_root=root)
            rows.append(
                {
                    "format": source.suffix,
                    "bytes": source.stat().st_size,
                    "status": result.status,
                    "elapsed_ms": int((time.monotonic() - started) * 1000),
                    "max_child_rss_so_far_kib": resource.getrusage(
                        resource.RUSAGE_CHILDREN
                    ).ru_maxrss,
                    "attempts": [a.model_dump() for a in result.attempts],
                    "findings": result.quality_findings,
                    "blocks": len(result.blocks),
                    "images": len(result.images),
                }
            )
            assert result.status == "staged"
    print(json.dumps(rows))
