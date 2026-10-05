"""Turn an uploaded file into normalized articles.

Every supported source ends up as the same thing: a title, a normalized body,
and an optional category. The body is what gets stored and what chunk offsets
are measured against, so normalization happens exactly once, here. A later
stage that re-normalizes would invalidate every citation offset.

Uploaded files are untrusted input. Size, type, and decodability are checked
before any parsing, and a parser failure is reported as a rejected upload
rather than a partially ingested document.
"""

from __future__ import annotations

import csv
import io
import re
from dataclasses import dataclass, field
from html.parser import HTMLParser
from pathlib import Path

from app.knowledge.text import VALID_CATEGORIES

# Matches the approved list in the C04 `knowledge_sources.content_type` check.
SUPPORTED_CONTENT_TYPES: dict[str, str] = {
    "application/pdf": ".pdf",
    "text/markdown": ".md",
    "text/plain": ".txt",
    "text/html": ".html",
    "text/csv": ".csv",
}

EXTENSION_CONTENT_TYPES: dict[str, str] = {
    ".pdf": "application/pdf",
    ".md": "text/markdown",
    ".markdown": "text/markdown",
    ".txt": "text/plain",
    ".text": "text/plain",
    ".html": "text/html",
    ".htm": "text/html",
    ".csv": "text/csv",
}

MAX_FILE_BYTES = 10 * 1024 * 1024
MAX_SECTION_CHARS = 900
# Matches the C04 check constraint on knowledge_articles.search_terms.
MAX_SEARCH_TERMS = 24
REQUIRED_CSV_COLUMNS = {"title", "body"}

# Matches the MVP's per-section override, so a Markdown file ingested through
# either path is categorised identically.
CATEGORY_PATTERN = re.compile(
    r"(?:\*\*)?Category\s*:(?:\*\*)?\s*(technical|billing|account)\b", re.IGNORECASE
)
HEADING_PATTERN = re.compile(r"(?:^|\n)#{1,3}\s+(.+)")


class ExtractionError(ValueError):
    """An upload that cannot be ingested. Always surfaced to the uploader."""


@dataclass(frozen=True)
class ExtractedArticle:
    """One article ready to be stored, chunked, and embedded."""

    external_ref: str
    title: str
    body: str
    category: str | None = None
    ordinal: int = 0
    # Curated vocabulary the document itself may not use. Stored separately
    # from the body so citation offsets stay true.
    search_terms: tuple[str, ...] = ()


@dataclass(frozen=True)
class ExtractionResult:
    filename: str
    content_type: str
    articles: tuple[ExtractedArticle, ...]
    warnings: tuple[str, ...] = field(default=())


class _TextExtractor(HTMLParser):
    """HTML to text using the standard library.

    Deliberately no third-party HTML parser: this runs over untrusted uploads,
    and the standard library keeps the dependency surface where it is. Script
    and style content is dropped rather than indexed - it is not knowledge, and
    indexing it would pollute retrieval with markup tokens.
    """

    _SKIP = {"script", "style", "noscript", "template"}
    _BREAK = {
        "p", "div", "br", "li", "tr", "section", "article", "header", "footer",
        "h1", "h2", "h3", "h4", "h5", "h6",
    }

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self._parts: list[str] = []
        self._skip_depth = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in self._SKIP:
            self._skip_depth += 1
        elif tag in self._BREAK:
            self._parts.append("\n")

    def handle_endtag(self, tag: str) -> None:
        if tag in self._SKIP and self._skip_depth:
            self._skip_depth -= 1
        elif tag in self._BREAK:
            self._parts.append("\n")

    def handle_data(self, data: str) -> None:
        if not self._skip_depth:
            self._parts.append(data)

    def text(self) -> str:
        return "".join(self._parts)


def normalize_body(text: str) -> str:
    """Collapse whitespace deterministically.

    Runs once, before offsets are measured. Horizontal whitespace collapses to
    a single space and runs of blank lines to one blank line; the result is
    stable, so re-ingesting identical content produces identical offsets.
    """
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = re.sub(r"[ \t\f\v]+", " ", text)
    text = re.sub(r" *\n *", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def content_type_for(filename: str, declared: str | None = None) -> str:
    """Resolve the content type, preferring an approved declared value."""
    if declared:
        normalized = declared.split(";")[0].strip().lower()
        if normalized in SUPPORTED_CONTENT_TYPES:
            return normalized
        raise ExtractionError(
            f"{filename}: content type {declared!r} is not an approved knowledge source."
        )

    suffix = Path(filename).suffix.lower()
    resolved = EXTENSION_CONTENT_TYPES.get(suffix)
    if resolved is None:
        raise ExtractionError(
            f"{filename}: unsupported file type. Approved types are "
            f"PDF, Markdown, text, HTML, and CSV."
        )
    return resolved


def _decode(payload: bytes, filename: str) -> str:
    try:
        return payload.decode("utf-8-sig")
    except UnicodeDecodeError as error:
        raise ExtractionError(
            f"{filename}: file is not valid UTF-8 text ({error.reason})."
        ) from error


def _slug(filename: str) -> str:
    stem = Path(filename).stem.upper()
    return re.sub(r"[^A-Z0-9]+", "-", stem).strip("-")[:28] or "DOCUMENT"


def _split_sections(text: str, *, markdown: bool) -> list[str]:
    """Split into sections, preferring Markdown headings when present.

    Heading-delimited sections are never merged with each other. A heading is
    an explicit authoring decision about where one answer ends, and merging two
    short sections would also merge their per-section `**Category:**` labels -
    silently giving the second section the first one's category.

    Paragraphs in an unstructured document carry no such signal, so they are
    packed up to the section limit as the MVP does.
    """
    heading_delimited = markdown and bool(
        re.search(r"^#{1,3}\s+", text, flags=re.MULTILINE)
    )
    if heading_delimited:
        parts = re.split(r"(?=^#{1,3}\s+)", text, flags=re.MULTILINE)
    else:
        parts = re.split(r"\n\s*\n", text)

    if heading_delimited:
        return [block.strip() for block in parts if block.strip()]

    sections: list[str] = []
    pending = ""
    for part in (block.strip() for block in parts):
        if not part:
            continue
        if len(part) > MAX_SECTION_CHARS:
            if pending:
                sections.append(pending)
                pending = ""
            sections.append(part)
            continue
        if not pending:
            pending = part
        elif len(pending) + len(part) + 2 <= MAX_SECTION_CHARS:
            pending = f"{pending}\n\n{part}"
        else:
            sections.append(pending)
            pending = part
    if pending:
        sections.append(pending)
    return sections


def _title_for(section: str, filename: str, ordinal: int) -> str:
    heading = HEADING_PATTERN.search(section)
    if heading:
        return heading.group(1).strip(" #")[:200]
    first_line = section.strip().splitlines()[0] if section.strip() else ""
    if 0 < len(first_line) <= 120:
        return first_line.strip(" #")[:200]
    return f"{Path(filename).stem} section {ordinal}"[:200]


def _category_for(section: str) -> str | None:
    match = CATEGORY_PATTERN.search(section)
    if match:
        return match.group(1).lower()
    return None


def _extract_pdf(payload: bytes, filename: str) -> str:
    from pypdf import PdfReader

    try:
        reader = PdfReader(io.BytesIO(payload))
        return "\n\n".join((page.extract_text() or "").strip() for page in reader.pages)
    except ExtractionError:
        raise
    except Exception as error:  # pypdf raises a wide range of parse errors
        raise ExtractionError(
            f"{filename}: could not be read as a PDF ({type(error).__name__})."
        ) from error


def _extract_csv(text: str, filename: str) -> list[ExtractedArticle]:
    """One article per row. `title` and `body` are required."""
    reader = csv.DictReader(io.StringIO(text))
    fieldnames = {(name or "").strip().lower() for name in (reader.fieldnames or [])}
    missing = REQUIRED_CSV_COLUMNS - fieldnames
    if missing:
        raise ExtractionError(
            f"{filename}: CSV is missing required column(s): {', '.join(sorted(missing))}."
        )

    slug = _slug(filename)
    articles: list[ExtractedArticle] = []
    for index, row in enumerate(reader, start=1):
        normalized = {(key or "").strip().lower(): (value or "") for key, value in row.items()}
        title = normalized.get("title", "").strip()
        body = normalize_body(normalized.get("body", ""))
        if not title or not body:
            raise ExtractionError(f"{filename}: row {index} has an empty title or body.")

        category = normalized.get("category", "").strip().lower() or None
        if category and category not in VALID_CATEGORIES:
            raise ExtractionError(
                f"{filename}: row {index} has category {category!r}; "
                f"approved categories are {', '.join(sorted(VALID_CATEGORIES))}."
            )

        reference = normalized.get("article_id", "").strip() or f"{slug}-{index:03d}"
        raw_terms = normalized.get("keywords", "") or normalized.get("search_terms", "")
        terms = tuple(
            dict.fromkeys(
                term.strip().lower()
                for term in re.split(r"[;,|]", raw_terms)
                if term.strip()
            )
        )[:MAX_SEARCH_TERMS]

        articles.append(
            ExtractedArticle(
                external_ref=reference,
                title=title[:200],
                body=body,
                category=category,
                ordinal=index,
                search_terms=terms,
            )
        )

    if not articles:
        raise ExtractionError(f"{filename}: CSV contains no rows.")
    return articles


def extract(
    payload: bytes,
    filename: str,
    *,
    declared_content_type: str | None = None,
    default_category: str | None = None,
) -> ExtractionResult:
    """Validate and extract one uploaded file. Raises `ExtractionError` on refusal."""

    if not payload:
        raise ExtractionError(f"{filename}: file is empty.")
    if len(payload) > MAX_FILE_BYTES:
        raise ExtractionError(
            f"{filename}: file is larger than the "
            f"{MAX_FILE_BYTES // (1024 * 1024)} MB limit."
        )
    if default_category and default_category not in VALID_CATEGORIES:
        raise ExtractionError(
            f"{default_category!r} is not an approved category."
        )

    content_type = content_type_for(filename, declared_content_type)
    warnings: list[str] = []

    if content_type == "text/csv":
        articles = _extract_csv(_decode(payload, filename), filename)
        return ExtractionResult(
            filename=filename, content_type=content_type, articles=tuple(articles)
        )

    if content_type == "application/pdf":
        raw = _extract_pdf(payload, filename)
    elif content_type == "text/html":
        parser = _TextExtractor()
        parser.feed(_decode(payload, filename))
        parser.close()
        raw = parser.text()
    else:
        raw = _decode(payload, filename)

    body = normalize_body(raw)
    if not body:
        raise ExtractionError(f"{filename}: no readable text was found.")

    sections = _split_sections(body, markdown=content_type == "text/markdown")
    if not sections:
        raise ExtractionError(f"{filename}: no readable sections were found.")

    slug = _slug(filename)
    articles = []
    for index, section in enumerate(sections, start=1):
        category = _category_for(section) or default_category
        articles.append(
            ExtractedArticle(
                external_ref=f"DOC-{slug}-{index:03d}",
                title=_title_for(section, filename, index),
                body=section,
                category=category,
                ordinal=index,
            )
        )

    uncategorised = sum(1 for article in articles if not article.category)
    if uncategorised:
        warnings.append(
            f"{uncategorised} of {len(articles)} sections have no category. "
            "Category is a retrieval preference, not a filter, so they remain searchable."
        )

    return ExtractionResult(
        filename=filename,
        content_type=content_type,
        articles=tuple(articles),
        warnings=tuple(warnings),
    )
