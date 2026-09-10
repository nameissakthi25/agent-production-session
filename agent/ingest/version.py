"""The corpus has a version, and it is computed, not declared.

Why this matters more than it looks: **the corpus is part of your program.**
Change a sentence in a policy document and the assistant's answer changes, with
no code commit, no deploy, and nothing in your git log explaining why last
week's answer was different.

So the version is a hash of the content itself. You cannot forget to bump it,
and two people with the same version have provably the same documents.

It is written into every point in Qdrant. That buys three things:

- an answer can name the corpus version it was drawn from
- re-indexing the same corpus is detectable, and skippable
- an old version can be deleted after a new one is live, which is a
  blue/green swap rather than a gap where the assistant knows nothing
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path


def file_digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def build_manifest(corpus_dir: Path) -> dict:
    """A sorted map of filename -> content hash, plus the version over all of it.

    Sorted because a dict's insertion order would make the version depend on
    the order the filesystem happened to list the files in, which is not a
    property of the corpus.
    """
    files = sorted(corpus_dir.glob("*.md"))
    if not files:
        raise FileNotFoundError(f"no .md files in {corpus_dir}")

    documents = {p.name: file_digest(p) for p in files}

    rolling = hashlib.sha256()
    for name in sorted(documents):
        rolling.update(name.encode())
        rolling.update(documents[name].encode())

    return {
        "version": rolling.hexdigest()[:12],  # short enough to say out loud
        "document_count": len(documents),
        "total_bytes": sum(p.stat().st_size for p in files),
        "documents": documents,
    }


def write_manifest(manifest: dict, path: Path) -> None:
    path.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n")


def read_manifest(path: Path) -> dict | None:
    if not path.exists():
        return None
    return json.loads(path.read_text())


def describe_change(old: dict | None, new: dict) -> str:
    """What changed between two manifests, in words.

    Printed at index time so a re-index is never silent about what it did.
    """
    if old is None:
        return f"first index: {new['document_count']} documents"
    if old["version"] == new["version"]:
        return f"unchanged at {new['version']}"

    before, after = old["documents"], new["documents"]
    added = sorted(set(after) - set(before))
    removed = sorted(set(before) - set(after))
    edited = sorted(d for d in set(before) & set(after) if before[d] != after[d])

    parts = [f"{old['version']} -> {new['version']}"]
    for label, items in (("added", added), ("removed", removed), ("edited", edited)):
        if items:
            shown = ", ".join(items[:4]) + (f" +{len(items) - 4} more" if len(items) > 4 else "")
            parts.append(f"{label}: {shown}")
    return "; ".join(parts)
