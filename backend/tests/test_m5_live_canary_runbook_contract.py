"""GS-010 safety contract for the bounded live-canary runbook."""

from __future__ import annotations

from pathlib import Path

RUNBOOK = Path(__file__).resolve().parents[2] / "docs" / "operations" / "google-live-canary.md"


def _runbook() -> str:
    return RUNBOOK.read_text(encoding="utf-8")


def test_live_canary_runbook_requires_separate_non_production_authorization() -> None:
    text = _runbook()

    assert "does not itself authorize any Google request" in text
    assert "separate human authorization" in text
    assert "dedicated non-production spreadsheet" in text
    assert "no production tenant, store, spreadsheet" in text
    assert "Executing the live canary is intentionally not a GS-010 completion requirement" in text


def test_live_canary_runbook_is_bounded_and_fail_stop() -> None:
    text = _runbook()

    assert "at most **two** live projection executions" in text
    assert "at most **one** Google E-pay readback/persistence call" in text
    assert "no bulk/all-store projection" in text
    assert "immediate stop on the first unexpected mutation" in text
    assert "A FAIL must not be converted to PASS by rerunning" in text


def test_live_canary_runbook_requires_reconciliation_protection_and_shutdown() -> None:
    text = _runbook()

    assert "verification_mode=live_readback" in text
    assert "only H:I for the exact current expected person rows are operator-editable" in text
    assert "UGRILE_GOOGLE_LIVE_MUTATIONS_ENABLED=false" in text
    assert "spreadsheet_fingerprint_sha256" in text
    assert "must not copy credentials" in text
    assert "`unihub-retail` remains read-only" in text
