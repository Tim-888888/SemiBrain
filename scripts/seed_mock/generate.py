import argparse
import os
from pathlib import Path

from semibrain_business.warehouse import engine_from_url, generate, load_dataset, save_dataset

parser = argparse.ArgumentParser(description="Generate or load explicitly synthetic warehouse data")
parser.add_argument("--seed", type=int, required=True)
parser.add_argument("--prefix", default="DEV")
parser.add_argument("--output", type=Path)
parser.add_argument("--load", action="store_true")
args = parser.parse_args()
dataset = generate(args.seed, prefix=args.prefix)
if args.output:
    save_dataset(dataset, args.output)
if args.load:
    load_dataset(engine_from_url(os.environ["SEMIBRAIN_WAREHOUSE_ADMIN_URL"]), dataset)
print(
    {
        "dataset_id": dataset["dataset_id"],
        "content_hash": dataset["content_hash"],
        "row_counts": {k: len(v) for k, v in dataset["tables"].items()},
    }
)
