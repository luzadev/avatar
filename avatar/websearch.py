"""Ricerca web con Brave Search (per il motore locale)."""
from __future__ import annotations

import re

import requests


def brave_search(query: str, api_key: str, count: int = 6) -> list[dict]:
    res = requests.get(
        "https://api.search.brave.com/res/v1/web/search",
        params={"q": query, "count": count, "country": "IT", "search_lang": "it"},
        headers={"Accept": "application/json", "X-Subscription-Token": api_key},
        timeout=15,
    )
    res.raise_for_status()
    hits = []
    for r in (res.json().get("web") or {}).get("results") or []:
        if r.get("url"):
            hits.append({
                "title": r.get("title") or r["url"],
                "url": r["url"],
                "description": re.sub(r"<[^>]+>", "", r.get("description") or ""),
            })
    return hits
