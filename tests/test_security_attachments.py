"""The attachment scan gate (C13).

C04's schema promised that nothing reads an attachment until a scan says it is
clean, and left the scanner to this phase. The gate is now a single function,
and these tests hold the four rules that make it a gate rather than a comment -
plus one scan proving the download path genuinely does not exist yet, with a
positive control so the scan cannot pass by looking in the wrong place.
"""

from __future__ import annotations

import ast
import pathlib
from datetime import UTC, datetime

import pytest

from app.security.attachments import (
    ALLOWED_ATTACHMENT_TYPES,
    MAX_ATTACHMENT_BYTES,
    READABLE_STATES,
    AttachmentRefused,
    RefusingScanner,
    ScanState,
    ScanVerdict,
    may_read,
    next_state,
    refusal_for,
    require_readable,
)

ROOT = pathlib.Path(__file__).resolve().parents[1]
NOW = datetime(2026, 10, 5, 9, 0, tzinfo=UTC)


# ===========================================================================
# ONLY CLEAN IS READABLE
# ===========================================================================


def test_exactly_one_state_is_readable() -> None:
    assert READABLE_STATES == {ScanState.CLEAN}


@pytest.mark.parametrize("state", [s for s in ScanState if s is not ScanState.CLEAN])
def test_every_other_state_is_refused(state: ScanState) -> None:
    """PENDING included: "not yet known to be bad" is not "known to be good",
    so a verdict that never arrives fails closed."""
    assert not may_read(state)
    with pytest.raises(AttachmentRefused) as refused:
        require_readable(state)
    assert refused.value.state == state
    assert refused.value.code == refusal_for(state)


def test_a_clean_attachment_is_readable() -> None:
    assert may_read(ScanState.CLEAN)
    require_readable(ScanState.CLEAN)  # must not raise


def test_a_deleted_attachment_is_never_readable_even_when_clean() -> None:
    assert not may_read(ScanState.CLEAN, deleted_at=NOW)
    with pytest.raises(AttachmentRefused) as refused:
        require_readable(ScanState.CLEAN, deleted_at=NOW)
    assert refused.value.code == "ATTACHMENT_DELETED"


def test_an_unknown_state_is_refused_rather_than_defaulted() -> None:
    assert not may_read("SOMETHING_NEW")
    assert refusal_for("SOMETHING_NEW") == "ATTACHMENT_NOT_AVAILABLE"


@pytest.mark.parametrize("state", [s for s in ScanState if s is not ScanState.CLEAN])
def test_each_refusal_names_the_reason(state: ScanState) -> None:
    """An operator has to tell "we have not looked" from "it is a virus"."""
    code = refusal_for(state)
    assert code.startswith("ATTACHMENT_")
    assert code != "ATTACHMENT_NOT_AVAILABLE", f"{state} has no specific reason"


def test_the_refusal_reasons_are_distinct() -> None:
    codes = [refusal_for(s) for s in ScanState if s is not ScanState.CLEAN]
    assert len(set(codes)) == len(codes), codes


# ===========================================================================
# NO SCANNER MEANS FAILED, NEVER CLEAN
# ===========================================================================


def test_an_unconfigured_deployment_cannot_produce_a_clean_verdict() -> None:
    verdict = RefusingScanner().scan(storage_object="bucket/obj", sha256="a" * 64)

    assert verdict.state is ScanState.FAILED
    assert verdict.detail == "NO_SCANNER_CONFIGURED"
    assert not may_read(verdict.state)


# ===========================================================================
# APPLYING A VERDICT
# ===========================================================================


@pytest.mark.parametrize("state", [ScanState.CLEAN, ScanState.INFECTED, ScanState.FAILED])
def test_a_verdict_moves_a_pending_attachment(state: ScanState) -> None:
    verdict = ScanVerdict(state=state, sha256="b" * 64, storage_object="bucket/obj")

    assert next_state(ScanState.PENDING, verdict) is state


def test_an_infected_verdict_cannot_be_overwritten_by_a_later_clean_one() -> None:
    """The rule that matters most: re-scanning a judged attachment would let
    the second answer win."""
    clean = ScanVerdict(state=ScanState.CLEAN, sha256="c" * 64, storage_object="bucket/obj")

    with pytest.raises(AttachmentRefused) as refused:
        next_state(ScanState.INFECTED, clean)
    assert refused.value.code == "ATTACHMENT_ALREADY_JUDGED"


@pytest.mark.parametrize("state", [ScanState.CLEAN, ScanState.INFECTED, ScanState.SKIPPED])
def test_a_terminal_state_refuses_any_verdict(state: ScanState) -> None:
    verdict = ScanVerdict(state=ScanState.CLEAN, sha256="d" * 64, storage_object="o")

    with pytest.raises(AttachmentRefused):
        next_state(state, verdict)


def test_a_failed_scan_may_be_retried() -> None:
    """Transient scanner failure is not a judgement about the bytes."""
    verdict = ScanVerdict(state=ScanState.CLEAN, sha256="e" * 64, storage_object="bucket/obj")

    assert next_state(ScanState.FAILED, verdict) is ScanState.CLEAN


def test_a_clean_verdict_must_name_the_bytes_it_judged() -> None:
    """Mirrors the database's `clean_attachment_has_an_object`."""
    for verdict in (
        ScanVerdict(state=ScanState.CLEAN),
        ScanVerdict(state=ScanState.CLEAN, sha256="f" * 64),
        ScanVerdict(state=ScanState.CLEAN, storage_object="bucket/obj"),
    ):
        with pytest.raises(AttachmentRefused) as refused:
            next_state(ScanState.PENDING, verdict)
        assert refused.value.code == "CLEAN_VERDICT_WITHOUT_BYTES"


@pytest.mark.parametrize("state", [ScanState.PENDING, ScanState.SKIPPED])
def test_a_non_verdict_is_refused(state: ScanState) -> None:
    """ "Still pending" and "we never fetched it" are not scan results."""
    with pytest.raises(AttachmentRefused) as refused:
        next_state(ScanState.PENDING, ScanVerdict(state=state))
    assert refused.value.code == "NOT_A_VERDICT"


# ===========================================================================
# THE INTAKE POLICY IS C07'S, NOT A SECOND COPY
# ===========================================================================


def test_the_gate_reuses_the_ingestion_policy() -> None:
    from app.ingestion import normalize

    assert ALLOWED_ATTACHMENT_TYPES is normalize.ALLOWED_ATTACHMENT_TYPES
    assert MAX_ATTACHMENT_BYTES == normalize.MAX_ATTACHMENT_BYTES


def test_no_executable_type_is_ever_accepted() -> None:
    for dangerous in (
        "application/x-msdownload",
        "application/x-executable",
        "application/vnd.microsoft.portable-executable",
        "application/x-sh",
        "application/javascript",
        "text/javascript",
        "application/zip",
        "application/x-msdos-program",
    ):
        assert dangerous not in ALLOWED_ATTACHMENT_TYPES, dangerous


# ===========================================================================
# THE READ PATH DOES NOT EXIST YET, AND WILL NOT APPEAR UNNOTICED
# ===========================================================================

DOWNLOAD_MARKERS = (
    "attachments().get",
    "attachments/",
    "download_as_bytes",
    "download_to_filename",
    "get_blob",
    "storage_object_bytes",
)

PYTHON_SOURCES = sorted(
    path for path in (ROOT / "app").rglob("*.py") if "__pycache__" not in path.parts
)


def _code_only(text: str) -> str:
    """Strip comments and docstrings, so prose about downloads is not a hit."""
    tree = ast.parse(text)
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            body = getattr(node, "body", [])
            if (
                body
                and isinstance(body[0], ast.Expr)
                and isinstance(body[0].value, ast.Constant)
                and isinstance(body[0].value.value, str)
            ):
                body.pop(0)
    return ast.unparse(tree)


def test_nothing_fetches_attachment_bytes_today() -> None:
    """The gate is currently unreachable because the read path is absent.

    When someone adds a download, this test fails and they have to route it
    through `require_readable` - which is the point of writing the gate first.
    """
    offenders: list[str] = []
    for source in PYTHON_SOURCES:
        code = _code_only(source.read_text(encoding="utf-8"))
        for marker in DOWNLOAD_MARKERS:
            if marker in code:
                offenders.append(f"{source.relative_to(ROOT)}: {marker}")
    assert not offenders, offenders


def test_the_download_scan_can_actually_fail() -> None:
    """Positive control. Without this, the test above passes a typo."""
    sample = '''
"""A module whose docstring mentions attachments/ and download_as_bytes."""

def fetch(client, name):
    # downloads attachments/ from storage
    return client.get_blob(name).download_as_bytes()
'''
    code = _code_only(sample)
    assert any(marker in code for marker in DOWNLOAD_MARKERS)
    assert "docstring mentions" not in code, "docstrings must be stripped"
    assert "downloads attachments/ from storage" not in code, "comments must be stripped"


def test_the_web_tier_offers_no_attachment_download() -> None:
    """Metadata is shown; bytes are not served.

    Scoped to the attachment markup rather than the whole file: the ticket page
    legitimately links to the inbox, and a test that flags that would be
    switched off within a week.
    """
    web = ROOT / "apps" / "web" / "src"

    routes = list(web.rglob("route.ts"))
    assert routes, "no API routes found; the scan is looking in the wrong place"
    for route in routes:
        assert "attachment" not in route.read_text(encoding="utf-8").lower(), route

    # Attribute-shaped, not the bare word: the UI legitimately says
    # "not downloaded (outside the attachment policy)".
    serving = ("href=", "<a ", "download=", "download>", "blob:", "src=")
    checked = 0
    for page in web.rglob("*.tsx"):
        lines = page.read_text(encoding="utf-8").splitlines()
        for number, line in enumerate(lines, 1):
            if "attachment" not in line.lower():
                continue
            checked += 1
            # The markup around a filename, not just the line holding it.
            window = " ".join(lines[max(0, number - 3) : number + 3])
            for marker in serving:
                assert marker not in window, (
                    f"{page}:{number} may serve attachment bytes ({marker})"
                )
    assert checked, "no attachment markup found; the scan is looking in the wrong place"


def test_the_attachment_markup_scan_can_actually_fail() -> None:
    """Positive control for the window-based scan above."""
    lines = [
        '<ul className="ws-attachments">',
        "  <li>",
        "    <a href={`/api/attachments/${attachment.id}`}>{attachment.filename}</a>",
        "  </li>",
        "</ul>",
    ]
    hits = 0
    for number, line in enumerate(lines, 1):
        if "attachment" not in line.lower():
            continue
        window = " ".join(lines[max(0, number - 3) : number + 3])
        if any(
            marker in window
            for marker in ("href=", "<a ", "download=", "download>", "blob:", "src=")
        ):
            hits += 1
    assert hits, "the window scan would miss a real download link"
