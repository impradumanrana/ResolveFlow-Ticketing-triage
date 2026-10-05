"""Split an article body into chunks that carry exact character offsets.

The offsets are the whole point. A citation that says "article KB-007" is not
evidence; a citation that says "characters 412-688 of KB-007, which read as
follows" is. The C05 gate requires citations to map to exact passages, and that
is only true if this invariant holds for every chunk:

    body[chunk.start_offset:chunk.end_offset] == chunk.content

Nothing here rewrites text. Chunks are slices of the stored body, never
reformatted copies of it, because a copy that differs by one collapsed space
makes the offsets lie.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

# Sized for the embedding model and for a passage a reviewer can read at a
# glance. Overlap keeps a sentence that straddles a boundary retrievable from
# either side.
DEFAULT_MAX_CHARS = 900
DEFAULT_OVERLAP_CHARS = 120
MIN_CHUNK_CHARS = 1

_PARAGRAPH_BREAK = re.compile(r"\n{2,}")
_SENTENCE_END = re.compile(r"(?<=[.!?])\s+")


class ChunkingError(ValueError):
    """A body that cannot be chunked without breaking the offset invariant."""


@dataclass(frozen=True)
class Chunk:
    index: int
    content: str
    start_offset: int
    end_offset: int

    @property
    def length(self) -> int:
        return self.end_offset - self.start_offset


def _spans(body: str) -> list[tuple[int, int]]:
    """Paragraph spans, falling back to sentences for long paragraphs."""
    spans: list[tuple[int, int]] = []
    cursor = 0

    for match in _PARAGRAPH_BREAK.finditer(body):
        if match.start() > cursor:
            spans.append((cursor, match.start()))
        cursor = match.end()
    if cursor < len(body):
        spans.append((cursor, len(body)))

    refined: list[tuple[int, int]] = []
    for start, end in spans:
        if end - start <= DEFAULT_MAX_CHARS:
            refined.append((start, end))
            continue

        # Split on sentence boundaries, measured against the original string so
        # the offsets stay true.
        piece_start = start
        for sentence in _SENTENCE_END.finditer(body, start, end):
            if sentence.end() - piece_start >= DEFAULT_MAX_CHARS:
                refined.append((piece_start, sentence.start()))
                piece_start = sentence.end()
        if piece_start < end:
            refined.append((piece_start, end))

    return [(start, end) for start, end in refined if body[start:end].strip()]


def _hard_split(body: str, start: int, end: int, max_chars: int) -> list[tuple[int, int]]:
    """Last resort for text with no paragraph or sentence structure."""
    pieces: list[tuple[int, int]] = []
    cursor = start
    while cursor < end:
        stop = min(cursor + max_chars, end)
        if stop < end:
            # Prefer a word boundary, but never at the cost of an empty piece.
            window = body.rfind(" ", cursor + max_chars // 2, stop)
            if window > cursor:
                stop = window
        pieces.append((cursor, stop))
        cursor = stop if stop > cursor else end
    return pieces


def chunk_body(
    body: str,
    *,
    max_chars: int = DEFAULT_MAX_CHARS,
    overlap_chars: int = DEFAULT_OVERLAP_CHARS,
) -> list[Chunk]:
    """Chunk `body`, preserving exact offsets into it."""

    if max_chars < 50:
        raise ChunkingError("max_chars below 50 produces passages too small to cite.")
    if not 0 <= overlap_chars < max_chars:
        raise ChunkingError("overlap_chars must be non-negative and smaller than max_chars.")
    if not body or not body.strip():
        return []

    spans = _spans(body)
    packed: list[tuple[int, int]] = []
    current: tuple[int, int] | None = None

    for start, end in spans:
        if end - start > max_chars:
            if current:
                packed.append(current)
                current = None
            packed.extend(_hard_split(body, start, end, max_chars))
            continue

        if current is None:
            current = (start, end)
        elif end - current[0] <= max_chars:
            # Extend through the intervening break so the slice stays contiguous.
            current = (current[0], end)
        else:
            packed.append(current)
            current = (start, end)

    if current:
        packed.append(current)

    chunks: list[Chunk] = []
    for index, (start, end) in enumerate(packed):
        # Overlap reaches backwards only, so a chunk never extends past the
        # text it was built from.
        if index and overlap_chars:
            start = max(0, min(start, packed[index - 1][1]) - overlap_chars)
            start = max(start, 0)

        content = body[start:end]
        if len(content) < MIN_CHUNK_CHARS or not content.strip():
            continue

        chunks.append(
            Chunk(index=len(chunks), content=content, start_offset=start, end_offset=end)
        )

    verify_offsets(body, chunks)
    return chunks


def verify_offsets(body: str, chunks: list[Chunk]) -> None:
    """Fail loudly if any chunk does not slice back to its stored content.

    Called on every chunking run rather than only in tests: a citation that
    points at the wrong passage is worse than a failed ingestion, because it
    looks like evidence.
    """
    for chunk in chunks:
        if not 0 <= chunk.start_offset < chunk.end_offset <= len(body):
            raise ChunkingError(
                f"Chunk {chunk.index} has offsets outside the body "
                f"({chunk.start_offset}, {chunk.end_offset}) for length {len(body)}."
            )
        if body[chunk.start_offset : chunk.end_offset] != chunk.content:
            raise ChunkingError(
                f"Chunk {chunk.index} does not match the body at its offsets. "
                "Citations from this article would point at the wrong passage."
            )


def coverage(body: str, chunks: list[Chunk]) -> float:
    """Fraction of non-whitespace characters reachable through some chunk."""
    if not body.strip():
        return 1.0

    covered = bytearray(len(body))
    for chunk in chunks:
        for position in range(chunk.start_offset, chunk.end_offset):
            covered[position] = 1

    total = sum(1 for index, character in enumerate(body) if not character.isspace())
    if not total:
        return 1.0
    reached = sum(
        1
        for index, character in enumerate(body)
        if not character.isspace() and covered[index]
    )
    return reached / total
