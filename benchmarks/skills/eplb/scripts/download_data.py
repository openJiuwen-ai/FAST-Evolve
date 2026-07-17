"""Download the external EPLB workload."""

from __future__ import annotations

import argparse
from pathlib import Path
from urllib.request import urlretrieve


URL = "https://huggingface.co/datasets/abmfy/eplb-openevolve/resolve/main/expert-load.json"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--force", action="store_true", help="Redownload an existing workload")
    args = parser.parse_args()

    destination = Path(__file__).resolve().parents[1] / "expert-load.json"
    if destination.exists() and not args.force:
        print(f"skip {destination}")
        return
    print(f"download {destination}")
    urlretrieve(URL, destination)


if __name__ == "__main__":
    main()
