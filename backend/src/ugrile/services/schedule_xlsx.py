"""Safe, deterministic XLSX schedule template and preview parser."""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date
from io import BytesIO
from typing import Any

from openpyxl import Workbook, load_workbook  # type: ignore[import-untyped]
from openpyxl.styles import Protection  # type: ignore[import-untyped]
from openpyxl.utils import get_column_letter  # type: ignore[import-untyped]
from openpyxl.worksheet.datavalidation import DataValidation  # type: ignore[import-untyped]

from ..domain.enums import DayStatus, WorkingKind
from .calendar import CalendarChange

SCHEMA = "UGRILE-S2-SCHEDULE-V1"


@dataclass(frozen=True, slots=True)
class ParsedSchedule:
    tenant_id: str
    month_id: str
    base_revision: int
    changes: list[CalendarChange]
    errors: list[dict[str, Any]]
    warnings: list[dict[str, Any]]


def _decode(
    value: object, person_id: str, day: date, stores: dict[str, str]
) -> CalendarChange | None:
    if value is None or str(value).strip() == "":
        return CalendarChange(person_id, day, None, DayStatus.OFF)
    text = str(value).strip()
    if text == "LIBER":
        return CalendarChange(person_id, day, None, DayStatus.OFF)
    if text == "CONCEDIU":
        return CalendarChange(person_id, day, None, DayStatus.LEAVE)
    for prefix, status in (
        ("NORMAL - ", WorkingKind.NORMAL),
        ("SUPLIMENTAR ACASĂ - ", WorkingKind.EXTRA_HOME),
        ("SUPLIMENTAR - ", WorkingKind.EXTRA_OTHER),
    ):
        if text.startswith(prefix):
            code = text[len(prefix) :].strip()
            if code not in stores:
                raise ValueError("UNKNOWN_STORE")
            return CalendarChange(person_id, day, stores[code], DayStatus.WORKING, status)
    raise ValueError("MALFORMED_CELL")


def _encode(change: CalendarChange | None, stores_by_id: dict[str, str]) -> str:
    if change is None or change.status == DayStatus.OFF:
        return "LIBER"
    if change.status == DayStatus.LEAVE:
        return "CONCEDIU"
    if change.store_id is None or change.working_kind is None:
        return "LIBER"
    code = stores_by_id.get(change.store_id)
    if code is None:
        return "LIBER"
    prefix = {
        WorkingKind.NORMAL: "NORMAL",
        WorkingKind.EXTRA_HOME: "SUPLIMENTAR ACASĂ",
        WorkingKind.EXTRA_OTHER: "SUPLIMENTAR",
    }[change.working_kind]
    return f"{prefix} - {code}"


def _day_values(
    person_id: str,
    year: int,
    month: int,
    calendar_by_key: dict[tuple[str, date], CalendarChange],
    stores_by_id: dict[str, str],
) -> list[str]:
    values: list[str] = []
    for day in range(1, 32):
        try:
            business_date = date(year, month, day)
        except ValueError:
            values.append("LIBER")
        else:
            values.append(_encode(calendar_by_key.get((person_id, business_date)), stores_by_id))
    return values


def _allowed_for_person(person: dict[str, str], stores: dict[str, str]) -> list[str]:
    home_store = person.get("home_store_code", "")
    values = ["LIBER", "CONCEDIU"]
    if home_store in stores:
        values.extend([f"NORMAL - {home_store}", f"SUPLIMENTAR ACASĂ - {home_store}"])
    values.extend(f"SUPLIMENTAR - {code}" for code in stores if code != home_store)
    return values


def build_template(
    *,
    tenant_id: str,
    month_id: str,
    year: int,
    month: int,
    base_revision: int,
    people: list[dict[str, str]],
    stores: dict[str, str],
    calendar: list[CalendarChange] | None = None,
) -> bytes:
    calendar_by_key = {
        (change.person_id, change.business_date): change for change in (calendar or [])
    }
    store_codes_by_id = {store_id: code for code, store_id in stores.items()}
    wb = Workbook()
    ws = wb.active
    ws.title = "Instrucțiuni"
    ws.append(["schema", SCHEMA])
    ws.append(["tenant_id", tenant_id])
    ws.append(["month_id", month_id])
    ws.append(["base_revision", base_revision])
    ws.append(
        ["legend", "LIBER | CONCEDIU | NORMAL - cod | SUPLIMENTAR ACASĂ - cod | SUPLIMENTAR - cod"]
    )
    lists = wb.create_sheet("_Lists")
    lists.sheet_state = "hidden"
    lists.protection.sheet = True
    dropdown_ranges: dict[str, str] = {}
    for column, person in enumerate(people, start=1):
        values = _allowed_for_person(person, stores)
        for row, value in enumerate(values, start=1):
            lists.cell(row, column, value)
        letter = get_column_letter(column)
        dropdown_ranges[person["person_id"]] = f"='_Lists'!${letter}$1:${letter}${len(values)}"

    used_sheet_names: set[str] = set(wb.sheetnames)
    for manager, rows in _by_manager(people):
        sheet_name = _safe_sheet_name(manager, used_sheet_names)
        used_sheet_names.add(sheet_name)
        tab = wb.create_sheet(sheet_name)
        headers = ["person_id", "nume", "magazin_bază"] + [str(d) for d in range(1, 32)]
        tab.append(headers)
        for person in rows:
            dv = DataValidation(
                type="list",
                formula1=dropdown_ranges[person["person_id"]],
                allow_blank=False,
            )
            tab.add_data_validation(dv)
            tab.append(
                [
                    person["person_id"],
                    person.get("display_name", ""),
                    person.get("home_store_code", ""),
                    *_day_values(
                        person["person_id"], year, month, calendar_by_key, store_codes_by_id
                    ),
                ]
            )
            row_number = tab.max_row
            dv.add(f"D{row_number}:AH{row_number}")
            for column in range(4, 35):
                tab.cell(row_number, column).protection = Protection(locked=False)
        tab.column_dimensions["A"].hidden = True
        tab.freeze_panes = "D2"
        tab.protection.sheet = True
    manifest = wb.create_sheet("Manifest")
    manifest.sheet_state = "hidden"
    manifest.protection.sheet = True
    manifest.append(["schema", SCHEMA])
    manifest.append(["tenant_id", tenant_id])
    manifest.append(["month_id", month_id])
    manifest.append(["base_revision", base_revision])
    manifest.append(["store_codes", "|".join(sorted(stores))])
    out = BytesIO()
    wb.save(out)
    return out.getvalue()


def _safe_sheet_name(manager: str, used: set[str]) -> str:
    base = re.sub(r"[\[\]:*?/\\]", "_", manager).strip()[:31] or "Manager"
    candidate = base
    suffix = 2
    while candidate in used:
        tail = f"_{suffix}"
        candidate = f"{base[: 31 - len(tail)]}{tail}"
        suffix += 1
    return candidate


def _by_manager(people: list[dict[str, str]]) -> list[tuple[str, list[dict[str, str]]]]:
    grouped: dict[str, list[dict[str, str]]] = {}
    for p in people:
        grouped.setdefault(p.get("manager_code", "manager"), []).append(p)
    return sorted(grouped.items())


def parse_schedule(
    data: bytes,
    *,
    expected_tenant_id: str,
    expected_month_id: str,
    year: int,
    month: int,
    stores: dict[str, str],
    known_person_ids: set[str] | None = None,
) -> ParsedSchedule:
    errors: list[dict[str, Any]] = []
    warnings: list[dict[str, Any]] = []
    changes: list[CalendarChange] = []
    seen_person_days: set[tuple[str, date]] = set()
    try:
        wb = load_workbook(BytesIO(data), data_only=True, keep_links=False)
    except Exception as exc:
        return ParsedSchedule(
            expected_tenant_id,
            expected_month_id,
            -1,
            [],
            [{"code": "INVALID_XLSX", "message": str(exc)}],
            [],
        )
    if "Instrucțiuni" not in wb.sheetnames or "Manifest" not in wb.sheetnames:
        return ParsedSchedule(
            expected_tenant_id, expected_month_id, -1, [], [{"code": "INVALID_STRUCTURE"}], []
        )
    manifest = wb["Manifest"]
    values = {
        str(manifest.cell(r, 1).value): manifest.cell(r, 2).value
        for r in range(1, manifest.max_row + 1)
    }
    tenant_id = str(values.get("tenant_id", ""))
    month_id = str(values.get("month_id", ""))
    try:
        revision = int(values.get("base_revision", -1))
    except (TypeError, ValueError):
        revision = -1
    if tenant_id != expected_tenant_id:
        errors.append({"code": "TENANT_MISMATCH"})
    if month_id != expected_month_id:
        errors.append({"code": "MONTH_MISMATCH"})
    try:
        for sheet in wb.worksheets:
            if sheet.title in {"Instrucțiuni", "Manifest", "_Lists"}:
                continue
            for row in range(2, sheet.max_row + 1):
                person_id = sheet.cell(row, 1).value
                if not person_id:
                    errors.append({"code": "MISSING_PERSON_ID", "sheet": sheet.title, "row": row})
                    continue
                if str(person_id).strip() != str(person_id):
                    errors.append({"code": "MALFORMED_PERSON_ID", "sheet": sheet.title, "row": row})
                if known_person_ids is not None and str(person_id) not in known_person_ids:
                    errors.append({"code": "UNKNOWN_PERSON", "sheet": sheet.title, "row": row})
                    continue
                for day_number in range(1, 32):
                    try:
                        day = date(year, month, day_number)
                    except ValueError:
                        continue
                    try:
                        change = _decode(
                            sheet.cell(row, day_number + 3).value, str(person_id), day, stores
                        )
                    except ValueError as exc:
                        errors.append(
                            {"code": str(exc), "sheet": sheet.title, "row": row, "day": day_number}
                        )
                        continue
                    if change:
                        key = (change.person_id, change.business_date)
                        if key in seen_person_days:
                            errors.append(
                                {
                                    "code": "DUPLICATE_PERSON_DAY",
                                    "sheet": sheet.title,
                                    "row": row,
                                    "day": day_number,
                                }
                            )
                        else:
                            seen_person_days.add(key)
                            changes.append(change)
    except Exception as exc:
        errors.append({"code": "PARSE_ERROR", "message": str(exc)})
    return ParsedSchedule(tenant_id, month_id, revision, changes, errors, warnings)
