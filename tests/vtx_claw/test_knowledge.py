from __future__ import annotations

from pathlib import Path
import pytest

from vtx_claw.knowledge import HybridRetriever, BM25Store, VectorStore, reciprocal_rank_fusion


def test_hybrid_retrieval(tmp_path: Path):
    r = HybridRetriever(tmp_path)
    r.add("User likes Python and Rust", {"source": "memory", "id": "a"})
    r.add("User drives a Honda car", {"source": "memory", "id": "b"})
    results = r.search("programming languages", top_k=2)
    texts = [x["text"] for x in results]
    assert any("Python" in t for t in texts)
    assert any("Rust" in t for t in texts)


def test_bm25_only(tmp_path: Path):
    store = BM25Store(tmp_path)
    store.add("Apple is red", {"id": "1"})
    store.add("Banana is yellow", {"id": "2"})
    out = store.search("fruit", top_k=2)
    assert len(out) == 2


def test_vector_only(tmp_path: Path):
    store = VectorStore(tmp_path)
    store.add("quick brown fox", {"id": "1"})
    out = store.search("quick fox", top_k=1)
    assert len(out) == 1


def test_fusion_merges_rankings():
    a = [{"text": "a", "meta": {"id": "1"}}]
    b = [{"text": "b", "meta": {"id": "2"}}]
    out = reciprocal_rank_fusion(a, b, k=2)
    assert len(out) == 2
