"""Read the article PDFs from S3, chunk them, embed with Bedrock, store in S3 Vectors.

Usage: aws-vault exec "$AWS_PROFILE" -- uv run src/ingest.py
"""

import io
import os
import re
import unicodedata
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from botocore.exceptions import ClientError
from pypdf import PdfReader

from common import (
    ARTICLES,
    EMBED_DIM,
    VECTOR_BUCKET,
    VECTOR_INDEX,
    embed,
    s3,
    s3vectors,
)

# Only the ingestion reads the source PDFs; the server never touches them.
DOCS_BUCKET = os.environ["DOCS_BUCKET"]
DOCS_PREFIX = os.environ.get("DOCS_PREFIX", "atanas/raw/")

CHUNK_WORDS = 220
OVERLAP_WORDS = 40
EMBED_WORKERS = 8
PUT_BATCH = 100
BOILERPLATE_MIN_LEN = 15


def read_pdf(key: str) -> list[str]:
    """Extract the text of a PDF stored in S3, one string per page."""
    body = s3().get_object(Bucket=DOCS_BUCKET, Key=key)["Body"]
    reader = PdfReader(io.BytesIO(body.read()))
    return [page.extract_text() or "" for page in reader.pages]


def strip_boilerplate(pages: list[str]) -> str:
    """Drop lines repeated across most pages: running headers, footers, arXiv stamps.

    These add nothing to a chunk's meaning but do skew its embedding.
    """
    lines = Counter(
        line.strip()
        for page in pages
        for line in page.splitlines()
        if len(line.strip()) > BOILERPLATE_MIN_LEN
    )
    boilerplate = {line for line, count in lines.items() if count >= max(3, len(pages) * 0.5)}
    return "\n".join(
        line for page in pages for line in page.splitlines() if line.strip() not in boilerplate
    )


def clean(text: str) -> str:
    """Undo the PDF layout artifacts that would otherwise be embedded as noise."""
    # Soft hyphens and hyphens before a line break split words mid-token.
    text = text.replace("­", "").replace("-\n", "")
    # These papers encode "fi"/"ffi"/"fl" as single ligature glyphs, so "efficient"
    # arrives as "e<ffi>cient". NFKC expands them back into plain ASCII letters.
    text = unicodedata.normalize("NFKC", text)
    return re.sub(r"\s+", " ", text).strip()


def chunk(text: str) -> list[str]:
    """Split text into overlapping word windows so sentences are not cut mid-thought."""
    words = text.split()
    step = CHUNK_WORDS - OVERLAP_WORDS
    # Stop one overlap short of the end, otherwise the last window is pure overlap.
    stop = max(len(words) - OVERLAP_WORDS, 1)
    return [" ".join(words[i : i + CHUNK_WORDS]) for i in range(0, stop, step)]


def ensure_index() -> None:
    """Create the vector bucket and index if they do not exist yet."""
    for create, kwargs in (
        (s3vectors().create_vector_bucket, {"vectorBucketName": VECTOR_BUCKET}),
        (
            s3vectors().create_index,
            {
                "vectorBucketName": VECTOR_BUCKET,
                "indexName": VECTOR_INDEX,
                "dataType": "float32",
                "dimension": EMBED_DIM,
                "distanceMetric": "cosine",
                # The chunk text is only ever returned, never filtered on, and it is far
                # bigger than the filterable metadata limit.
                "metadataConfiguration": {"nonFilterableMetadataKeys": ["text"]},
            },
        ),
    ):
        try:
            create(**kwargs)
        except ClientError as error:
            if error.response["Error"]["Code"] != "ConflictException":
                raise


def ingest(article: dict) -> list[str]:
    """Chunk, embed and store one article. Returns the vector keys written."""
    article_id = Path(article.file).stem
    chunks = chunk(clean(strip_boilerplate(read_pdf(DOCS_PREFIX + article.file))))

    with ThreadPoolExecutor(EMBED_WORKERS) as pool:
        embeddings = list(pool.map(embed, chunks))

    vectors = [
        {
            "key": f"{article_id}#{chunk_id}",
            "data": {"float32": embedding},
            "metadata": {
                "article_title": article.title,
                "article_url": article.url,
                "chunk_id": chunk_id,
                "text": text,
            },
        }
        for chunk_id, (text, embedding) in enumerate(zip(chunks, embeddings))
    ]

    for start in range(0, len(vectors), PUT_BATCH):
        s3vectors().put_vectors(
            vectorBucketName=VECTOR_BUCKET,
            indexName=VECTOR_INDEX,
            vectors=vectors[start : start + PUT_BATCH],
        )
    return [vector["key"] for vector in vectors]


def delete_orphans(written: set[str]) -> int:
    """Drop vectors from an earlier run that the current corpus no longer produces.

    Keys are deterministic, so re-ingesting overwrites in place; only chunks that
    disappeared (a shorter article, a removed one) need cleaning up.
    """
    pages = s3vectors().get_paginator("list_vectors").paginate(
        vectorBucketName=VECTOR_BUCKET, indexName=VECTOR_INDEX
    )
    stale = [v["key"] for page in pages for v in page["vectors"] if v["key"] not in written]
    for start in range(0, len(stale), PUT_BATCH):
        s3vectors().delete_vectors(
            vectorBucketName=VECTOR_BUCKET,
            indexName=VECTOR_INDEX,
            keys=stale[start : start + PUT_BATCH],
        )
    return len(stale)


def main() -> None:
    ensure_index()
    print(f"index ready: {VECTOR_BUCKET}/{VECTOR_INDEX}")
    written = set()
    for article in ARTICLES:
        keys = ingest(article)
        written.update(keys)
        print(f"  {article.file}: {len(keys)} chunks")
    print(f"stored {len(written)} vectors from {len(ARTICLES)} articles")
    orphans = delete_orphans(written)
    if orphans:
        print(f"removed {orphans} stale vectors from a previous run")


if __name__ == "__main__":
    main()
