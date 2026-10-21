"""Exercise a running MCP server: list its tools, then call both of them.

Usage: uv run test_retrieval.py [url]     (default http://127.0.0.1:8000/mcp)
"""

import asyncio
import sys

from fastmcp import Client

QUERIES = [
    "how does dropout prevent overfitting?",
    "gradient boosting on sparse data",
    "training agents to play video games from pixels",
]
SNIPPET_CHARS = 200


async def main(url: str) -> None:
    async with Client(url) as client:
        tools = await client.list_tools()
        print(f"tools: {[tool.name for tool in tools]}\n")

        articles = await client.call_tool("list_articles", {})
        print(f"list_articles -> {len(articles.data)} articles")
        for article in articles.data:
            print(f"   {article['title'][:60]}")

        for query in QUERIES:
            hits = await client.call_tool("search_articles", {"query": query, "top_k": 3})
            print(f"\nsearch_articles({query!r})")
            for hit in hits.data:
                print(f"   {hit['score']:<8} {hit['article_title'][:52]} (chunk {hit['chunk_id']})")
                print(f"            {hit['text'][:SNIPPET_CHARS]}...")


if __name__ == "__main__":
    asyncio.run(main(sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8000/mcp"))
