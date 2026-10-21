"""Configuration and AWS helpers shared by the ingestion script and the MCP server."""

import json
import os
from functools import cache

import boto3

from utils import load_articles

REGION = os.environ.get("AWS_REGION", "us-east-1")
VECTOR_BUCKET = os.environ["VECTOR_BUCKET"]
VECTOR_INDEX = os.environ.get("VECTOR_INDEX", "articles")
EMBED_MODEL = os.environ.get("EMBED_MODEL", "amazon.titan-embed-text-v2:0")
EMBED_DIM = int(os.environ.get("EMBED_DIM", "1024"))

ARTICLES = load_articles()


@cache
def s3():
    return boto3.client("s3", region_name=REGION)


@cache
def bedrock():
    return boto3.client("bedrock-runtime", region_name=REGION)


@cache
def s3vectors():
    return boto3.client("s3vectors", region_name=REGION)


@cache
def agentcore_control():
    return boto3.client("bedrock-agentcore-control", region_name=REGION)


def embed(text: str) -> list[float]:
    """Embed a single piece of text with the Bedrock embedding model."""
    response = bedrock().invoke_model(
        modelId=EMBED_MODEL,
        body=json.dumps({"inputText": text, "dimensions": EMBED_DIM, "normalize": True}),
    )
    return json.loads(response["body"].read())["embedding"]
