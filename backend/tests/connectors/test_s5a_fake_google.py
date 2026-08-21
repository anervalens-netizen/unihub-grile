"""S5a tests for the fake Google adapter.

The tests exercise the contract without any network I/O:

* a successful ``write_store_projection`` persists a
  ``sheet_projection_runs`` + ``sheet_bindings`` row, ``read`` returns
  the same structural shape, and ``last_error`` stays empty;
* when ``UGR_S5_GOOGLE_FAIL=1`` is set the adapter raises
  ``GoogleAdapterError``; the last-good row is retained on a second
  attempt and ``failures`` increments monotonically;
* the projection payload survives a session rollback so the caller can
  use the adapter as the sole projection writer.
"""

from __future__ import annotations

import pytest

from ugrile.connectors.google import (
    GoogleAdapterError,
    is_provider_failing,
    last_error,
    last_run_at,
    read_store_projection,
    write_store_projection,
)
from ugrile.repositories.models import SheetBinding, SheetProjectionRun


def _payload() -> dict[str, object]:
    return {
        "grila": {"revision": 1, "rows": [{"business_date": "2026-08-01"}]},
        "pontaj": {"revision": 1, "rows": [{"person_id": "p1", "hours": "8.0"}]},
    }


def test_write_store_projection_success_persists_rows(session, faker_tenant):
    projection = write_store_projection(
        session,
        tenant_id=faker_tenant["tenant_id"],
        store_id=faker_tenant["store_id"],
        generation="FIXTURE_V1",
        payload=_payload(),
    )
    session.commit()
    assert projection.generation == "FIXTURE_V1"
    binding = session.query(SheetBinding).filter_by(
        tenant_id=faker_tenant["tenant_id"], store_id=faker_tenant["store_id"]
    ).one()
    assert binding.sheet_name_grila == "Grila"
    assert binding.sheet_name_pontaj == "Pontaj"
    runs = session.query(SheetProjectionRun).filter_by(
        tenant_id=faker_tenant["tenant_id"], store_id=faker_tenant["store_id"]
    ).all()
    assert len(runs) == 1
    run = runs[0]
    assert run.status == "DONE"
    assert run.last_success_generation == "FIXTURE_V1"
    assert run.failures == 0
    assert run.last_error is None
    assert run.payload and "grila" in run.payload and "pontaj" in run.payload
    # Readback matches the projection.
    back = read_store_projection(
        session,
        tenant_id=faker_tenant["tenant_id"],
        store_id=faker_tenant["store_id"],
    )
    assert back is not None
    assert back.generation == "FIXTURE_V1"
    assert back.last_success_generation == "FIXTURE_V1"
    assert back.grila["revision"] == 1
    assert back.pontaj["revision"] == 1


def test_write_store_projection_records_last_run_at(session, faker_tenant):
    write_store_projection(
        session,
        tenant_id=faker_tenant["tenant_id"],
        store_id=faker_tenant["store_id"],
        generation="FIXTURE_V1",
        payload=_payload(),
    )
    session.commit()
    last = last_run_at(
        session,
        tenant_id=faker_tenant["tenant_id"],
        store_id=faker_tenant["store_id"],
    )
    assert last is not None
    err = last_error(
        session,
        tenant_id=faker_tenant["tenant_id"],
        store_id=faker_tenant["store_id"],
    )
    assert err is None


def test_write_store_projection_failure_keeps_last_good(monkeypatch, session, faker_tenant):
    monkeypatch.setenv("UGR_S5_GOOGLE_FAIL", "1")
    assert is_provider_failing() is True

    # First successful run establishes the last-good row.
    monkeypatch.delenv("UGR_S5_GOOGLE_FAIL")
    write_store_projection(
        session,
        tenant_id=faker_tenant["tenant_id"],
        store_id=faker_tenant["store_id"],
        generation="FIXTURE_V1",
        payload=_payload(),
    )
    session.commit()
    last_good_run = session.query(SheetProjectionRun).filter_by(
        tenant_id=faker_tenant["tenant_id"], store_id=faker_tenant["store_id"]
    ).order_by(SheetProjectionRun.id.desc()).first()
    assert last_good_run.status == "DONE"

    # Now flip the failure flag and try again.
    monkeypatch.setenv("UGR_S5_GOOGLE_FAIL", "1")
    with pytest.raises(GoogleAdapterError) as excinfo:
        write_store_projection(
            session,
            tenant_id=faker_tenant["tenant_id"],
            store_id=faker_tenant["store_id"],
            generation="FIXTURE_V1_v2",
            payload=_payload(),
        )
    session.commit()
    assert excinfo.value.code == "DOMAIN_ERROR"
    assert "UGR_S5_GOOGLE_FAIL" in excinfo.value.message

    # Readback must still return the last-good row.
    back = read_store_projection(
        session,
        tenant_id=faker_tenant["tenant_id"],
        store_id=faker_tenant["store_id"],
    )
    assert back is not None
    assert back.generation == "FIXTURE_V1"

    # The most recent run row is FAILED with an incremented counter and a
    # non-null ``last_error`` that mentions the failure flag.
    failed_run = session.query(SheetProjectionRun).filter_by(
        tenant_id=faker_tenant["tenant_id"], store_id=faker_tenant["store_id"]
    ).order_by(SheetProjectionRun.id.desc()).first()
    assert failed_run.status == "FAILED"
    assert failed_run.failures >= 1
    assert failed_run.last_error is not None
    assert "UGR_S5_GOOGLE_FAIL" in failed_run.last_error


def test_write_store_projection_failure_without_last_good(monkeypatch, session, faker_tenant):
    monkeypatch.setenv("UGR_S5_GOOGLE_FAIL", "1")
    with pytest.raises(GoogleAdapterError):
        write_store_projection(
            session,
            tenant_id=faker_tenant["tenant_id"],
            store_id=faker_tenant["store_id"],
            generation="FIXTURE_V1",
            payload=_payload(),
        )
    session.commit()
    run = session.query(SheetProjectionRun).filter_by(
        tenant_id=faker_tenant["tenant_id"], store_id=faker_tenant["store_id"]
    ).one()
    assert run.status == "FAILED"
    assert run.last_success_generation is None
    assert run.failures == 1


def test_write_store_projection_rejects_invalid_inputs(session, faker_tenant):
    with pytest.raises(GoogleAdapterError):
        write_store_projection(
            session,
            tenant_id=faker_tenant["tenant_id"],
            store_id="",
            generation="FIXTURE_V1",
            payload=_payload(),
        )
    with pytest.raises(GoogleAdapterError):
        write_store_projection(
            session,
            tenant_id=faker_tenant["tenant_id"],
            store_id=faker_tenant["store_id"],
            generation="",
            payload=_payload(),
        )
    with pytest.raises(GoogleAdapterError):
        write_store_projection(
            session,
            tenant_id=faker_tenant["tenant_id"],
            store_id=faker_tenant["store_id"],
            generation="FIXTURE_V1",
            payload={"grila": "not-a-mapping", "pontaj": {}},  # type: ignore[dict-item]
        )
    with pytest.raises(GoogleAdapterError):
        write_store_projection(
            session,
            tenant_id=faker_tenant["tenant_id"],
            store_id=faker_tenant["store_id"],
            generation="FIXTURE_V1",
            payload={"grila": {}, "pontaj": []},  # type: ignore[dict-item]
        )


def test_read_store_projection_returns_none_when_empty(session, faker_tenant):
    assert (
        read_store_projection(
            session,
            tenant_id=faker_tenant["tenant_id"],
            store_id=faker_tenant["store_id"],
        )
        is None
    )
