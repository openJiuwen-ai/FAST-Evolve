# Copyright (c) Huawei Technologies Co., Ltd. 2026-2026. All rights reserved.
"""Download the external Cloudcast benchmark data."""

from __future__ import annotations

import argparse
from pathlib import Path
from urllib.request import urlretrieve

BASE_URL = "https://huggingface.co/datasets/f20180301/adrs-data/resolve/main/cloudcast"
FILES = [
    ("profiles/cost.csv", "profiles/cost.csv"),
    ("profiles/throughput.csv", "profiles/throughput.csv"),
    ("examples/config/intra_aws.json", "examples/config/intra_aws.json"),
    ("examples/config/intra_azure.json", "examples/config/intra_azure.json"),
    ("examples/config/intra_gcp.json", "examples/config/intra_gcp.json"),
    ("examples/config/inter_agz.json", "examples/config/inter_agz.json"),
    ("examples/config/inter_gaz2.json", "examples/config/inter_gaz2.json"),
]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--force", action="store_true", help="Redownload existing files")
    args = parser.parse_args()

    benchmark_dir = Path(__file__).resolve().parents[1]
    for remote_path, local_path in FILES:
        destination = benchmark_dir / local_path
        destination.parent.mkdir(parents=True, exist_ok=True)
        if destination.exists() and not args.force:
            print(f"skip {local_path}")
            continue
        print(f"download {local_path}")
        urlretrieve(f"{BASE_URL}/{remote_path}", destination)


if __name__ == "__main__":
    main()
