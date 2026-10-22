"""Download the external LLM-SQL CSV datasets."""

from __future__ import annotations

import argparse
from pathlib import Path
from urllib.request import urlretrieve

BASE_URL = "https://huggingface.co/datasets/f20180301/adrs-data/resolve/main/llm_sql/datasets"
DATASETS = ("movies.csv", "beer.csv", "BIRD.csv", "PDMX.csv", "products.csv")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--force", action="store_true", help="Redownload existing datasets")
    args = parser.parse_args()

    datasets_dir = Path(__file__).resolve().parents[1] / "datasets"
    datasets_dir.mkdir(parents=True, exist_ok=True)
    for filename in DATASETS:
        destination = datasets_dir / filename
        if destination.exists() and not args.force:
            print(f"skip {filename}")
            continue
        print(f"download {filename}")
        urlretrieve(f"{BASE_URL}/{filename}", destination)


if __name__ == "__main__":
    main()
