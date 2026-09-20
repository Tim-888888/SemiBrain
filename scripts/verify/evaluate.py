"""Offline evaluator skeleton. Running services never receive the oracle or this directory.

Responses are evaluation envelopes around unrestricted Markdown and observable trace metadata.
Subjective answer quality always requires a reviewer; passing arithmetic is not product acceptance.
"""

import argparse
import hashlib
import json
from pathlib import Path


def evaluate(root: Path, responses: Path | None):
    manifest = json.loads((root / "manifest.json").read_text(encoding="utf-8"))
    results = []
    for task in manifest["tasks"]:
        response_file = responses / (task["task_id"] + ".json") if responses else None
        item = {"task_id": task["task_id"], "stratum": task["stratum"], "status": "not_executed"}
        if response_file and response_file.exists():
            reply = json.loads(response_file.read_text(encoding="utf-8"))
            gold_path = (root / task["oracle_path"]).resolve()
            assert gold_path.is_relative_to(root)
            assert hashlib.sha256(gold_path.read_bytes()).hexdigest() == task["oracle_sha256"]
            gold = json.loads(gold_path.read_text(encoding="utf-8"))
            assert isinstance(reply["markdown"], str)
            assert reply["strategy"] in {
                "quick_rag",
                "single_agent",
                "multi_agent",
                "fixed_workflow",
            }
            item.update(
                status="requires_review",
                strategy=reply["strategy"],
                measurements=reply["measurements"],
            )
            # Metrics come from tool observations, never from a required report body schema.
            if gold["kind"] == "numeric":
                observed = reply.get("observations", {}).get("metrics", {})
                item["arithmetic_passed"] = all(
                    observed.get(k) == v for k, v in gold["metrics"].items()
                )
            if gold["kind"] == "policy":
                item["policy_passed"] = (
                    reply.get("observations", {}).get("denied") is True
                    and reply.get("observations", {}).get("executed_tool_count") == 0
                )
            item["review_required"] = [
                "answer_grounding",
                "citations",
                "scope",
                "missing_evidence",
                "markdown_readability",
            ]
        results.append(item)
    return {
        "manifest_sha256": hashlib.sha256((root / "manifest.json").read_bytes()).hexdigest(),
        "results": results,
        "product_acceptance": "not_signed",
        "measurement_contract": {
            "units": {
                "ttft_ms": "ms",
                "wall_ms": "ms",
                "input_tokens": "tokens",
                "output_tokens": "tokens",
                "cost": "provider_currency",
            },
            "comparison": "Same inputs, model version, ACL, available tools, corpus version and per-run ceilings; report missing values, never zeros for unavailable measurements.",
        },
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("directory", type=Path)
    parser.add_argument("--responses", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    repo = Path(__file__).resolve().parents[2]
    assert not args.output.resolve().is_relative_to(repo)
    report = evaluate(args.directory.resolve(), args.responses)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(
        json.dumps(
            {
                "tasks": len(report["results"]),
                "executed": sum(r["status"] != "not_executed" for r in report["results"]),
                "acceptance": report["product_acceptance"],
            }
        )
    )
