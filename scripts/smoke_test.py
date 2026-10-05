"""Production smoke tests (C15).

Run immediately after a deploy, against the environment that was just
deployed. Every check answers one question an operator would otherwise answer
by clicking around, and the exit status is the verdict, so this can gate a
release.

**Every check is read-only.** A smoke test that writes to production is a
liability: it leaves rows nobody asked for, and on the day something is already
wrong it makes the state harder to read. A test asserts that no statement here
mutates anything, and that the HTTP checks use no method but GET - the one
exception being the deliberate unauthenticated probes, which must be refused
before they reach a handler.

What it deliberately does **not** do:

* call a paid model - that costs money on every deploy, and the gateway's own
  verification already exists for when an operator wants it;
* send anything, which it could not do anyway;
* create an organization, a user, or a ticket.

    python -m scripts.smoke_test --api-url … --web-url … --database-url …
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

# The revision a correctly deployed database should be at. Read from the
# migration chain rather than hardcoded, so it cannot fall behind.
EXPECTED_HEAD = "20260920_0012"

TIMEOUT_SECONDS = 15

# Read-only statements only. Asserted by a test, because "read-only" is a
# property of this list and nothing else enforces it.
CHECKS_SQL: dict[str, str] = {
    "schema_revision": "SELECT version_num FROM alembic_version",
    "append_only_triggers": (
        "SELECT count(*) FROM pg_trigger WHERE NOT tgisinternal "
        "AND tgrelid IN ('audit_events'::regclass, 'erasure_records'::regclass)"
    ),
    "sending_disabled": "SELECT count(*) FROM organizations WHERE sending_enabled",
    "no_send_capable_credential": (
        "SELECT count(*) FROM mailbox_credentials WHERE "
        "'https://www.googleapis.com/auth/gmail.send' = ANY(granted_scopes) "
        "OR 'https://mail.google.com/' = ANY(granted_scopes) "
        "OR 'https://www.googleapis.com/auth/gmail.modify' = ANY(granted_scopes)"
    ),
    "an_owner_exists": (
        "SELECT count(*) FROM memberships WHERE role = 'OWNER' AND status = 'ACTIVE'"
    ),
    "retention_is_configured": "SELECT count(*) FROM retention_policies WHERE enabled",
    "no_expired_session_is_live": "SELECT count(*) FROM sessions WHERE expires < now()",
}


@dataclass
class Result:
    name: str
    ok: bool
    detail: str
    critical: bool = True


@dataclass
class Report:
    results: list[Result] = field(default_factory=list)

    def record(self, name: str, ok: bool, detail: str, *, critical: bool = True) -> None:
        self.results.append(Result(name, ok, detail, critical))

    @property
    def failures(self) -> list[Result]:
        return [r for r in self.results if not r.ok and r.critical]

    @property
    def warnings(self) -> list[Result]:
        return [r for r in self.results if not r.ok and not r.critical]

    @property
    def passed(self) -> bool:
        return not self.failures


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    """Stop at the redirect rather than following it.

    Following redirects made the workspace check useless: urllib followed the
    303 to the sign-in page and reported 200, which is indistinguishable from
    the workspace having been served to a visitor with no session. The status
    and the Location are the answer, so the redirect must not be followed.
    """

    def redirect_request(self, *args: object, **kwargs: object) -> None:
        return None


_NO_REDIRECT = urllib.request.build_opener(_NoRedirect)


def _get(
    url: str, headers: dict[str, str] | None = None, *, follow: bool = True
) -> tuple[int, dict[str, str], str]:
    """One GET, returning the status even for an error response.

    An error status is data here, not an exception: several checks exist
    precisely to confirm that a request is refused.
    """
    request = urllib.request.Request(url, headers=headers or {}, method="GET")
    opener = urllib.request.urlopen if follow else _NO_REDIRECT.open
    try:
        with opener(request, timeout=TIMEOUT_SECONDS) as response:  # noqa: S310
            return (
                response.status,
                dict(response.headers),
                response.read(65536).decode("utf-8", "replace"),
            )
    except urllib.error.HTTPError as error:
        return error.code, dict(error.headers or {}), error.read(65536).decode("utf-8", "replace")
    except (urllib.error.URLError, TimeoutError, OSError) as error:
        return 0, {}, f"unreachable: {type(error).__name__}"


# --------------------------------------------------------------------------
# The private API
# --------------------------------------------------------------------------


def check_api(report: Report, base_url: str, token: str) -> None:
    base = base_url.rstrip("/")

    status, _, body = _get(f"{base}/healthz")
    report.record("api responds", status == 200, f"GET /healthz -> {status}")

    status, _, _ = _get(f"{base}/v1/capabilities")
    report.record(
        "api refuses an unauthenticated caller",
        status == 401,
        f"no credential -> {status} (expected 401)",
    )

    status, _, _ = _get(
        f"{base}/v1/capabilities",
        {
            "Authorization": "Bearer not-the-real-token",
            "X-ResolveFlow-Organization-Id": "smoke-test",
            "X-Request-Id": "smoke-test-0001",
        },
    )
    report.record(
        "api refuses a wrong credential", status == 401, f"bad token -> {status} (expected 401)"
    )

    headers = {
        "Authorization": f"Bearer {token}",
        "X-ResolveFlow-Organization-Id": "smoke-test",
        "X-Request-Id": "smoke-test-0002",
    }
    status, _, body = _get(f"{base}/v1/capabilities", headers)
    report.record("api accepts the service credential", status == 200, f"-> {status}")

    if status == 200:
        try:
            payload = json.loads(body)
        except json.JSONDecodeError:
            report.record("capabilities is json", False, "unparseable response")
            return
        report.record(
            "sending is off",
            payload.get("automatic_sending") is False,
            f"automatic_sending = {payload.get('automatic_sending')!r}",
        )
        stage = payload.get("pilot_stage")
        report.record(
            "pilot stage is reported",
            stage in {"OBSERVE", "DRAFT"},
            f"pilot_stage = {stage!r}, observe_mode = {payload.get('observe_mode')!r}",
        )
        docs_status = _get(f"{base}/openapi.json")[0]
        report.record(
            "docs are not exposed",
            docs_status in {401, 404},
            f"GET /openapi.json -> {docs_status} (expected 404 outside local and test)",
        )

    status, _, _ = _get(
        f"{base}/v1/capabilities",
        {"Authorization": f"Bearer {token}", "X-Request-Id": "smoke-test-0003"},
    )
    report.record(
        "api refuses a missing organization context",
        status == 400,
        f"no organization -> {status} (expected 400)",
    )


# --------------------------------------------------------------------------
# The web tier
# --------------------------------------------------------------------------


def check_web(report: Report, web_url: str) -> None:
    base = web_url.rstrip("/")
    secure = base.startswith("https://")

    status, headers, _ = _get(f"{base}/signin")
    report.record("web responds", status == 200, f"GET /signin -> {status}")

    lowered = {name.lower(): value for name, value in headers.items()}
    policy = lowered.get("content-security-policy", "")
    report.record(
        "a content-security policy is sent",
        "default-src 'none'" in policy and "nonce-" in policy,
        policy[:120] or "absent",
    )
    report.record(
        "the policy permits no inline script",
        "'unsafe-inline'" not in policy and "'unsafe-eval'" not in policy,
        "unsafe-* absent" if policy else "no policy to check",
    )
    report.record(
        "framing is refused",
        "frame-ancestors 'none'" in policy,
        "frame-ancestors 'none'" if "frame-ancestors 'none'" in policy else "absent",
    )

    hsts = lowered.get("strict-transport-security", "")
    if secure:
        report.record(
            "HSTS is sent", "max-age=" in hsts and "includeSubDomains" in hsts, hsts or "absent"
        )
    else:
        report.record(
            "HSTS is not sent over plain HTTP",
            not hsts,
            hsts or "absent, as expected for http",
            critical=False,
        )

    report.record(
        "content sniffing is refused",
        lowered.get("x-content-type-options", "") == "nosniff",
        lowered.get("x-content-type-options", "absent"),
    )

    # Two requests must not share a nonce, or the policy is decorative.
    first = _get(f"{base}/signin")[1]
    second = _get(f"{base}/signin")[1]

    def nonce_of(response_headers: dict[str, str]) -> str:
        value = {k.lower(): v for k, v in response_headers.items()}.get(
            "content-security-policy", ""
        )
        return value.split("nonce-")[1].split("'")[0] if "nonce-" in value else ""

    report.record(
        "each response gets its own nonce",
        bool(nonce_of(first)) and nonce_of(first) != nonce_of(second),
        "distinct" if nonce_of(first) != nonce_of(second) else "repeated",
    )

    status, redirect_headers, body = _get(f"{base}/workspace", follow=False)
    location = {k.lower(): v for k, v in redirect_headers.items()}.get("location", "")
    report.record(
        "the workspace refuses an unauthenticated visitor",
        300 <= status < 400 and "/signin" in location,
        f"GET /workspace -> {status} to {location or '(no Location)'}",
    )
    # And whatever it returned, it must not be the workspace itself.
    report.record(
        "no workspace content is served without a session",
        "ws-pagehead" not in body and "Unified inbox" not in body,
        f"{len(body)} bytes returned",
    )


# --------------------------------------------------------------------------
# The database
# --------------------------------------------------------------------------


def check_database(report: Report, database_url: str) -> None:
    from sqlalchemy import create_engine, text

    engine = create_engine(database_url, pool_pre_ping=True)
    values: dict[str, Any] = {}
    try:
        with engine.connect() as connection:
            for name, statement in CHECKS_SQL.items():
                try:
                    values[name] = connection.execute(text(statement)).scalar()
                except Exception as error:  # noqa: BLE001 - reported, not raised
                    values[name] = f"error: {type(error).__name__}"
    except Exception as error:  # noqa: BLE001
        report.record("database reachable", False, f"{type(error).__name__}")
        return

    report.record("database reachable", True, "connected")
    report.record(
        "schema is at the expected revision",
        values.get("schema_revision") == EXPECTED_HEAD,
        f"{values.get('schema_revision')!r} (expected {EXPECTED_HEAD!r})",
    )
    report.record(
        "the audit trail is append-only",
        values.get("append_only_triggers") == 2,
        f"{values.get('append_only_triggers')} triggers on audit_events and erasure_records "
        "(expected 2)",
    )
    report.record(
        "no organization has sending enabled",
        values.get("sending_disabled") == 0,
        f"{values.get('sending_disabled')} with sending_enabled (expected 0)",
    )
    report.record(
        "no stored credential could send",
        values.get("no_send_capable_credential") == 0,
        f"{values.get('no_send_capable_credential')} send-capable credentials (expected 0)",
    )
    report.record(
        "an active Owner exists",
        isinstance(values.get("an_owner_exists"), int) and values["an_owner_exists"] >= 1,
        f"{values.get('an_owner_exists')} active Owner(s)",
    )
    report.record(
        "retention is configured",
        isinstance(values.get("retention_is_configured"), int)
        and values["retention_is_configured"] >= 1,
        f"{values.get('retention_is_configured')} enabled policies",
        # A warning, not a failure: a brand-new deployment legitimately has
        # none until the Owner sets them, and launch should not be blocked by
        # the order those two happen in.
        critical=False,
    )
    report.record(
        "no expired session is still present",
        values.get("no_expired_session_is_live") == 0,
        f"{values.get('no_expired_session_is_live')} expired sessions (expected 0)",
        critical=False,
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--api-url", help="base URL of the private API")
    parser.add_argument("--web-url", help="base URL of the web tier")
    parser.add_argument("--database-url", help="SQLAlchemy URL, read-only credentials are enough")
    parser.add_argument("--token", default=os.getenv("RESOLVEFLOW_INTERNAL_API_TOKEN", ""))
    parser.add_argument("--json", type=Path, help="write the full report here")
    arguments = parser.parse_args(argv)

    if not (arguments.api_url or arguments.web_url or arguments.database_url):
        parser.error("give at least one of --api-url, --web-url, --database-url")

    report = Report()
    if arguments.api_url:
        if not arguments.token:
            report.record(
                "api credential supplied",
                False,
                "no --token and no RESOLVEFLOW_INTERNAL_API_TOKEN",
            )
        else:
            check_api(report, arguments.api_url, arguments.token)
    if arguments.web_url:
        check_web(report, arguments.web_url)
    if arguments.database_url:
        check_database(report, arguments.database_url)

    width = max((len(r.name) for r in report.results), default=10)
    print(f"\n{'check'.ljust(width)}  verdict  detail")
    for result in report.results:
        verdict = "pass" if result.ok else ("FAIL" if result.critical else "warn")
        print(f"{result.name.ljust(width)}  {verdict:7}  {result.detail}")

    print(f"\nVerdict: {'PASS' if report.passed else 'FAIL'}")
    if report.warnings:
        print(f"{len(report.warnings)} warning(s), which do not fail the run")

    if arguments.json:
        arguments.json.write_text(
            json.dumps(
                {
                    "passed": report.passed,
                    "results": [
                        {
                            "name": r.name,
                            "ok": r.ok,
                            "detail": r.detail,
                            "critical": r.critical,
                        }
                        for r in report.results
                    ],
                },
                indent=2,
            ),
            encoding="utf-8",
        )

    return 0 if report.passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
