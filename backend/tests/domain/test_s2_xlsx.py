from datetime import date
from io import BytesIO

from openpyxl import load_workbook

from ugrile.domain.enums import DayStatus, WorkingKind
from ugrile.services.calendar import CalendarChange
from ugrile.services.schedule_xlsx import SCHEMA, build_template, parse_schedule


def test_template_parsed_round_trip_and_manifest():
    data = build_template(
        tenant_id="tenant_acme",
        month_id="month_acme_2026_08",
        year=2026,
        month=8,
        base_revision=4,
        people=[
            {
                "person_id": "person_acme_a",
                "display_name": "Alice",
                "home_store_code": "s1",
                "manager_code": "m1",
            }
        ],
        stores={"s1": "store_acme_s1"},
        calendar=[
            CalendarChange(
                "person_acme_a",
                date(2026, 8, 1),
                "store_acme_s1",
                DayStatus.WORKING,
                WorkingKind.NORMAL,
            )
        ],
    )
    wb = load_workbook(BytesIO(data), data_only=True)
    assert {"Instrucțiuni", "Manifest", "_Lists", "m1"}.issubset(wb.sheetnames)
    assert wb["Manifest"]["B1"].value == SCHEMA
    assert wb["m1"].column_dimensions["A"].hidden is True
    assert wb["m1"].protection.sheet is True
    assert wb["m1"]["D2"].protection.locked is False
    assert wb["_Lists"].sheet_state == "hidden"
    assert wb["_Lists"].protection.sheet is True
    assert wb["m1"]["D2"].value == "NORMAL - s1"
    parsed = parse_schedule(
        data,
        expected_tenant_id="tenant_acme",
        expected_month_id="month_acme_2026_08",
        year=2026,
        month=8,
        stores={"s1": "store_acme_s1"},
    )
    assert parsed.errors == []
    assert parsed.base_revision == 4
    assert len(parsed.changes) == 31
    assert parsed.changes[0].status == DayStatus.WORKING


def test_xlsx_unknown_and_malformed_cells_are_reported():
    data = build_template(
        tenant_id="tenant_acme",
        month_id="month_acme_2026_08",
        year=2026,
        month=8,
        base_revision=4,
        people=[
            {
                "person_id": "person_acme_a",
                "display_name": "Alice",
                "home_store_code": "s1",
                "manager_code": "m1",
            }
        ],
        stores={"s1": "store_acme_s1"},
    )
    wb = load_workbook(BytesIO(data))
    ws = wb["m1"]
    ws["D2"] = "NORMAL - does-not-exist"
    ws["E2"] = "BROKEN"
    out = BytesIO()
    wb.save(out)
    parsed = parse_schedule(
        out.getvalue(),
        expected_tenant_id="tenant_acme",
        expected_month_id="month_acme_2026_08",
        year=2026,
        month=8,
        stores={"s1": "store_acme_s1"},
    )
    assert {e["code"] for e in parsed.errors} == {"UNKNOWN_STORE", "MALFORMED_CELL"}


def test_template_dropdowns_are_person_specific():
    data = build_template(
        tenant_id="tenant_acme",
        month_id="month_acme_2026_08",
        year=2026,
        month=8,
        base_revision=0,
        people=[
            {
                "person_id": "person_acme_a",
                "display_name": "Alice",
                "home_store_code": "s1",
                "manager_code": "m1",
            }
        ],
        stores={"s1": "store_acme_s1", "s2": "store_acme_s2"},
    )
    workbook = load_workbook(BytesIO(data), data_only=True)
    options = [workbook["_Lists"].cell(row, 1).value for row in range(1, 6)]
    assert options == [
        "LIBER",
        "CONCEDIU",
        "NORMAL - s1",
        "SUPLIMENTAR ACASĂ - s1",
        "SUPLIMENTAR - s2",
    ]
