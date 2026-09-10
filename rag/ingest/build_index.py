"""Create the collection, chunk the corpus, embed it, load it.

Run it as a script. It is idempotent: the same corpus produces the same version
and the same point ids, so re-running replaces rather than duplicates.

    uv run python -m ingest.build_index
    uv run python -m ingest.build_index --recreate   # start the collection over
"""

from __future__ import annotations

import argparse
import sys
import time
import uuid
from pathlib import Path

from qdrant_client import QdrantClient
from qdrant_client.models import Distance, PointStruct, VectorParams

from ragbot.config import (
    COLLECTION,
    CORPUS_DIR,
    EMBED_DIM,
    EMBED_MODEL,
    MANIFEST_PATH,
    QDRANT_URL,
)

from .chunk import chunk_document
from .version import build_manifest, describe_change, read_manifest, write_manifest

# A fixed namespace makes point ids a pure function of (document, ordinal), so
# re-indexing overwrites the same rows instead of piling up duplicates that all
# match the same query.
NAMESPACE = uuid.UUID("6f1f1f2e-0000-4000-8000-000000000000")


def point_id(doc_id: str, ordinal: int) -> str:
    return str(uuid.uuid5(NAMESPACE, f"{doc_id}:{ordinal}"))


def ensure_collection(client: QdrantClient, recreate: bool) -> None:
    """Create the collection if it is missing, and say what was decided.

    Two choices are baked in here and cannot be changed later without
    re-indexing everything:

      size     384, because that is what bge-small emits. A mismatch here is
               rejected at upsert time, which is the good outcome -- the bad one
               is a collection built at the wrong size that silently returns
               nonsense.
      distance COSINE, because these embeddings are normalised, and cosine on
               normalised vectors is the metric the model was trained for.
    """
    exists = client.collection_exists(COLLECTION)

    if exists and recreate:
        print(f"dropping collection {COLLECTION!r}")
        client.delete_collection(COLLECTION)
        exists = False

    if not exists:
        client.create_collection(
            collection_name=COLLECTION,
            vectors_config=VectorParams(size=EMBED_DIM, distance=Distance.COSINE),
        )
        print(f"created collection {COLLECTION!r}: {EMBED_DIM} dims, cosine")
    else:
        info = client.get_collection(COLLECTION)
        print(f"collection {COLLECTION!r} exists with {info.points_count} points")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--recreate", action="store_true",
                        help="drop the collection first")
    parser.add_argument("--force", action="store_true",
                        help="re-embed even if the corpus version is unchanged")
    args = parser.parse_args()

    corpus = Path(CORPUS_DIR)
    manifest = build_manifest(corpus)
    previous = read_manifest(Path(MANIFEST_PATH))

    print(f"corpus  : {corpus}")
    print(f"version : {manifest['version']}  "
          f"({manifest['document_count']} documents, "
          f"{manifest['total_bytes'] / 1024:.0f} KiB)")
    print(f"change  : {describe_change(previous, manifest)}")

    client = QdrantClient(url=QDRANT_URL, timeout=60)
    ensure_collection(client, args.recreate)

    unchanged = previous is not None and previous["version"] == manifest["version"]
    if unchanged and not args.force and not args.recreate:
        info = client.get_collection(COLLECTION)
        if info.points_count:
            print("\nnothing to do -- corpus unchanged and the collection has "
                  "points. Pass --force to re-embed anyway.")
            return 0

    # --- chunk ------------------------------------------------------------
    chunks = []
    for path in sorted(corpus.glob("*.md")):
        chunks.extend(chunk_document(path.name, path.read_text(encoding="utf-8")))

    sizes = sorted(len(c.text) for c in chunks)
    print(f"\nchunked : {len(chunks)} chunks from {manifest['document_count']} documents")
    print(f"          {sizes[0]} / {sizes[len(sizes) // 2]} / {sizes[-1]} chars "
          f"(min / median / max)")

    # --- embed ------------------------------------------------------------
    # Imported here, not at module scope: loading the ONNX model takes a few
    # seconds and `--help` should not pay for it.
    from fastembed import TextEmbedding

    print(f"\nembedding with {EMBED_MODEL} on CPU...")
    started = time.perf_counter()
    embedder = TextEmbedding(model_name=EMBED_MODEL)
    vectors = list(embedder.embed([c.text for c in chunks]))
    elapsed = time.perf_counter() - started

    if len(vectors[0]) != EMBED_DIM:
        raise SystemExit(
            f"model emits {len(vectors[0])} dims but the collection expects "
            f"{EMBED_DIM}. Fix EMBED_DIM, then --recreate."
        )
    print(f"embedded: {len(vectors)} vectors in {elapsed:.1f}s "
          f"({len(vectors) / elapsed:.0f}/s), {len(vectors[0])} dims")

    # --- load -------------------------------------------------------------
    points = [
        PointStruct(
            id=point_id(c.doc_id, c.ordinal),
            vector=vector.tolist(),
            payload={
                "text": c.text,
                "doc_id": c.doc_id,
                "title": c.title,
                "heading": c.heading,
                "citation": c.citation,
                # Stamped on every point, so an answer can name the corpus it
                # came from and an old version can be swapped out cleanly.
                "corpus_version": manifest["version"],
            },
        )
        for c, vector in zip(chunks, vectors, strict=True)
    ]

    client.upsert(collection_name=COLLECTION, points=points, wait=True)
    info = client.get_collection(COLLECTION)
    print(f"\nloaded  : {info.points_count} points in {COLLECTION!r}")

    write_manifest(manifest, Path(MANIFEST_PATH))
    print(f"manifest: {MANIFEST_PATH}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
