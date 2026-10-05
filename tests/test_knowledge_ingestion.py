"""Extraction, chunking, and offset integrity.

All offline. The invariant these exist to protect is that a citation resolves
to the exact stored passage - `body[start:end] == content` - because a citation
that points at the wrong text looks like evidence while being none.
"""

from __future__ import annotations

import pytest

from app.knowledge.chunking import (
    Chunk,
    ChunkingError,
    chunk_body,
    coverage,
    verify_offsets,
)
from app.knowledge.extraction import (
    MAX_FILE_BYTES,
    ExtractionError,
    content_type_for,
    extract,
    normalize_body,
)

# ---------------------------------------------------------------------------
# Content types and validation
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("filename", "expected"),
    [
        ("guide.pdf", "application/pdf"),
        ("guide.md", "text/markdown"),
        ("guide.markdown", "text/markdown"),
        ("guide.txt", "text/plain"),
        ("guide.html", "text/html"),
        ("guide.htm", "text/html"),
        ("guide.csv", "text/csv"),
        ("GUIDE.PDF", "application/pdf"),
    ],
)
def test_approved_extensions_resolve(filename: str, expected: str) -> None:
    assert content_type_for(filename) == expected


@pytest.mark.parametrize("filename", ["macro.docm", "app.exe", "archive.zip", "noext"])
def test_unapproved_extensions_are_refused(filename: str) -> None:
    with pytest.raises(ExtractionError, match="unsupported file type|not an approved"):
        content_type_for(filename)


def test_a_declared_type_outside_the_approved_list_is_refused() -> None:
    """An uploader must not be able to smuggle a type past the extension check."""
    with pytest.raises(ExtractionError, match="not an approved"):
        content_type_for("guide.pdf", "application/x-msdownload")


def test_a_declared_type_with_parameters_is_accepted() -> None:
    assert content_type_for("guide.csv", "text/csv; charset=utf-8") == "text/csv"


def test_an_empty_file_is_refused() -> None:
    with pytest.raises(ExtractionError, match="empty"):
        extract(b"", "guide.md")


def test_an_oversized_file_is_refused() -> None:
    with pytest.raises(ExtractionError, match="larger than"):
        extract(b"x" * (MAX_FILE_BYTES + 1), "guide.txt")


def test_undecodable_bytes_are_refused_not_mangled() -> None:
    with pytest.raises(ExtractionError, match="not valid UTF-8"):
        extract(b"\xff\xfe\x00broken", "guide.txt")


def test_an_unapproved_default_category_is_refused() -> None:
    with pytest.raises(ExtractionError, match="not an approved category"):
        extract(b"Some text", "guide.txt", default_category="urgent")


# ---------------------------------------------------------------------------
# Normalization
# ---------------------------------------------------------------------------


def test_normalization_is_deterministic_and_idempotent() -> None:
    """Offsets are measured against the normalized body, so it must be stable."""
    raw = "Line  one\r\n\r\n\r\n   Line two\t\tend   "
    once = normalize_body(raw)
    assert normalize_body(once) == once
    assert once == "Line one\n\nLine two end"


# ---------------------------------------------------------------------------
# Per-format extraction
# ---------------------------------------------------------------------------


def test_markdown_headings_become_separate_articles_with_their_own_category() -> None:
    """Merging heading sections would also merge their category labels."""
    payload = (
        b"# Resetting your password\n\n**Category:** account\n\nOpen Settings.\n\n"
        b"# Duplicate charge\n\n**Category:** billing\n\nWe refund duplicates.\n"
    )
    articles = extract(payload, "help.md").articles

    assert len(articles) == 2
    assert [a.category for a in articles] == ["account", "billing"]
    assert articles[0].title == "Resetting your password"
    assert articles[1].title == "Duplicate charge"


def test_unstructured_text_packs_paragraphs_into_sections() -> None:
    payload = b"First paragraph.\n\nSecond paragraph.\n\nThird paragraph.\n"
    articles = extract(payload, "notes.txt", default_category="technical").articles

    assert len(articles) == 1
    assert articles[0].category == "technical"


def test_html_drops_script_and_style_rather_than_indexing_markup() -> None:
    payload = (
        b"<html><head><style>p{color:red}</style></head><body>"
        b"<h1>Sync issues</h1><p>Reconnect the device.</p>"
        b"<script>alert('x')</script></body></html>"
    )
    articles = extract(payload, "sync.html", default_category="technical").articles

    body = " ".join(a.body for a in articles)
    assert "Reconnect the device." in body
    assert "color:red" not in body
    assert "alert" not in body


def test_csv_rows_become_articles_with_curated_terms() -> None:
    payload = (
        b"article_id,title,body,category,keywords\n"
        b'KB-001,Refunds,We refund within five days.,billing,"refund;receipt;money back"\n'
    )
    articles = extract(payload, "kb.csv").articles

    assert len(articles) == 1
    assert articles[0].external_ref == "KB-001"
    assert articles[0].search_terms == ("refund", "receipt", "money back")


def test_csv_missing_required_columns_is_refused() -> None:
    with pytest.raises(ExtractionError, match="missing required column"):
        extract(b"title,category\nRefunds,billing\n", "kb.csv")


def test_csv_with_an_unapproved_category_is_refused() -> None:
    with pytest.raises(ExtractionError, match="approved categories"):
        extract(b"title,body,category\nX,Y,urgent\n", "kb.csv")


def test_csv_with_an_empty_body_is_refused() -> None:
    with pytest.raises(ExtractionError, match="empty title or body"):
        extract(b"title,body\nRefunds,\n", "kb.csv")


def test_curated_terms_are_bounded_to_the_database_limit() -> None:
    terms = ";".join(f"term{i}" for i in range(50))
    payload = f'title,body,keywords\nX,Some body text,"{terms}"\n'.encode()

    assert len(extract(payload, "kb.csv").articles[0].search_terms) == 24


def test_uncategorised_sections_produce_a_visible_warning() -> None:
    result = extract(b"# Heading\n\nSome guidance text.\n", "help.md")

    assert result.warnings
    assert "no category" in result.warnings[0]


# ---------------------------------------------------------------------------
# Chunking and offsets
# ---------------------------------------------------------------------------


BODIES = [
    "Short body.",
    "One paragraph.\n\nTwo paragraph.\n\nThree paragraph.",
    "A sentence. " * 300,
    "word " * 900,
    "# Heading\n\n" + ("Detailed guidance. " * 200),
    "Ünïcödé bödy with accents. " * 80,
]


@pytest.mark.parametrize("body", BODIES)
def test_every_chunk_slices_back_to_the_body(body: str) -> None:
    """The citation invariant. If this fails, every citation is suspect."""
    chunks = chunk_body(body)

    for chunk in chunks:
        assert body[chunk.start_offset : chunk.end_offset] == chunk.content


@pytest.mark.parametrize("body", BODIES)
def test_chunking_covers_all_meaningful_text(body: str) -> None:
    assert coverage(body, chunk_body(body)) == pytest.approx(1.0)


@pytest.mark.parametrize("body", BODIES)
def test_chunk_indexes_are_contiguous_from_zero(body: str) -> None:
    chunks = chunk_body(body)
    assert [chunk.index for chunk in chunks] == list(range(len(chunks)))


def test_empty_and_blank_bodies_produce_no_chunks() -> None:
    for body in ("", "   ", "\n\n\t\n"):
        assert chunk_body(body) == []


def test_chunking_is_deterministic() -> None:
    body = "A paragraph. " * 200
    first = chunk_body(body)
    second = chunk_body(body)

    assert [(c.start_offset, c.end_offset) for c in first] == [
        (c.start_offset, c.end_offset) for c in second
    ]


def test_overlap_never_reaches_before_the_body_start() -> None:
    chunks = chunk_body("Para one.\n\n" + "Filler text. " * 200)

    assert all(chunk.start_offset >= 0 for chunk in chunks)
    assert chunks[0].start_offset == 0


def test_invalid_chunking_parameters_are_refused() -> None:
    with pytest.raises(ChunkingError, match="too small to cite"):
        chunk_body("text", max_chars=10)
    with pytest.raises(ChunkingError, match="overlap_chars"):
        chunk_body("text", max_chars=100, overlap_chars=100)


def test_verify_offsets_detects_a_lying_chunk() -> None:
    """The guard must fail on exactly the defect it exists to catch."""
    body = "The real stored text."
    tampered = [Chunk(index=0, content="Something else", start_offset=0, end_offset=21)]

    with pytest.raises(ChunkingError, match="wrong passage"):
        verify_offsets(body, tampered)


def test_verify_offsets_detects_out_of_range_offsets() -> None:
    body = "Short."
    with pytest.raises(ChunkingError, match="outside the body"):
        verify_offsets(body, [Chunk(index=0, content="Short.", start_offset=0, end_offset=99)])
