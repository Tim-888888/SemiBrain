"""Upload only an explicitly generated synthetic PDF to the configured MinerU service."""

import hashlib
import json
import resource
import tempfile
import time
from pathlib import Path

from reportlab.pdfgen import canvas
from semibrain_business.parsing import ParseProfile, parse_asset


def main():
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        source = root / "synthetic-mineru-probe.pdf"
        pdf = canvas.Canvas(str(source))
        pdf.drawString(55, 740, "Synthetic semiconductor yield probe")
        pdf.drawString(55, 700, "Lot Alpha: passed 183 / tested 200 = 91.5 percent")
        pdf.rect(50, 620, 340, 45)
        pdf.drawString(55, 640, "Metric: FPY    Value: 91.5%")
        pdf.showPage()
        pdf.drawString(55, 740, "Second page: no causal conclusion is supported.")
        pdf.save()
        before = hashlib.sha256(source.read_bytes()).hexdigest()
        started = time.monotonic()
        result = parse_asset(
            source,
            allowed_root=root,
            profile=ParseProfile(engines=("mineru_cloud",), allow_external=True),
        )
        report = {
            "status": result.status,
            "attempts": [a.model_dump() for a in result.attempts],
            "elapsed_ms": int((time.monotonic() - started) * 1000),
            "max_child_rss_kib": resource.getrusage(resource.RUSAGE_CHILDREN).ru_maxrss,
            "findings": result.quality_findings,
            "images": len(result.images),
            "blocks": len(result.blocks),
            "page_indices": sorted(
                {b.location["page_idx"] for b in result.blocks if "page_idx" in b.location}
            ),
            "decimal_preserved": "91.5" in result.markdown,
            "original_unchanged": before == hashlib.sha256(source.read_bytes()).hexdigest(),
            "provider_cost": "not_returned_by_api",
        }
        print(json.dumps(report))
        assert (
            report["status"] == "staged"
            and report["decimal_preserved"]
            and report["original_unchanged"]
        )
        assert report["page_indices"] == [0, 1]


if __name__ == "__main__":
    main()
