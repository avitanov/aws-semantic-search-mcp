"""Shared data-loading helpers for the article-search application."""

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class Article:
    file: str
    title: str
    url: str
    download_url: str


def read_json(path: Path) -> Any:
    """Read and decode a JSON file."""
    return json.loads(path.read_text())


def load_articles(path: Path | None = None) -> tuple[Article, ...]:
    """Load the article manifest into typed records."""
    manifest = path or Path(__file__).resolve().parent.parent / "data" / "articles.json"
    return tuple(Article(**record) for record in read_json(manifest))
