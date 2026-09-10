"""Chunking and versioning, asserted. No Qdrant, no embeddings, no GPU.

Both are pure functions of their input, which is exactly the kind of thing that
should be a test rather than something you eyeball in a notebook.
"""

from pathlib import Path

import pytest

from ingest.chunk import MAX_CHARS, MIN_CHARS, chunk_document
from ingest.version import build_manifest, describe_change

CORPUS = Path(__file__).resolve().parent.parent / "corpus"

SAMPLE = """# How to reset your corporate password

**Service:** password-reset-portal

## Before you start

You need access to at least one enrolled MFA factor for this to work at all.

## Steps

1. Go to `https://passwordreset.corplabs.com` from any browser.
2. Enter your corporate username, not your email address.
3. Approve the Okta MFA prompt on your enrolled device.

## Tiny

x
"""


# --- chunking --------------------------------------------------------------

def test_splits_on_headings():
    chunks = chunk_document("password.md", SAMPLE)
    headings = [c.heading for c in chunks]
    assert "Before you start" in headings
    assert "Steps" in headings


def test_every_chunk_carries_its_title_and_heading():
    """A chunk that says "3. Approve the Okta MFA prompt" is useless without
    knowing which article it came from -- for the model and for the citation."""
    for chunk in chunk_document("password.md", SAMPLE):
        assert chunk.title == "How to reset your corporate password"
        assert chunk.title in chunk.text
        if chunk.heading:
            assert chunk.heading in chunk.text


def test_tiny_sections_are_dropped():
    """A stub heading with nothing under it only adds noise to the results."""
    headings = [c.heading for c in chunk_document("password.md", SAMPLE)]
    assert "Tiny" not in headings


def test_short_but_load_bearing_sections_survive():
    """The regression this suite already caught once.

    MIN_CHARS was 80, and "Before you start" is 78 characters -- so a
    prerequisite section was being deleted before it could ever be retrieved.
    Across the real corpus that rule discarded 31 of 256 chunks, mostly the
    document preambles that carry the service and type metadata.
    """
    chunks = chunk_document("password.md", SAMPLE)
    prereq = [c for c in chunks if c.heading == "Before you start"]
    assert prereq, "a short prerequisite section was dropped"
    assert "MFA factor" in prereq[0].text


def test_ordinals_are_stable_and_contiguous():
    """Point ids are derived from (doc_id, ordinal), so re-indexing the same
    document must overwrite the same rows rather than add new ones."""
    chunks = chunk_document("password.md", SAMPLE)
    assert [c.ordinal for c in chunks] == list(range(len(chunks)))


def test_citation_names_the_document_and_section():
    chunks = chunk_document("password.md", SAMPLE)
    steps = next(c for c in chunks if c.heading == "Steps")
    assert steps.citation == "password.md#Steps"


def test_a_procedure_is_not_cut_in_half():
    """The whole reason for splitting on structure: half a numbered procedure,
    retrieved confidently, is worse than nothing."""
    steps = next(c for c in chunk_document("password.md", SAMPLE)
                 if c.heading == "Steps")
    for step in ("1.", "2.", "3."):
        assert step in steps.text


def test_long_sections_are_split_on_paragraphs():
    paragraph = "This sentence is here to take up room. " * 12
    long_doc = "# Title\n\n## Big\n\n" + "\n\n".join([paragraph] * 6)
    chunks = chunk_document("big.md", long_doc)
    assert len(chunks) > 1
    for chunk in chunks:
        # The prefix is added after splitting, so allow for it.
        assert len(chunk.text) <= MAX_CHARS + 200
        assert not chunk.text.endswith(("th", "sen"))   # not mid-word


@pytest.mark.parametrize("doc", sorted(CORPUS.glob("*.md"))[:10])
def test_real_corpus_documents_chunk_cleanly(doc):
    chunks = chunk_document(doc.name, doc.read_text(encoding="utf-8"))
    assert chunks, f"{doc.name} produced no chunks"
    for chunk in chunks:
        assert len(chunk.text) >= MIN_CHARS
        assert chunk.doc_id == doc.name


def test_whole_corpus_chunks():
    total = sum(len(chunk_document(p.name, p.read_text(encoding="utf-8")))
                for p in CORPUS.glob("*.md"))
    # A sanity band, not an exact figure: it should be several chunks per
    # document, and if it collapses to roughly one the heading split broke.
    assert total > 100, f"only {total} chunks -- did the heading split stop working?"


# --- versioning ------------------------------------------------------------

def test_version_is_stable_for_the_same_content():
    assert build_manifest(CORPUS)["version"] == build_manifest(CORPUS)["version"]


def test_version_covers_every_document(tmp_path):
    (tmp_path / "a.md").write_text("# A\n\n## S\n\n" + "x" * 200)
    first = build_manifest(tmp_path)

    (tmp_path / "b.md").write_text("# B\n\n## S\n\n" + "y" * 200)
    second = build_manifest(tmp_path)

    assert first["version"] != second["version"]
    assert second["document_count"] == 2


def test_editing_one_document_changes_the_version(tmp_path):
    """The point of the whole mechanism: the corpus is part of the program, and
    editing it must be as visible as editing code."""
    doc = tmp_path / "a.md"
    doc.write_text("# A\n\n## S\n\n" + "x" * 200)
    before = build_manifest(tmp_path)

    doc.write_text("# A\n\n## S\n\n" + "x" * 199 + "z")
    after = build_manifest(tmp_path)

    assert before["version"] != after["version"]
    assert "edited" in describe_change(before, after)
    assert "a.md" in describe_change(before, after)


def test_version_ignores_filesystem_order(tmp_path):
    """Sorted on purpose -- otherwise the version depends on the order the
    filesystem happened to list the files in, which is not a property of the
    corpus."""
    for name in ("z.md", "a.md", "m.md"):
        (tmp_path / name).write_text(f"# {name}\n\n## S\n\n" + "x" * 200)
    version = build_manifest(tmp_path)["version"]
    for name in ("z.md", "a.md", "m.md"):
        (tmp_path / name).touch()          # changes mtime, not content
    assert build_manifest(tmp_path)["version"] == version


def test_describe_change_reports_no_change():
    manifest = build_manifest(CORPUS)
    assert "unchanged" in describe_change(manifest, manifest)


def test_empty_corpus_is_an_error(tmp_path):
    with pytest.raises(FileNotFoundError):
        build_manifest(tmp_path)
