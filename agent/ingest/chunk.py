"""Turn a markdown document into retrievable pieces.

Chunking is the part of RAG people skip, and it decides more about answer
quality than the choice of vector database does. Two rules here:

1. **Split on structure, not on character count.** These documents are written
   with `##` headings — "Steps", "If it does not work", "Escalation" — and each
   heading is a coherent answer to a different question. Cutting every 500
   characters would slice a numbered procedure in half, and half a procedure
   retrieved confidently is worse than nothing.

2. **Carry the parents down.** A chunk that says "3. Approve the Okta MFA
   prompt" is useless without knowing which article and which section it came
   from, both for the model and for the citation. So every chunk is prefixed
   with its document title and heading path.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

# A chunk large enough to hold a whole procedure, small enough that several fit
# in the prompt. Characters, not tokens: it is an upper bound, not a budget.
MAX_CHARS = 1200
# Below this a chunk is a stub heading with nothing under it, which only adds
# noise to the results.
#
# MEASURED, and this number was wrong first time. At 80 it silently discarded
# 31 of 256 chunks -- 12% of the corpus -- and they were not junk: they were the
# document preambles carrying the "**Service:** ... **Type:** ..." metadata, and
# the short "Before you start" prerequisite sections. Exactly the text that
# makes a document findable and a procedure safe to follow.
#
# On this corpus 40 drops nothing and 60 drops one chunk. A minimum chunk size
# is a delete, and a silent delete deserves a measurement rather than a guess.
MIN_CHARS = 40


@dataclass
class Chunk:
    """One retrievable piece, and where it came from."""

    doc_id: str          # the filename, which is what a citation shows
    title: str           # the document's H1
    heading: str         # the section it came from, "" for the preamble
    ordinal: int         # position within the document, for stable ids
    text: str            # what actually gets embedded
    meta: dict = field(default_factory=dict)

    @property
    def citation(self) -> str:
        return f"{self.doc_id}#{self.heading}" if self.heading else self.doc_id


def _split_sections(body: str) -> list[tuple[str, str]]:
    """Split on `##` headings. Returns (heading, text) pairs."""
    parts = re.split(r"^##\s+(.+)$", body, flags=re.M)
    # re.split with one group gives [preamble, h1, body1, h2, body2, ...]
    sections: list[tuple[str, str]] = []
    preamble = parts[0].strip()
    if preamble:
        sections.append(("", preamble))
    for i in range(1, len(parts) - 1, 2):
        sections.append((parts[i].strip(), parts[i + 1].strip()))
    return sections


def _split_long(text: str, limit: int = MAX_CHARS) -> list[str]:
    """Break an over-long section on blank lines, never mid-sentence."""
    if len(text) <= limit:
        return [text]
    pieces, current = [], ""
    for para in text.split("\n\n"):
        if current and len(current) + len(para) + 2 > limit:
            pieces.append(current.strip())
            current = para
        else:
            current = f"{current}\n\n{para}" if current else para
    if current.strip():
        pieces.append(current.strip())
    return pieces


def chunk_document(doc_id: str, raw: str) -> list[Chunk]:
    """Chunk one markdown document."""
    title_match = re.search(r"^#\s+(.+)$", raw, flags=re.M)
    title = title_match.group(1).strip() if title_match else doc_id
    body = raw[title_match.end():] if title_match else raw

    chunks: list[Chunk] = []
    for heading, section in _split_sections(body):
        for piece in _split_long(section):
            if len(piece) < MIN_CHARS:
                continue
            # The prefix is embedded too, on purpose: it puts the words
            # "password reset" into a chunk whose own text only says "Steps".
            prefix = f"{title}" + (f" — {heading}" if heading else "")
            chunks.append(
                Chunk(
                    doc_id=doc_id,
                    title=title,
                    heading=heading,
                    ordinal=len(chunks),
                    text=f"{prefix}\n\n{piece}",
                )
            )
    return chunks
