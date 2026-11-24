"""
Download and extract the official full HDFS dataset from LogPAI LogHub on Zenodo.
Preserves existing HDFS_2k benchmark intact.
Verifies MD5 checksum and extracts raw and preprocessed components.
"""

from __future__ import annotations

import hashlib
import shutil
import sys
import time
import urllib.request
import zipfile
from pathlib import Path

ZENODO_URL = "https://zenodo.org/records/8196385/files/HDFS_v1.zip"
EXPECTED_MD5 = "76a24b4d9a6164d543fb275f89773260"
EXPECTED_SIZE_BYTES = 186645559


def compute_md5(file_path: Path, chunk_size: int = 1024 * 1024) -> str:
    """Calculate MD5 hash of a local file."""
    hasher = hashlib.md5()
    with open(file_path, "rb") as f:
        while chunk := f.read(chunk_size):
            hasher.update(chunk)
    return hasher.hexdigest()


def download_file(url: str, dest_path: Path) -> None:
    """Download file with progress reporting."""
    dest_path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = dest_path.with_suffix(".tmp")

    print(f"Downloading from: {url}")
    print(f"Destination: {dest_path}")

    req = urllib.request.Request(url, headers={"User-Agent": "RootLens/1.0"})
    t0 = time.time()

    with urllib.request.urlopen(req) as resp, open(tmp_path, "wb") as out_f:
        total_len = int(resp.headers.get("Content-Length", 0))
        downloaded = 0
        last_print = 0

        while True:
            chunk = resp.read(1024 * 1024)
            if not chunk:
                break
            out_f.write(chunk)
            downloaded += len(chunk)
            now = time.time()
            if now - last_print > 5.0 or downloaded == total_len:
                pct = (downloaded / total_len * 100) if total_len else 0
                speed = (downloaded / 1024 / 1024) / (now - t0)
                print(f"Progress: {downloaded / 1024 / 1024:.1f}/{total_len / 1024 / 1024:.1f} MB ({pct:.1f}%) @ {speed:.2f} MB/s")
                last_print = now

    tmp_path.replace(dest_path)
    elapsed = time.time() - t0
    print(f"Download complete: {dest_path.stat().st_size:,} bytes in {elapsed:.1f}s")


def main() -> None:
    raw_dir = Path("data/benchmarks/hdfs_full/raw")
    raw_dir.mkdir(parents=True, exist_ok=True)
    zip_path = raw_dir / "HDFS_v1.zip"

    # Step 1: Download if not already present and verified
    needs_download = True
    if zip_path.is_file():
        print(f"Found existing {zip_path}, verifying MD5...")
        actual_md5 = compute_md5(zip_path)
        if actual_md5 == EXPECTED_MD5:
            print("Existing archive matches expected MD5.")
            needs_download = False
        else:
            print(f"MD5 mismatch (found {actual_md5}, expected {EXPECTED_MD5}). Re-downloading...")

    if needs_download:
        download_file(ZENODO_URL, zip_path)
        actual_md5 = compute_md5(zip_path)
        if actual_md5 != EXPECTED_MD5:
            raise ValueError(f"Checksum verification failed! Got {actual_md5}, expected {EXPECTED_MD5}")
        print("MD5 checksum verified successfully.")

    # Step 2: Extract files
    print("\nExtracting archive contents to:", raw_dir)
    with zipfile.ZipFile(zip_path, "r") as zf:
        for member in zf.infolist():
            print(f"  Extracting {member.filename} ({member.file_size:,} bytes)...")
            zf.extract(member, raw_dir)

    # Flatten preprocessed files to raw_dir if helpful or keep structure
    preprocessed_dir = raw_dir / "preprocessed"
    if preprocessed_dir.is_dir():
        print("\nOrganizing preprocessed components...")
        for item in preprocessed_dir.iterdir():
            target = raw_dir / item.name
            if not target.exists():
                shutil.copy2(item, target)
                print(f"  Copied {item.name} -> {target}")

    print("\nHDFS Full raw acquisition complete!")
    print("Files in", raw_dir)
    for p in raw_dir.iterdir():
        if p.is_file():
            print(f"  {p.name}: {p.stat().st_size:,} bytes")


if __name__ == "__main__":
    main()
