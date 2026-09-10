"""Search the corpus, and be honest about what came back.

The embedder is loaded once per process and reused. It is a ~130MB ONNX model on
CPU; loading it per query would dominate the latency of the whole request.
"""

from __future__ import annotations

import time
from dataclasses import dataclass

from qdrant_client import QdrantClient

from agentbot.config import (
    COLLECTION,
    EMBED_MODEL,
    QDRANT_URL,
    SCORE_FLOOR,
    TOP_K,
)

_client: QdrantClient | None = None
_embedder = None


def client() -> QdrantClient:
    global _client
    if _client is None:
        _client = QdrantClient(url=QDRANT_URL, timeout=30)
    return _client


def embedder():
    global _embedder
    if _embedder is None:
        from fastembed import TextEmbedding

        _embedder = TextEmbedding(model_name=EMBED_MODEL)
    return _embedder


@dataclass
class Hit:
    """One retrieved chunk."""

    text: str
    citation: str
    doc_id: str
    title: str
    score: float
    corpus_version: str


def embed_query(question: str) -> list[float]:
    # fastembed distinguishes documents from queries. bge models were trained
    # with an instruction prefix on the query side, and query_embed applies it;
    # using embed() for queries costs a few points of recall for no reason.
    return list(embedder().query_embed([question]))[0].tolist()


def search(question: str, top_k: int = TOP_K, floor: float = SCORE_FLOOR) -> tuple[list[Hit], dict]:
    """Return the hits above the floor, and timings worth putting on a span."""
    t0 = time.perf_counter()
    vector = embed_query(question)
    embed_ms = (time.perf_counter() - t0) * 1000

    t1 = time.perf_counter()
    # query_points, not the deprecated search(). Asking for more than top_k so
    # the floor has something to reject -- otherwise "everything passed the
    # threshold" is an artefact of only fetching what you wanted.
    response = client().query_points(
        collection_name=COLLECTION,
        query=vector,
        limit=top_k * 2,
        with_payload=True,
    )
    search_ms = (time.perf_counter() - t1) * 1000

    all_hits = [
        Hit(
            text=p.payload.get("text", ""),
            citation=p.payload.get("citation", p.payload.get("doc_id", "?")),
            doc_id=p.payload.get("doc_id", "?"),
            title=p.payload.get("title", ""),
            score=p.score,
            corpus_version=p.payload.get("corpus_version", "?"),
        )
        for p in response.points
    ]
    kept = [h for h in all_hits if h.score >= floor][:top_k]

    stats = {
        "retrieval.embed_ms": round(embed_ms, 1),
        "retrieval.search_ms": round(search_ms, 1),
        "retrieval.candidates": len(all_hits),
        "retrieval.kept": len(kept),
        "retrieval.top_score": round(all_hits[0].score, 4) if all_hits else 0.0,
        "retrieval.floor": floor,
        "retrieval.corpus_version": kept[0].corpus_version if kept else "?",
    }
    return kept, stats


def build_context(hits: list[Hit]) -> str:
    """Format the hits for the prompt, each labelled with its citation.

    The label is what makes a citation possible: the model can only name a
    source if it was told the name. Numbering them lets it write [1] rather
    than reproducing a filename it may get wrong.
    """
    blocks = []
    for n, hit in enumerate(hits, 1):
        blocks.append(f"[{n}] source: {hit.citation}\n{hit.text}")
    return "\n\n---\n\n".join(blocks)


def collection_status() -> dict:
    """For the startup banner. Never raises -- an unreachable Qdrant is a
    message on screen, not a stack trace before the first question."""
    try:
        info = client().get_collection(COLLECTION)
        return {
            "ok": True,
            "points": info.points_count,
            "collection": COLLECTION,
        }
    except Exception as error:
        return {"ok": False, "error": f"{type(error).__name__}: {error}"[:160]}
