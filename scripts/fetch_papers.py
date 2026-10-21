"""Download ML papers from public sources and upload to S3."""

import os
import shutil
import subprocess
import sys
from pathlib import Path

import boto3

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from utils import load_articles  # noqa: E402

articles = load_articles()

docs_bucket = os.environ.get("DOCS_BUCKET")
docs_prefix = os.environ.get("DOCS_PREFIX")

if not docs_bucket or not docs_prefix:
    raise ValueError("Set DOCS_BUCKET and DOCS_PREFIX in .env")

print(f"Checking s3://{docs_bucket}/{docs_prefix}")
s3 = boto3.client("s3")
existing_keys = {
    obj["Key"]
    for page in s3.get_paginator("list_objects_v2").paginate(
        Bucket=docs_bucket,
        Prefix=docs_prefix,
    )
    for obj in page.get("Contents", [])
}
missing = [
    article
    for article in articles
    if f"{docs_prefix}{article.file}" not in existing_keys
]

if not missing:
    print("All papers from articles.json already exist; nothing to download or upload.")
    raise SystemExit(0)

print(f"{len(missing)} paper(s) missing; downloading to s3://{docs_bucket}/{docs_prefix}")

docs_dir = Path("documents")
docs_dir.mkdir(exist_ok=True)

for article in missing:
    filename = article.file
    url = article.download_url
    filepath = docs_dir / filename
    if not filepath.exists():
        print(f"  {filename}...", end=" ", flush=True)
        subprocess.run(["curl", "-Lo", str(filepath), url], check=True, capture_output=True)
        print("OK")
    else:
        print(f"  {filename} already downloaded")

print(f"\nUploading to S3...")
subprocess.run(
    ["aws", "s3", "sync", str(docs_dir), f"s3://{docs_bucket}/{docs_prefix}",
     "--include", "*.pdf"],
    check=True
)

print("Cleaning up local copy...")
shutil.rmtree(docs_dir)
print("Done")
