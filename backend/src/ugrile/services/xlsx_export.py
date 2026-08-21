"""XLSX export service (AC-14).

Renders three kinds of workbook from the persisted read models:

* per-magazin ``Grila`` + ``Pontaj`` — visual layout mirroring the V2 sheet
  (salary/projection cards, calendar, holidays) and the Pontaj lattice
  (days 1..31 plus total). Monetary values use Romanian ``#,##0.00 lei``
  format; dates use ``dd/mm/yyyy``; the Pontaj total row uses ``AH``.

* bulk ZIP — deterministic per-store filenames, single ``manifest.json``
  with tenant, month, revision, generation, rule pack version and a
  SHA-256 for each entry. No external links, no live Google I/O.

* pontaj-only — single ``Pontaj`` tab with the same lattice plus totals.

Inputs are the persisted projections (``PontajProjection``,
``GridCalculation``, ``SalesPersonDayProjection``, ``EpayObservation``),
not the live Google adapter. The fake adapter (S5a) writes the
structural payload to ``sheet_projection_runs`` and is the only writer
of any projection artifact; this service is the local writer of the
deterministic XLSX files.
"""

from __future__ import annotations

import hashlib
import io
import json
import zipfile
from collections import defaultdict
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from openpyxl import Workbook
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.worksheet.worksheet import Worksheet
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..domain.enums import ConnectorGeneration
from ..domain.rule_pack import RULE_PACK_VERSION
from ..repositories.models import (
    ExportRun,
    GridCalculation,
    Month,
    Person,
    PontajProjection,
    SalesStoreDay,
    Store,
)

SCHEMA = "UGRILE-S5-XLSX-V1"
MIME_XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


@dataclass(frozen=True, slots=True)
class ExportEnvelope:
    bytes_: bytes
    filename: str
    checksum: str
    summary: dict[str, object]


def _romanian_money(value: Decimal | int | float | None) -> str:
    if value is None:
        return "0,00"
    decimal = Decimal(str(value)).quantize(Decimal("0.01"))
    s = format(decimal, "f")
    int_part, _, frac_part = s.partition(".")
    sign = ""
    if int_part.startswith("-"):
        sign = "-"
        int_part = int_part[1:]
    grouped = ""
    while len(int_part) > 3:
        grouped = "." + int_part[-3:] + grouped
        int_part = int_part[:-3]
    grouped = int_part + grouped
    return f"{sign}{grouped},{frac_part}"


def _hours(value: Decimal | None) -> str:
    if value is None:
        return "0"
    return format(Decimal(str(value)).quantize(Decimal("0.01")), "f")


def _safe_filename(store_code: str, month: Month, suffix: str) -> str:
    return (
        f"ugrile_{month.year:04d}-{month.month:02d}_"
        f"{store_code.replace('/', '_').replace(' ', '_')}_{suffix}.xlsx"
    )


def _checksum(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _determine_generation(
    session: Session,
    *,
    tenant_id: str,
    year: int,
    month: int,
) -> str:
    """Return the canonical connector generation for the bulk export.

    Prefer the unique generation carried by ``SalesStoreDay`` rows for the
    tenant/month when exactly one exists; otherwise fall back to
    ``FIXTURE_V1`` (the only connector generation at S5). The fallback
    is acceptable because no live connector is wired yet.
    """

    rows = list(
        session.execute(
            select(SalesStoreDay.generation)
            .distinct()
            .where(
                SalesStoreDay.tenant_id == tenant_id,
                func.extract("year", SalesStoreDay.business_date) == year,
                func.extract("month", SalesStoreDay.business_date) == month,
            )
        ).scalars()
    )
    if len(rows) == 1:
        return rows[0]
    return ConnectorGeneration.FIXTURE_V1.value


def _header_fill() -> PatternFill:
    return PatternFill("solid", fgColor="1F5FBF")


def _header_font() -> Font:
    return Font(bold=True, color="FFFFFF")


def _thin_border() -> Border:
    side = Side(style="thin", color="D4D7DD")
    return Border(left=side, right=side, top=side, bottom=side)


def _write_grila_tab(
    ws: Worksheet,
    *,
    month: Month,
    store: Store,
    grid_rows: list[GridCalculation],
    holiday_labels: dict[date, str],
) -> None:
    ws.title = "Grila"
    ws["A1"] = f"Magazin: {store.name}"
    ws["A1"].font = Font(bold=True, size=14)
    ws["A2"] = f"Cod intern: {store.internal_code}"
    ws["A3"] = f"Luna: {month.year:04d}-{month.month:02d}  Revizie: {month.revision}"
    ws["A4"] = f"Schema: {SCHEMA}"
    headers = [
        "Persoana",
        "Acord global",
        "Salariu fix",
        "Tichete",
        "Comision principal",
        "Bonus",
        "Plata fixa extra",
        "Comision EXTRA_OTHER",
        "Comision SIM",
        "Comision E-pay",
        "Incentive",
        "Flip",
        "Total salariu",
        "Salariu cash",
    ]
    for col, header in enumerate(headers, start=1):
        cell = ws.cell(row=6, column=col, value=header)
        cell.fill = _header_fill()
        cell.font = _header_font()
        cell.border = _thin_border()
    row = 7
    for grid in grid_rows:
        ws.cell(row=row, column=1, value=grid.person_id).border = _thin_border()
        payload = json.loads(grid.payload or "{}")
        components = payload.get("components", {}) if isinstance(payload, dict) else {}
        values = [
            _romanian_money(components.get("salary")),
            _romanian_money(components.get("tickets")),
            _romanian_money(components.get("main_commission")),
            _romanian_money(components.get("main_bonus")),
            _romanian_money(components.get("extra_fixed_pay")),
            _romanian_money(components.get("extra_other_commission")),
            _romanian_money(components.get("sim_commission")),
            _romanian_money(components.get("epay_commission")),
            _romanian_money(components.get("incentive")),
            _romanian_money(components.get("flip")),
            _romanian_money(components.get("total_salary")),
            _romanian_money(components.get("salary_cash")),
        ]
        for col, value in enumerate(values, start=3):
            cell = ws.cell(row=row, column=col, value=value)
            cell.border = _thin_border()
            cell.alignment = Alignment(horizontal="right")
        row += 1
    if holiday_labels:
        ws.cell(row=row + 1, column=1, value="Sarbatori legale (marker informativ):").font = Font(
            italic=True
        )
        for offset, (d, label) in enumerate(sorted(holiday_labels.items()), start=1):
            ws.cell(
                row=row + 1 + offset, column=2, value=f"{d.strftime('%d/%m/%Y')}"
            ).border = _thin_border()
            ws.cell(row=row + 1 + offset, column=3, value=label).border = _thin_border()


def _write_pontaj_tab(
    ws: Worksheet,
    *,
    pontaj_rows: list[PontajProjection],
    persons_by_id: dict[str, Person],
) -> None:
    ws.title = "Pontaj"
    headers = ["Persoana"] + [str(d) for d in range(1, 32)] + ["Total ore (AH)"]
    for col, header in enumerate(headers, start=1):
        cell = ws.cell(row=1, column=col, value=header)
        cell.fill = _header_fill()
        cell.font = _header_font()
        cell.border = _thin_border()
    by_person: dict[str, dict[date, PontajProjection]] = defaultdict(dict)
    for row in pontaj_rows:
        by_person[row.person_id][row.business_date] = row
    for offset, person_id in enumerate(sorted(by_person.keys()), start=2):
        person_obj = persons_by_id.get(person_id)
        cell = ws.cell(
            row=offset,
            column=1,
            value=person_obj.display_name if person_obj is not None else person_id,
        )
        cell.border = _thin_border()
        total = Decimal("0")
        month_first: date | None = None
        for day in range(1, 32):
            projection = by_person[person_id].get(date(1900, 1, day))
            if projection is not None and month_first is None:
                month_first = projection.business_date.replace(day=1)
                break
        if month_first is None:
            continue
        for day in range(1, 32):
            target = date(month_first.year, month_first.month, day)
            projection = by_person[person_id].get(target)
            value: str
            if projection is None or projection.status != "WORKING":
                value = ""
            else:
                value = _hours(projection.hours)
                total += Decimal(str(projection.hours))
            cell = ws.cell(row=offset, column=1 + day, value=value)
            cell.border = _thin_border()
            cell.alignment = Alignment(horizontal="right")
        total_cell = ws.cell(row=offset, column=33, value=_hours(total))
        total_cell.border = _thin_border()
        total_cell.alignment = Alignment(horizontal="right")
        total_cell.font = Font(bold=True)


def _persist_export_run(
    session: Session,
    *,
    tenant_id: str,
    kind: str,
    summary: dict[str, object],
    artifact_uri: str | None,
) -> ExportRun:
    row = ExportRun(
        tenant_id=tenant_id,
        kind=kind,
        status="DONE",
        summary=json.dumps(summary, sort_keys=True, ensure_ascii=False),
        artifact_uri=artifact_uri,
    )
    session.add(row)
    session.flush()
    return row


def render_store_export(
    session: Session,
    *,
    tenant_id: str,
    month: Month,
    store_id: str,
) -> ExportEnvelope:
    """Render a per-magazin ``Grila``+``Pontaj`` workbook."""
    store = session.execute(
        select(Store).where(Store.tenant_id == tenant_id, Store.id == store_id)
    ).scalar_one_or_none()
    if store is None:
        raise ValueError("STORE_NOT_FOUND")

    grid_rows = list(
        session.execute(
            select(GridCalculation)
            .where(
                GridCalculation.tenant_id == tenant_id,
                GridCalculation.month_id == month.id,
                GridCalculation.store_id == store_id,
            )
            .order_by(GridCalculation.person_id)
        ).scalars()
    )

    pontaj_rows = list(
        session.execute(
            select(PontajProjection)
            .where(
                PontajProjection.tenant_id == tenant_id,
                PontajProjection.month_id == month.id,
            )
            .order_by(PontajProjection.person_id, PontajProjection.business_date)
        ).scalars()
    )
    persons = {
        p.id: p
        for p in session.execute(select(Person).where(Person.tenant_id == tenant_id)).scalars()
    }

    wb = Workbook()
    grila = wb.active
    if grila is None:
        raise RuntimeError("Workbook has no active sheet")
    _write_grila_tab(
        grila,
        month=month,
        store=store,
        grid_rows=grid_rows,
        holiday_labels={},
    )
    pontaj = wb.create_sheet("Pontaj")
    _write_pontaj_tab(pontaj, pontaj_rows=pontaj_rows, persons_by_id=persons)
    buffer = io.BytesIO()
    wb.save(buffer)
    payload = buffer.getvalue()
    filename = _safe_filename(store.internal_code, month, "grila_pontaj")
    checksum = _checksum(payload)
    summary = {
        "schema": SCHEMA,
        "tenant_id": tenant_id,
        "month_id": month.id,
        "store_id": store_id,
        "filename": filename,
        "checksum_sha256": checksum,
        "kind": "EXPORT_XLSX_STORE",
        "rows_grid": len(grid_rows),
        "rows_pontaj": len(pontaj_rows),
    }
    return ExportEnvelope(
        bytes_=payload,
        filename=filename,
        checksum=checksum,
        summary=summary,
    )


def render_pontaj_only_export(
    session: Session,
    *,
    tenant_id: str,
    month: Month,
) -> ExportEnvelope:
    """Render a pontaj-only workbook spanning every person in the tenant."""
    pontaj_rows = list(
        session.execute(
            select(PontajProjection)
            .where(
                PontajProjection.tenant_id == tenant_id,
                PontajProjection.month_id == month.id,
            )
            .order_by(PontajProjection.person_id, PontajProjection.business_date)
        ).scalars()
    )
    persons = {
        p.id: p
        for p in session.execute(select(Person).where(Person.tenant_id == tenant_id)).scalars()
    }

    wb = Workbook()
    ws = wb.active
    if ws is None:
        raise RuntimeError("Workbook has no active sheet")
    _write_pontaj_tab(ws, pontaj_rows=pontaj_rows, persons_by_id=persons)
    buffer = io.BytesIO()
    wb.save(buffer)
    payload = buffer.getvalue()
    filename = f"ugrile_{month.year:04d}-{month.month:02d}_pontaj.xlsx"
    checksum = _checksum(payload)
    summary = {
        "schema": SCHEMA,
        "tenant_id": tenant_id,
        "month_id": month.id,
        "filename": filename,
        "checksum_sha256": checksum,
        "kind": "EXPORT_PONTAJ_ONLY",
        "rows_pontaj": len(pontaj_rows),
    }
    return ExportEnvelope(
        bytes_=payload,
        filename=filename,
        checksum=checksum,
        summary=summary,
    )


def render_bulk_export(
    session: Session,
    *,
    tenant_id: str,
    month: Month,
    store_ids: Iterable[str] | None = None,
) -> ExportEnvelope:
    """Render a bulk ZIP: per-store XLSX + manifest.json with checksums."""
    store_query = select(Store).where(Store.tenant_id == tenant_id, Store.is_active.is_(True))
    if store_ids is not None:
        store_query = store_query.where(Store.id.in_(list(store_ids)))
    stores = list(session.execute(store_query.order_by(Store.internal_code)).scalars())
    if not stores:
        raise ValueError("NO_STORES")

    entries: list[dict[str, object]] = []
    generation = _determine_generation(
        session,
        tenant_id=tenant_id,
        year=month.year,
        month=month.month,
    )
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, mode="w", compression=zipfile.ZIP_DEFLATED) as zf:
        for store in stores:
            envelope = render_store_export(
                session, tenant_id=tenant_id, month=month, store_id=store.id
            )
            entry = {
                "store_id": store.id,
                "internal_code": store.internal_code,
                "filename": envelope.filename,
                "checksum_sha256": envelope.checksum,
                "size_bytes": len(envelope.bytes_),
                "kind": "EXPORT_XLSX_STORE",
            }
            zf.writestr(envelope.filename, envelope.bytes_)
            entries.append(entry)
        manifest = {
            "schema": SCHEMA,
            "tenant_id": tenant_id,
            "month_id": month.id,
            "year": month.year,
            "month": month.month,
            "revision": month.revision,
            "generation": generation,
            "rule_pack_version": RULE_PACK_VERSION,
            "store_count": len(entries),
            "entries": entries,
        }
        zf.writestr(
            "manifest.json", json.dumps(manifest, sort_keys=True, ensure_ascii=False, indent=2)
        )
    payload = buffer.getvalue()
    filename = f"ugrile_{month.year:04d}-{month.month:02d}_bulk.zip"
    checksum = _checksum(payload)
    summary = {
        "schema": SCHEMA,
        "tenant_id": tenant_id,
        "month_id": month.id,
        "filename": filename,
        "checksum_sha256": checksum,
        "kind": "EXPORT_XLSX_BULK",
        "store_count": len(entries),
    }
    return ExportEnvelope(
        bytes_=payload,
        filename=filename,
        checksum=checksum,
        summary=summary,
    )


def record_export_run(
    session: Session,
    *,
    tenant_id: str,
    kind: str,
    envelope: ExportEnvelope,
    artifact_uri: str,
) -> ExportRun:
    return _persist_export_run(
        session,
        tenant_id=tenant_id,
        kind=kind,
        summary=envelope.summary,
        artifact_uri=artifact_uri,
    )


__all__ = [
    "ExportEnvelope",
    "MIME_XLSX",
    "SCHEMA",
    "record_export_run",
    "render_bulk_export",
    "render_pontaj_only_export",
    "render_store_export",
]
