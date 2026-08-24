from __future__ import annotations

from pathlib import Path


def replace_once(path: str, old: str, new: str) -> None:
    file_path = Path(path)
    text = file_path.read_text()
    if old in text:
        file_path.write_text(text.replace(old, new, 1))
        return
    if new in text:
        return
    raise SystemExit(f"expected old/new block missing in {path}: {old[:100]!r}")


# M2 export idempotency tests: the public API no longer exposes filesystem paths.
path = "backend/tests/api/test_m2_export_idempotency.py"
replace_once(
    path,
    "def _cleanup(*paths: str | None) -> None:\n    for path in paths:\n        if path and os.path.exists(path):\n            os.unlink(path)\n\n\n",
    "def _cleanup(*paths: str | None) -> None:\n    for path in paths:\n        if path and os.path.exists(path):\n            os.unlink(path)\n\n\ndef _export_artifact_uri(job_id: int) -> str | None:\n    with database.session_scope() as session:\n        run = session.get(ExportRun, job_id)\n        assert run is not None\n        return run.artifact_uri\n\n\n",
)
replace_once(
    path,
    '        assert second_body["job_id"] == first_body["job_id"]\n        assert second_body["artifact_uri_hint"] == first_body["artifact_uri_hint"]\n',
    '        assert second_body["job_id"] == first_body["job_id"]\n        assert "artifact_uri_hint" not in first_body\n        assert "artifact_uri_hint" not in second_body\n        assert first_body["artifact_ready"] is False\n        assert second_body["artifact_ready"] is False\n',
)
replace_once(
    path,
    '        assert replay_body["job_id"] == first_body["job_id"]\n        assert replay_body["status"] == "DONE"\n        _cleanup(replay_body["artifact_uri_hint"])\n',
    '        assert replay_body["job_id"] == first_body["job_id"]\n        assert replay_body["status"] == "DONE"\n        assert replay_body["artifact_ready"] is True\n        assert "artifact_uri_hint" not in replay_body\n        artifact_uri = _export_artifact_uri(replay_body["job_id"])\n        assert artifact_uri is not None\n        _cleanup(artifact_uri)\n',
)
replace_once(
    path,
    '            ).json()\n            first_path = first["artifact_uri_hint"]\n            row, result = run_once(locked_by=WORKER_LOCKED_BY)\n            assert row is not None and row.status == "DONE"\n            assert result is not None\n            with open(first_path, "rb") as fh:\n',
    '            ).json()\n            assert "artifact_uri_hint" not in first\n            assert first["artifact_ready"] is False\n            row, result = run_once(locked_by=WORKER_LOCKED_BY)\n            assert row is not None and row.status == "DONE"\n            assert result is not None\n            first_path = _export_artifact_uri(first["job_id"])\n            assert first_path is not None\n            with open(first_path, "rb") as fh:\n',
)
replace_once(
    path,
    '            ).json()\n            second_path = second["artifact_uri_hint"]\n            assert second_path != first_path\n            row, result = run_once(locked_by=WORKER_LOCKED_BY)\n            assert row is not None and row.status == "DONE"\n            assert result is not None\n\n            with open(first_path, "rb") as fh:\n',
    '            ).json()\n            assert "artifact_uri_hint" not in second\n            assert second["artifact_ready"] is False\n            row, result = run_once(locked_by=WORKER_LOCKED_BY)\n            assert row is not None and row.status == "DONE"\n            assert result is not None\n            second_path = _export_artifact_uri(second["job_id"])\n            assert second_path is not None\n            assert second_path != first_path\n\n            with open(first_path, "rb") as fh:\n',
)

# M2 revision-bound job test: prove stale job did not write via persisted ExportRun,
# and obtain the successful artifact path from internal state only.
path = "backend/tests/api/test_m2_revision_bound_jobs.py"
replace_once(
    path,
    '        assert stale_row.last_error is not None\n        assert "JOB_MONTH_REVISION_STALE" in stale_row.last_error\n        assert not os.path.exists(old["artifact_uri_hint"])\n',
    '        assert stale_row.last_error is not None\n        assert "JOB_MONTH_REVISION_STALE" in stale_row.last_error\n        assert "artifact_uri_hint" not in old\n        assert old["artifact_ready"] is False\n',
)
replace_once(
    path,
    '        current_row, current_result = run_once(locked_by=WORKER_LOCKED_BY)\n        assert current_row is not None and current_row.status == "DONE"\n        assert current_result is not None and current_result.status == "DONE"\n        current_artifact = new["artifact_uri_hint"]\n        assert os.path.exists(current_artifact)\n\n        with database.session_scope() as session:\n            current_run = session.get(ExportRun, new["job_id"])\n            assert current_run is not None and current_run.status == "DONE"\n            summary = json.loads(current_run.summary)\n',
    '        current_row, current_result = run_once(locked_by=WORKER_LOCKED_BY)\n        assert current_row is not None and current_row.status == "DONE"\n        assert current_result is not None and current_result.status == "DONE"\n        assert "artifact_uri_hint" not in new\n\n        with database.session_scope() as session:\n            current_run = session.get(ExportRun, new["job_id"])\n            assert current_run is not None and current_run.status == "DONE"\n            current_artifact = current_run.artifact_uri\n            assert current_artifact is not None\n            assert os.path.exists(current_artifact)\n            summary = json.loads(current_run.summary)\n',
)

# M7 operations contract: assert the explicit repository virtualenv command.
path = "backend/tests/test_m7_operations_contract.py"
replace_once(path, '        "alembic current",\n', '        ".venv/bin/alembic current",\n')
replace_once(path, '        "alembic check",\n', '        ".venv/bin/alembic check",\n')
replace_once(path, '        "alembic upgrade head",\n', '        ".venv/bin/alembic upgrade head",\n')
replace_once(
    path,
    '        "Do **not** run `alembic downgrade`",\n',
    '        "Do **not** run `.venv/bin/alembic downgrade`",\n',
)
