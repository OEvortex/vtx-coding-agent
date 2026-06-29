from __future__ import annotations

import json
import logging
import math
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


class BM25Store:
    def __init__(self, store_dir: Path | None = None) -> None:
        self._dir = store_dir or Path.home() / ".vtx" / "claw" / "rag" / "bm25"
        self._dir.mkdir(parents=True, exist_ok=True)
        self._docs: list[dict[str, Any]] = []
        self._load()

    def _load(self) -> None:
        p = self._dir / "docs.json"
        if p.exists():
            try:
                self._docs = json.loads(p.read_text())
            except Exception:
                logger.exception("Failed to load BM25 store")

    def add(self, text: str, meta: dict[str, Any] | None = None) -> None:
        self._docs.append({"text": text, "meta": meta or {}})
        self._persist()

    def search(self, query: str, top_k: int = 5) -> list[dict[str, Any]]:
        terms = [t.lower() for t in query.split() if t]
        scored = []
        for doc in self._docs:
            text = doc.get("text", "").lower()
            score = sum(text.count(t) for t in terms)
            scored.append((score, doc))
        scored.sort(key=lambda x: x[0], reverse=True)
        return [d for _, d in scored[:top_k]]

    def _persist(self) -> None:
        p = self._dir / "docs.json"
        p.write_text(json.dumps(self._docs, indent=2))


class VectorStore:
    def __init__(self, store_dir: Path | None = None) -> None:
        self._dir = store_dir or Path.home() / ".vtx" / "claw" / "rag" / "vectors"
        self._dir.mkdir(parents=True, exist_ok=True)
        self._docs: list[dict[str, Any]] = []
        self._load()

    def _load(self) -> None:
        p = self._dir / "docs.json"
        if p.exists():
            try:
                self._docs = json.loads(p.read_text())
            except Exception:
                logger.exception("Failed to load vector store")

    def add(self, text: str, meta: dict[str, Any] | None = None) -> None:
        embedding = _simple_embedding(text)
        self._docs.append({"text": text, "embedding": embedding, "meta": meta or {}})
        self._persist()

    def search(self, query: str, top_k: int = 5) -> list[dict[str, Any]]:
        q_vec = _simple_embedding(query)
        scored = []
        for doc in self._docs:
            sim = _cosine_similarity(q_vec, doc.get("embedding", []))
            scored.append((sim, doc))
        scored.sort(key=lambda x: x[0], reverse=True)
        return [d for _, d in scored[:top_k] if _ > 0.0]

    def _persist(self) -> None:
        p = self._dir / "docs.json"
        p.write_text(json.dumps(self._docs, indent=2))


def _simple_embedding(text: str) -> list[float]:
    words = text.lower().split()
    dim = 64
    vec = [0.0] * dim
    for i, w in enumerate(words):
        idx = hash(w) % dim
        vec[idx] += 1.0 / (i + 1)
    norm = math.sqrt(sum(v * v for v in vec)) or 1.0
    return [v / norm for v in vec]


def _cosine_similarity(a: list[float], b: list[float]) -> float:
    if not a or not b:
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    if na == 0 or nb == 0:
        return 0.0
    return dot / (na * nb)


def reciprocal_rank_fusion(
    list_a: list[dict[str, Any]], list_b: list[dict[str, Any]], k: int = 5
) -> list[dict[str, Any]]:
    scores: dict[str, tuple[float, dict[str, Any]]] = {}
    for rank, doc in enumerate(list_a, start=1):
        key = doc.get("meta", {}).get("id") or doc.get("text", "")[:64]
        scores[key] = (scores.get(key, (0.0, doc))[0] + 1.0 / (rank + k), doc)
    for rank, doc in enumerate(list_b, start=1):
        key = doc.get("meta", {}).get("id") or doc.get("text", "")[:64]
        scores[key] = (scores.get(key, (0.0, doc))[0] + 1.0 / (rank + k), doc)
    ranked = sorted(scores.values(), key=lambda x: x[0], reverse=True)
    return [d for _, d in ranked[:k]]


class HybridRetriever:
    def __init__(self, store_dir: Path | None = None, top_k: int = 5) -> None:
        self._top_k = top_k
        self._bm25 = BM25Store(store_dir / "bm25" if store_dir else None)
        self._vec = VectorStore(store_dir / "vectors" if store_dir else None)

    def add(self, text: str, meta: dict[str, Any] | None = None) -> None:
        self._bm25.add(text, meta)
        self._vec.add(text, meta)

    def search(self, query: str, top_k: int | None = None) -> list[dict[str, Any]]:
        k = top_k or self._top_k
        bm25_res = self._bm25.search(query, k * 2)
        vec_res = self._vec.search(query, k * 2)
        return reciprocal_rank_fusion(bm25_res, vec_res, k)
