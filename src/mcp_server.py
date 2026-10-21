"""FastMCP server exposing semantic search over the article collection in S3 Vectors.

Usage: aws-vault exec "$AWS_PROFILE" -- uv run src/mcp_server.py
       (Streamable HTTP on 0.0.0.0:8000/mcp)
"""

from fastmcp import FastMCP

from common import ARTICLES, VECTOR_BUCKET, VECTOR_INDEX, embed, s3vectors

MAX_TOP_K = 30  # S3 Vectors caps topK at 30 per query.

mcp = FastMCP("article-search")


@mcp.tool
def list_articles() -> list[dict]:
    """List the articles available in the searchable dataset."""
    return [{"title": article.title, "url": article.url} for article in ARTICLES]


@mcp.tool
def search_articles(query: str, top_k: int = 5) -> list[dict]:
    """Semantically search the articles and return the most relevant chunks.

    Args:
        query: Natural language question or topic to search for.
        top_k: How many chunks to return (1-30).
    """
    response = s3vectors().query_vectors(
        vectorBucketName=VECTOR_BUCKET,
        indexName=VECTOR_INDEX,
        queryVector={"float32": embed(query)},
        topK=max(1, min(top_k, MAX_TOP_K)),
        returnMetadata=True,
        returnDistance=True,
    )
    return [
        {
            "article_title": vector["metadata"]["article_title"],
            "article_url": vector["metadata"]["article_url"],
            "chunk_id": vector["metadata"]["chunk_id"],
            "text": vector["metadata"]["text"],
            # Cosine distance is 1 - cosine similarity; report similarity, it reads better.
            "score": round(1 - vector["distance"], 4),
        }
        for vector in response["vectors"]
    ]


if __name__ == "__main__":
    mcp.run(transport="http", host="0.0.0.0", port=8000, path="/mcp", stateless=True)
