"""Exercise the MCP server deployed to Bedrock AgentCore Runtime.

AgentCore fronts the container with an IAM-authenticated endpoint, so every MCP request
has to be SigV4-signed -- that is the only difference from talking to it locally.

Usage:
  aws-vault exec "$AWS_PROFILE" -- uv run tests/test_agentcore.py
                                                        (run default queries)
  aws-vault exec "$AWS_PROFILE" -- uv run tests/test_agentcore.py \
    --query "your question"                         (run a single query)
"""

import argparse
import asyncio
import os
from urllib.parse import quote

import boto3
import httpx
from botocore.auth import SigV4Auth
from botocore.awsrequest import AWSRequest
from fastmcp import Client
from fastmcp.client.transports import StreamableHttpTransport

REGION = os.environ.get("AWS_REGION", "us-east-1")
ARN = os.environ["AGENT_RUNTIME_ARN"]
SERVICE = "bedrock-agentcore"
DEFAULT_QUERIES = [
    "how does dropout prevent overfitting?",
    "gradient boosting on sparse data",
    "training agents to play video games from pixels",
]
SNIPPET_CHARS = 160


class SigV4(httpx.Auth):
    """Sign each outgoing request with the caller's AWS credentials."""

    requires_request_body = True

    def __init__(self, service: str, region: str):
        self.credentials = boto3.Session().get_credentials()
        self.service = service
        self.region = region

    def auth_flow(self, request):
        signed = AWSRequest(
            method=request.method,
            url=str(request.url),
            data=request.content,
            # Signing only the headers we control keeps the signature stable; httpx adds
            # the rest (content-length, accept-encoding) after signing.
            headers={"content-type": request.headers.get("content-type", "application/json")},
        )
        SigV4Auth(self.credentials.get_frozen_credentials(), self.service, self.region).add_auth(signed)
        request.headers.update(dict(signed.headers))
        yield request


async def main(queries: list[str], top_k: int, list_articles: bool) -> None:
    url = (
        f"https://{SERVICE}.{REGION}.amazonaws.com"
        f"/runtimes/{quote(ARN, safe='')}/invocations?qualifier=DEFAULT"
    )
    print(f"endpoint: {url}\n")

    transport = StreamableHttpTransport(url=url, auth=SigV4(SERVICE, REGION))
    async with Client(transport) as client:
        tools = await client.list_tools()
        print(f"tools: {[tool.name for tool in tools]}\n")

        if list_articles:
            articles = await client.call_tool("list_articles", {})
            print(f"list_articles -> {len(articles.data)} articles")
            for article in articles.data:
                print(f"   {article['title'][:60]}\n")

        for query in queries:
            hits = await client.call_tool("search_articles", {"query": query, "top_k": top_k})
            print(f"search_articles({query!r}) — top {top_k}")
            for hit in hits.data:
                print(f"   {hit['score']:<8} {hit['article_title'][:52]} (chunk {hit['chunk_id']})")
                print(f"            {hit['text'][:SNIPPET_CHARS]}...")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Query the deployed AgentCore MCP server",
        epilog="Examples:\n  aws-vault exec \"$AWS_PROFILE\" -- uv run tests/test_agentcore.py\n  aws-vault exec \"$AWS_PROFILE\" -- uv run tests/test_agentcore.py --query 'how does attention work?' --top-k 5\n  aws-vault exec \"$AWS_PROFILE\" -- uv run tests/test_agentcore.py --list-articles",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--query",
        type=str,
        help="Single query to run (if not provided, runs default queries)",
    )
    parser.add_argument(
        "--top-k",
        type=int,
        default=3,
        help="Number of results to return per query (default: 3)",
    )
    parser.add_argument(
        "--list-articles",
        action="store_true",
        help="List all articles in the index before searching",
    )
    args = parser.parse_args()

    queries = [args.query] if args.query else DEFAULT_QUERIES
    asyncio.run(main(queries, args.top_k, args.list_articles))
