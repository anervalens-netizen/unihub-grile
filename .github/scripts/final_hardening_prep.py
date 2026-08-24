from __future__ import annotations

import json
from pathlib import Path


def replace_exact(path: str, old: str, new: str) -> None:
    file_path = Path(path)
    text = file_path.read_text()
    if old not in text:
        raise SystemExit(f"expected block missing in {path}: {old[:120]!r}")
    file_path.write_text(text.replace(old, new, 1))


def update_server_runbook() -> None:
    path = "docs/operations/server-test-runbook.md"
    replace_exact(
        path,
        "Program/evidence tracker: issue #4.  \nPre-server remediation/certification ledger: issue #69.",
        "Authoritative server-test gate and installable SHA: issue #4.  \nPre-server remediation/evidence history: issue #69.",
    )
    replace_exact(
        path,
        "The installable candidate SHA is recorded in issue #69 after remediation,\ncertification and merge attestation. Issue #4 preserves the program tracker and\ncurrent gate status. This file uses the placeholder `<CANDIDATE_SHA>` rather\nthan attempting to name its own commit.",
        "The installable candidate SHA is recorded in issue #4 only after the final\nserver-test gate, certification and merge attestation. Issue #4 is the single\nauthoritative gate **and** candidate-identity source required by `AGENTS.md`;\nissue #69 preserves remediation/evidence history only. This file uses the\nplaceholder `<CANDIDATE_SHA>` rather than attempting to name its own commit.",
    )
    replace_exact(
        path,
        "- `git rev-parse HEAD` exactly equals the candidate SHA recorded in issue #69;",
        "- `git rev-parse HEAD` exactly equals the installable candidate SHA recorded in issue #4;",
    )
    replace_exact(
        path,
        "- the static frontend/reverse proxy must route `/api` to the Grile API;\n- **do not expose `pnpm dev` or `pnpm preview` as the server-test frontend**;",
        "- the static frontend/reverse proxy must route `/api/...` to the Grile API **after stripping the `/api` prefix** (`/api/session` -> backend `/session`, `/api/months/...` -> backend `/months/...`);\n- verify the rewrite from the externally served origin before UI acceptance. For Nginx, a minimal location is `location /api/ { proxy_pass http://127.0.0.1:8080/; }`; the trailing slash on `proxy_pass` is intentional and removes the matched `/api/` prefix. Equivalent proxies must preserve the same rewrite semantics;\n- after proxy setup, compare an authenticated request through `<SERVER_TEST_ORIGIN>/api/session` with the direct restricted-host API request to `http://127.0.0.1:8080/session`; both must reach the same backend route (subject to the same identity headers), and backend access logs must not show `/api/session`;\n- **do not expose `pnpm dev` or `pnpm preview` as the server-test frontend**;",
    )
    replace_exact(
        path,
        "candidate_sha: <exact issue-69 certified SHA>",
        "candidate_sha: <exact issue-4 installable SHA>",
    )
    replace_exact(
        path,
        "- Node 20+ and pnpm for building the standalone frontend;",
        "- Node >=20.19 (or >=22.12) and pnpm for building the standalone frontend;",
    )


def update_backup_runbook() -> None:
    path = "docs/operations/backup-restore-migrations.md"
    replace_exact(
        path,
        "Use PostgreSQL client tools compatible with the server major version (currently\nPostgreSQL 17 in CI):",
        "Use PostgreSQL client tools compatible with the server major version (currently\nPostgreSQL 17 in CI). Repository Alembic commands must be run from\n`<REPOSITORY_CHECKOUT>/backend` with `.venv/bin/alembic`; a global Alembic install\nis not part of the server contract. Required host tools:",
    )
    replace_exact(
        path,
        "```bash\nDATABASE_URL=\"$RESTORED_SQLALCHEMY_URL\" alembic current\nDATABASE_URL=\"$RESTORED_SQLALCHEMY_URL\" alembic check\n```",
        "```bash\ncd <REPOSITORY_CHECKOUT>/backend\nDATABASE_URL=\"$RESTORED_SQLALCHEMY_URL\" .venv/bin/alembic current\nDATABASE_URL=\"$RESTORED_SQLALCHEMY_URL\" .venv/bin/alembic check\ncd -\n```",
    )
    replace_exact(
        path,
        "3. inspect `alembic current`, `alembic heads` and migration files before changing\n   the server-test DB;",
        "3. from `<REPOSITORY_CHECKOUT>/backend`, inspect `.venv/bin/alembic current`,\n   `.venv/bin/alembic heads` and migration files before changing the server-test DB;",
    )
    replace_exact(
        path,
        "   ```bash\n   alembic upgrade head\n   ```",
        "   ```bash\n   cd <REPOSITORY_CHECKOUT>/backend\n   .venv/bin/alembic upgrade head\n   ```",
    )
    replace_exact(
        path,
        "   ```bash\n   alembic current\n   alembic check\n   ```",
        "   ```bash\n   .venv/bin/alembic current\n   .venv/bin/alembic check\n   cd -\n   ```",
    )
    replace_exact(
        path,
        "If `alembic upgrade head` fails before application traffic resumes:",
        "If `.venv/bin/alembic upgrade head` fails before application traffic resumes:",
    )
    replace_exact(
        path,
        "4. compare `alembic current` with the recorded pre-migration revision;",
        "4. from `<REPOSITORY_CHECKOUT>/backend`, compare `.venv/bin/alembic current` with the recorded pre-migration revision;",
    )
    replace_exact(
        path,
        "4. run `alembic current` and reconcile it with the application commit to be\n   started;",
        "4. from `<REPOSITORY_CHECKOUT>/backend`, run `.venv/bin/alembic current` and reconcile it with the application commit to be started;",
    )
    replace_exact(
        path,
        "Do **not** run `alembic downgrade` as a generic emergency command.",
        "Do **not** run `.venv/bin/alembic downgrade` as a generic emergency command.",
    )


def update_architecture() -> None:
    replace_exact(
        "ARCHITECTURE.md",
        "## 12. Current vs target\n\nArhitectura documentează **ținta programului**, nu pretinde că toate elementele\nsunt deja implementate. Statusul exact este numai în issue #4.\n\nMecanisme temporare cunoscute care trebuie eliminate/hardened înainte de gate:\n- development identity headers/fallback;\n- autorizare neuniformă pe unele endpointuri;\n- fixture ingest care nu trebuie montat în prod;\n- close policy încă insuficient de strict pentru toate inputurile financiare;\n- worker fără recovery/retry complet;\n- integrare Google/XLSX încă neproductionizată integral;\n- backend CI/observability incomplet;\n- Retail adapters încă neimplementate.\n",
        "## 12. Current vs target\n\nArhitectura de mai sus este implementată pentru candidatul standalone server-test;\nstatusul gate-ului și SHA-ul instalabil rămân autoritative exclusiv în issue #4.\n\nMecanismele care au fost harden-uite în program și nu mai sunt TODO-uri:\n- `dev_headers` este strict environment-gated, iar configurația production\n  fail-closed nu acceptă development identity;\n- capability + tenant + effective resource scope sunt backend-enforced, cu teste\n  negative cross-tenant/cross-manager;\n- fixture ingest nu este montat în production;\n- `ClosePolicy` verifică inputurile financiare obligatorii și serializează\n  mutațiile relevante pe Month;\n- workerul are lease committed, bounded retry/backoff, stale-RUNNING recovery,\n  idempotency și supersession/revision binding;\n- Google fake/live și XLSX au contracte fail-closed, readback/protection,\n  revision/generation pinning și publicare deterministă/atomică;\n- CI acoperă backend strict, PostgreSQL/migrații, frontend și browser E2E, iar\n  runtime-ul expune health/readiness/metrics/logging structurat.\n\nLimite deliberate care rămân în afara gate-ului standalone:\n- identitatea production reală va fi furnizată de adaptorul Retail/host;\n- adaptorul Retail real și montarea în shell-ul Retail sunt fază separată;\n- live Google canary, deployment/production activation și măsurătorile pe hostul\n  real necesită autorizare/executare separată.\n",
    )


def update_backend_config() -> None:
    replace_exact(
        "backend/pyproject.toml",
        'asyncio_mode = "auto"\n',
        'asyncio_mode = "auto"\nasyncio_default_fixture_loop_scope = "function"\n',
    )


def update_export_api_and_tests() -> None:
    path = "backend/src/ugrile/api/s5b.py"
    replace_exact(
        path,
        '    hint = summary.get("artifact_uri_hint")\n    artifact_uri_hint = str(hint) if isinstance(hint, str) and hint else run.artifact_uri\n    status = "ENQUEUED" if run.status == "PENDING" else run.status\n',
        '    status = "ENQUEUED" if run.status == "PENDING" else run.status\n',
    )
    replace_exact(
        path,
        '        "status": status,\n        "artifact_uri_hint": artifact_uri_hint,\n        "replayed": replayed,\n',
        '        "status": status,\n        "artifact_ready": run.status == "DONE" and bool(run.artifact_uri),\n        "replayed": replayed,\n',
    )
    replace_exact(
        path,
        '        "status": run.status,\n        "artifact_uri": run.artifact_uri,\n        "summary": run.summary,\n',
        '        "status": run.status,\n        "artifact_ready": run.status == "DONE" and bool(run.artifact_uri),\n        "summary": run.summary,\n',
    )

    test_path = Path("backend/tests/api/test_s5b_export.py")
    text = test_path.read_text()
    old_import = "from ugrile.repositories.months import MonthRepository\n"
    if old_import not in text:
        raise SystemExit("MonthRepository import marker missing")
    text = text.replace(
        old_import,
        "from ugrile.repositories.models import ExportRun\nfrom ugrile.repositories.months import MonthRepository\n",
        1,
    )
    marker = "def _cleanup_artifact(artifact_uri: str | None) -> None:\n    if artifact_uri and os.path.exists(artifact_uri):\n        os.unlink(artifact_uri)\n\n\n"
    if marker not in text:
        raise SystemExit("export test helper marker missing")
    text = text.replace(
        marker,
        marker
        + "def _internal_artifact_uri(job_id: int) -> str | None:\n"
        + "    with database.session_scope() as session:\n"
        + "        run = session.get(ExportRun, job_id)\n"
        + "        assert run is not None\n"
        + "        return run.artifact_uri\n\n\n",
        1,
    )
    replacements = [
        (
            '        assert data["artifact_uri_hint"].endswith(".xlsx")\n',
            '        assert "artifact_uri_hint" not in data\n        assert data["artifact_ready"] is False\n',
        ),
        (
            '        assert pending["artifact_uri"] is None\n',
            '        assert "artifact_uri" not in pending\n        assert pending["artifact_ready"] is False\n',
        ),
        (
            '        artifact_uri = body["artifact_uri"]\n        assert artifact_uri == data["artifact_uri_hint"]\n        assert os.path.exists(artifact_uri)\n',
            '        assert "artifact_uri" not in body\n        assert body["artifact_ready"] is True\n        artifact_uri = _internal_artifact_uri(job_id)\n        assert artifact_uri is not None\n        assert os.path.exists(artifact_uri)\n',
        ),
        (
            '        artifact_uri = status["artifact_uri"]\n        try:\n',
            '        assert "artifact_uri" not in status\n        assert status["artifact_ready"] is True\n        artifact_uri = _internal_artifact_uri(data["job_id"])\n        assert artifact_uri is not None\n        try:\n',
        ),
        (
            '        artifact_uri = body["artifact_uri"]\n        assert artifact_uri.endswith(".zip")\n        try:\n',
            '        assert "artifact_uri" not in body\n        assert body["artifact_ready"] is True\n        artifact_uri = _internal_artifact_uri(job_id)\n        assert artifact_uri is not None\n        assert artifact_uri.endswith(".zip")\n        try:\n',
        ),
        (
            '        artifact_uri = body["artifact_uri"]\n        try:\n            assert os.path.exists(artifact_uri)\n',
            '        assert "artifact_uri" not in body\n        assert body["artifact_ready"] is True\n        artifact_uri = _internal_artifact_uri(job_id)\n        assert artifact_uri is not None\n        try:\n            assert os.path.exists(artifact_uri)\n',
        ),
        (
            '        assert body["artifact_uri"] is None\n',
            '        assert "artifact_uri" not in body\n        assert body["artifact_ready"] is False\n        assert _internal_artifact_uri(bogus_run_id) is None\n',
        ),
    ]
    for old, new in replacements:
        if old not in text:
            raise SystemExit(f"export API test block missing: {old[:100]!r}")
        text = text.replace(old, new, 1)
    forbidden = (
        'data["artifact_uri_hint"]',
        'body["artifact_uri"]',
        'status["artifact_uri"]',
        'pending["artifact_uri"]',
    )
    if any(token in text for token in forbidden):
        raise SystemExit("public artifact path assertion remains in export API tests")
    test_path.write_text(text)


def update_frontend_toolchain() -> None:
    package_path = Path("frontend/package.json")
    package = json.loads(package_path.read_text())
    dev = package["devDependencies"]
    dev["vite"] = "8.2.2"
    dev["@vitejs/plugin-react"] = "6.0.5"
    dev["vitest"] = "4.1.7"
    package["engines"]["node"] = ">=20.19.0"
    package_path.write_text(json.dumps(package, ensure_ascii=False, indent=2) + "\n")


def update_makefile() -> None:
    replace_exact(
        "Makefile",
        "\tcd $(BACKEND) && $(VENV)/bin/pip install --quiet --upgrade pip\n\tcd $(BACKEND) && $(VENV)/bin/pip install --quiet -e \".[dev]\"\n",
        "\tcd $(BACKEND) && $(VENV)/bin/pip install --quiet --upgrade 'pip==26.2.1'\n\tcd $(BACKEND) && $(VENV)/bin/pip install --quiet -c requirements.lock -e \".[dev]\"\n\tcd $(BACKEND) && $(VENV)/bin/pip check\n",
    )


def main() -> None:
    update_server_runbook()
    update_backup_runbook()
    update_architecture()
    update_backend_config()
    update_export_api_and_tests()
    update_frontend_toolchain()
    update_makefile()


if __name__ == "__main__":
    main()
