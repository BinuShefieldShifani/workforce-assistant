"""Make sure the Kaggle CSV is in data/raw/ before the build scripts run.

Used by the Render build (render.yaml). Locally the file is already there, so this
does nothing. On a server it downloads the dataset with the Kaggle CLI, using
credentials from environment variables, so the CSV never has to be committed:
    KAGGLE_USERNAME + KAGGLE_KEY   (from a kaggle.json "legacy" API key), or
    KAGGLE_API_TOKEN               (newer single-token keys)

Usage:
    python scripts/prepare_data.py
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys

from workforce.config import RAW_CSV

DATASET = "pavansubhasht/ibm-hr-analytics-attrition-dataset"


def main() -> int:
    if RAW_CSV.exists():
        print(f"{RAW_CSV.name} already present, nothing to download")
        return 0

    has_creds = (os.getenv("KAGGLE_USERNAME") and os.getenv("KAGGLE_KEY")) or os.getenv("KAGGLE_API_TOKEN")
    if not has_creds:
        print("The dataset is missing and no Kaggle credentials are set.\n"
              "Set KAGGLE_USERNAME and KAGGLE_KEY (or KAGGLE_API_TOKEN) in the environment.")
        return 1
    if shutil.which("kaggle") is None:
        print("The Kaggle CLI is not installed. Run: pip install kaggle")
        return 1

    RAW_CSV.parent.mkdir(parents=True, exist_ok=True)
    print(f"Downloading {DATASET} from Kaggle...")
    subprocess.run(["kaggle", "datasets", "download", "-d", DATASET, "-p", str(RAW_CSV.parent), "--unzip"],
                   check=True)
    if not RAW_CSV.exists():
        print(f"Download finished but {RAW_CSV.name} was not found in {RAW_CSV.parent}")
        return 1
    print(f"Saved {RAW_CSV}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
