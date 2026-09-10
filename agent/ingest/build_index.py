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

from agentbot.config import (
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
    parser.add_argument("--dry-run", action="store_true",
                        help="chunk and report, but do not embed or load")
    parser.add_argument("--show", type=int, default=0, metavar="N",
                        help="print the first N chunks in full")
    args = parser.parse_args()

    corpus = Path(CORPUS_DIR)
    manifest = build_manifest(corpus)
    previous = read_manifest(Path(MANIFEST_PATH))

    print(f"corpus  : {corpus}")
    print(f"version : {manifest['version']}  "
          f"({manifest['document_count']} documents, "
          f"{manifest['total_bytes'] / 1024:.0f} KiB)")
    print(f"change  : {describe_change(previous, manifest)}")

    if args.dry_run:
        # Chunk and report, touching nothing. Useful before an ingest you are
        # about to do in front of people, and the only way to see what the
        # chunker decided without loading it.
        chunks = []
        for path in sorted(corpus.glob("*.md")):
            chunks.extend(chunk_document(path.name, path.read_text(encoding="utf-8")))
        sizes = sorted(len(c.text) for c in chunks)
        print("\nDRY RUN -- nothing was embedded or loaded")
        print(f"chunked : {len(chunks)} chunks from {manifest['document_count']} documents")
        print(f"          {sizes[0]} / {sizes[len(sizes) // 2]} / {sizes[-1]} chars "
              f"(min / median / max)")
        per_doc = {}
        for c in chunks:
            per_doc[c.doc_id] = per_doc.get(c.doc_id, 0) + 1
        busiest = sorted(per_doc.items(), key=lambda kv: -kv[1])[:5]
        print("          most-chunked: "
              + ", ".join(f"{d} ({n})" for d, n in busiest))
        for c in chunks[: args.show]:
            print(f"\n--- chunk {c.ordinal} of {c.doc_id}  [{c.citation}]")
            print(c.text)
        return 0

    client = QdrantClient(url=QDRANT_URL, timeout=60)
    ensure_collection(client, args.recreate)

    unchanged = previous is not None and previous["version"] == manifest["version"]
    if unchanged and not args.force and not args.recreate:
        info = client.get_collection(COLLECTION)
        if info.points_count:
            print(f"\nnothing to do -- corpus unchanged and {COLLECTION!r} already "
                  f"has {info.points_count} points. Pass --force to re-embed anyway.")
            return 0
        # The manifest says "already indexed" but the collection is empty. That
        # happens because the manifest lives on a volume and the vectors live in
        # Qdrant, and the two can be cleaned separately -- `docker compose down`
        # without -v, a dropped collection, a restored backup. Trusting the
        # manifest alone here would leave you with an empty index and a script
        # cheerfully reporting nothing to do.
        print(f"\nmanifest says {manifest['version']} is already indexed, but "
              f"{COLLECTION!r} is empty -- indexing anyway.")

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
