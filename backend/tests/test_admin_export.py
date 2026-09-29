from datetime import date, datetime, timedelta, timezone
from io import BytesIO
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from openpyxl import load_workbook
from PIL import Image

from app.api import admin as admin_api
from app.core.security import current_claims
from app.main import app
from app.services.photo_service import PhotoExpiredError


class MemoryCursor:
    def __init__(self, records):
        self.records = records

    def sort(self, *_args):
        return self

    def __iter__(self):
        return iter(self.records)


class MemoryAttendance:
    def __init__(self, records):
        self.records = records
        self.filters = []

    def find(self, query, _projection=None):
        self.filters.append(query)
        bounds = query.get("date", {})
        records = [
            record for record in self.records
            if bounds.get("$gte", "") <= record["date"] <= bounds.get("$lte", "9999-12-31")
        ]
        return MemoryCursor(records)


class MemoryEmployees:
    def __init__(self, employees):
        self.employees = employees

    def find(self, query, _projection=None):
        if "employee_id" in query:
            employee_ids = set(query["employee_id"]["$in"])
            return [employee for employee in self.employees if employee["employee_id"] in employee_ids]
        return [employee for employee in self.employees if all(employee.get(key) == value for key, value in query.items())]


def record(attendance_date, employee_id="EMP-1", **extra):
    return {
        "date": attendance_date,
        "employee_id": employee_id,
        "check_in_time": "2026-09-22T09:00:00+05:30",
        "check_out_time": "2026-09-22T18:00:00+05:30",
        "check_in_location": {"latitude": 18.52, "longitude": 73.85},
        "check_out_location": {"latitude": 18.53, "longitude": 73.86},
        "final_status": "PRESENT",
        **extra,
    }


def export_client(monkeypatch, records, employee_records=None):
    attendance = MemoryAttendance(records)
    employees = MemoryEmployees(employee_records if employee_records is not None else [
        {"employee_id": "EMP-1", "full_name": "Asha Rao", "email": "asha@example.com", "department": "Operations", "role": "employee", "is_active": True},
        {"employee_id": "EMP-2", "full_name": "Dev Patel", "email": "dev@example.com", "department": "Engineering", "role": "employee", "is_active": True},
    ])
    monkeypatch.setattr(admin_api, "get_db", lambda: SimpleNamespace(attendance=attendance, employees=employees))
    app.dependency_overrides[current_claims] = lambda: {"sub": "ADM-1", "role": "admin"}
    return TestClient(app), attendance


def read_sheet(response):
    assert response.status_code == 200
    return load_workbook(BytesIO(response.content)).active


@pytest.mark.parametrize(
    ("params", "expected_bounds", "expected_dates", "expected_filename"),
    [
        ({"range": "day", "date": "2026-09-23"}, ("2026-09-23", "2026-09-23"), ["2026-09-23"], "aurelix-attendance-day-2026-09-23.xlsx"),
        ({"range": "week", "date": "2026-09-23"}, ("2026-09-21", "2026-09-27"), ["2026-09-22", "2026-09-23"], "aurelix-attendance-week-2026-09-21-to-2026-09-27.xlsx"),
        ({"range": "month", "date": "2026-09-23"}, ("2026-09-01", "2026-09-30"), ["2026-09-01", "2026-09-22", "2026-09-23"], "aurelix-attendance-month-2026-09.xlsx"),
        ({"range": "months", "start_date": "2026-01-01", "end_date": "2026-03-01"}, ("2026-01-01", "2026-03-31"), ["2026-01-15", "2026-03-31"], "aurelix-attendance-months-2026-01-to-2026-03.xlsx"),
        ({"range": "year", "date": "2026-09-23"}, ("2026-01-01", "2026-12-31"), ["2026-01-15", "2026-03-31", "2026-09-01", "2026-09-22", "2026-09-23", "2026-10-01"], "aurelix-attendance-year-2026.xlsx"),
    ],
)
def test_export_uses_computed_date_range_and_returns_only_matching_rows(monkeypatch, params, expected_bounds, expected_dates, expected_filename):
    records = [record("2025-12-31"), record("2026-01-15"), record("2026-03-31"), record("2026-09-01"), record("2026-09-22"), record("2026-09-23"), record("2026-10-01")]
    client, attendance = export_client(monkeypatch, records)
    try:
        response = client.get("/api/admin/export", params=params)
        sheet = read_sheet(response)
        assert attendance.filters[-1] == {"date": {"$gte": expected_bounds[0], "$lte": expected_bounds[1]}}
        rows = list(sheet.iter_rows(min_row=2, values_only=True))
        actual_dates = [row[0] for row in rows if row[1] == "EMP-1" and row[12] != "ABSENT"]
        assert set(actual_dates) == set(expected_dates)
        assert actual_dates == sorted(actual_dates, reverse=True)
        expected_range_dates = [
            (date.fromisoformat(expected_bounds[0]) + timedelta(days=offset)).isoformat()
            for offset in range((date.fromisoformat(expected_bounds[1]) - date.fromisoformat(expected_bounds[0])).days + 1)
        ]
        absent_rows = [row for row in rows if row[1] == "EMP-2"]
        assert [row[0] for row in absent_rows] == sorted(expected_range_dates, reverse=True)
        assert all(row[12] == "ABSENT" for row in absent_rows)
        for employee_id in ("EMP-1", "EMP-2"):
            employee_rows = [row for row in rows if row[1] == employee_id]
            assert [row[0] for row in employee_rows] == sorted(expected_range_dates, reverse=True)
        assert len(rows) == len(expected_range_dates) * 2
        assert len(attendance.records) == 7
        assert response.headers["content-type"].startswith("application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
        assert response.headers["content-disposition"].startswith("attachment; filename=")
        assert expected_filename in response.headers["content-disposition"]
    finally:
        app.dependency_overrides.clear()


def test_two_export_periods_produce_different_workbook_contents(monkeypatch):
    client, _attendance = export_client(monkeypatch, [record("2026-09-22", "EMP-1"), record("2026-10-01", "EMP-2")])
    try:
        day_sheet = read_sheet(client.get("/api/admin/export", params={"range": "day", "date": "2026-09-22"}))
        month_sheet = read_sheet(client.get("/api/admin/export", params={"range": "month", "date": "2026-10-01"}))
        day_rows = list(day_sheet.iter_rows(min_row=2, values_only=True))
        assert [(row[0], row[1], row[12]) for row in day_rows] == [
            ("2026-09-22", "EMP-1", "PRESENT"),
            ("2026-09-22", "EMP-2", "ABSENT"),
        ]
        month_rows = list(month_sheet.iter_rows(min_row=2, values_only=True))
        assert len(month_rows) == 62
        assert [row[0] for row in month_rows if row[1] == "EMP-1"] == [f"2026-10-{day:02d}" for day in range(31, 0, -1)]
        assert all(row[12] == "ABSENT" for row in month_rows if row[1] == "EMP-1")
        assert sum(row[12] == "PRESENT" and row[0] == "2026-10-01" for row in month_rows if row[1] == "EMP-2") == 1
        assert sum(row[12] == "ABSENT" for row in month_rows if row[1] == "EMP-2") == 30
    finally:
        app.dependency_overrides.clear()


def test_week_export_includes_all_seven_dates_across_month_boundary(monkeypatch):
    client, attendance = export_client(monkeypatch, [record("2026-09-29", "EMP-1")])
    try:
        response = client.get("/api/admin/export", params={"range": "week", "date": "2026-09-29"})
        sheet = read_sheet(response)
        rows = list(sheet.iter_rows(min_row=2, values_only=True))
        dates = {f"2026-09-{day:02d}" for day in range(28, 31)} | {f"2026-10-{day:02d}" for day in range(1, 5)}

        assert attendance.filters[-1] == {"date": {"$gte": "2026-09-28", "$lte": "2026-10-04"}}
        assert len(rows) == 14
        assert {(row[0], row[1]) for row in rows} == {(day, employee_id) for day in dates for employee_id in ("EMP-1", "EMP-2")}
        assert all(row[12] == ("PRESENT" if row[0] == "2026-09-29" and row[1] == "EMP-1" else "ABSENT") for row in rows)
        assert len(attendance.records) == 1
    finally:
        app.dependency_overrides.clear()


def test_export_keeps_existing_inactive_and_missing_employee_records(monkeypatch):
    records = [
        record("2026-09-23", "EMP-INACTIVE", user_name="Inactive Person"),
        record("2026-09-23", "EMP-MISSING", user_name="Former Person"),
    ]
    employee_records = [
        {"employee_id": "EMP-1", "full_name": "Asha Rao", "email": "asha@example.com", "department": "Operations", "role": "employee", "is_active": True},
        {"employee_id": "EMP-2", "full_name": "Dev Patel", "email": "dev@example.com", "department": "Engineering", "role": "employee", "is_active": True},
        {"employee_id": "EMP-INACTIVE", "full_name": "Inactive Employee", "email": "inactive@example.com", "department": "Former", "role": "employee", "is_active": False},
    ]
    client, _attendance = export_client(monkeypatch, records, employee_records)
    try:
        sheet = read_sheet(client.get("/api/admin/export", params={"range": "day", "date": "2026-09-23"}))
        rows = {row[1]: row for row in sheet.iter_rows(min_row=2, values_only=True)}

        assert rows["EMP-INACTIVE"][2] == "Inactive Employee"
        assert rows["EMP-INACTIVE"][12] == "PRESENT"
        assert rows["EMP-MISSING"][2] == "Former Person"
        assert rows["EMP-MISSING"][12] == "PRESENT"
        assert rows["EMP-1"][12] == rows["EMP-2"][12] == "ABSENT"
    finally:
        app.dependency_overrides.clear()


def test_multiple_months_requires_ordered_months(monkeypatch):
    client, _attendance = export_client(monkeypatch, [])
    try:
        response = client.get("/api/admin/export", params={"range": "months", "start_date": "2026-04-01", "end_date": "2026-03-01"})
        assert response.status_code == 422
        assert response.json()["detail"] == "Start month must not be after end month"
    finally:
        app.dependency_overrides.clear()


def test_empty_export_has_headers_and_no_data_rows(monkeypatch):
    client, _attendance = export_client(monkeypatch, [])
    try:
        sheet = read_sheet(client.get("/api/admin/export", params={"range": "day", "date": "2026-09-23"}))
        assert sheet.max_row == 3
        assert [cell.value for cell in sheet[1]] == ["Date", "Employee ID", "Employee", "Email", "Department", "Check In", "Check In Location", "Check-in Photo", "Check Out", "Working Hours", "Check Out Location", "Check-out Photo", "Status"]
        assert [sheet[f"M{row}"].value for row in (2, 3)] == ["ABSENT", "ABSENT"]
    finally:
        app.dependency_overrides.clear()


def test_export_formats_utc_timestamps_as_kolkata_ist_strings(monkeypatch):
    client, _attendance = export_client(monkeypatch, [record(
        "2026-09-29",
        check_in_time=datetime(2026, 9, 29, 8, 47, 33, tzinfo=timezone.utc),
        check_out_time=datetime(2026, 9, 29, 9, 15, 10, tzinfo=timezone.utc),
    )])
    try:
        sheet = read_sheet(client.get("/api/admin/export", params={"range": "day", "date": "2026-09-29"}))
        assert sheet["F2"].value == "02:17:33 pm IST"
        assert sheet["I2"].value == "02:45:10 pm IST"
    finally:
        app.dependency_overrides.clear()


def test_export_writes_working_hours_as_excel_duration_and_marks_open_records(monkeypatch):
    records = [
        record("2026-09-23", check_in_time="2026-09-23T09:30:15+05:30", check_out_time="2026-09-23T18:10:42+05:30"),
        record("2026-09-23", check_in_time="2026-09-23T09:30:15+05:30", check_out_time=None),
        record("2026-09-23", check_in_time=None, check_out_time="2026-09-23T18:10:42+05:30"),
    ]
    client, _attendance = export_client(monkeypatch, records)
    try:
        sheet = read_sheet(client.get("/api/admin/export", params={"range": "day", "date": "2026-09-23"}))

        assert sheet["J2"].value == timedelta(hours=8, minutes=40, seconds=27)
        assert sheet["J2"].number_format == "[h]:mm:ss"
        assert sheet["I3"].value == "—"
        assert sheet["J3"].value == "—"
        assert sheet["J3"].number_format == "[h]:mm:ss"
        assert sheet["F4"].value == "—"
        assert sheet["J4"].value == "—"
    finally:
        app.dependency_overrides.clear()


@pytest.mark.parametrize(
    ("check_in_time", "check_out_time"),
    [
        ("invalid", "2026-09-23T18:10:42+05:30"),
        ("2026-09-23T18:10:42+05:30", "2026-09-23T09:30:15+05:30"),
        (None, "2026-09-23T18:10:42+05:30"),
    ],
)
def test_export_marks_invalid_or_negative_working_hours(monkeypatch, check_in_time, check_out_time):
    client, _attendance = export_client(monkeypatch, [record("2026-09-23", check_in_time=check_in_time, check_out_time=check_out_time)])
    try:
        sheet = read_sheet(client.get("/api/admin/export", params={"range": "day", "date": "2026-09-23"}))
        assert sheet["J2"].value == "—"
    finally:
        app.dependency_overrides.clear()


def test_export_embeds_retained_check_in_and_check_out_photos(monkeypatch):
    image_buffer = BytesIO()
    Image.new("RGB", (800, 400), "navy").save(image_buffer, format="JPEG")
    image_data = image_buffer.getvalue()
    monkeypatch.setattr(admin_api, "open_photo", lambda _reference: BytesIO(image_data))
    client, _attendance = export_client(monkeypatch, [record("2026-09-23", check_in_photo_reference={"file_id": "in"}, check_out_photo_reference={"file_id": "out"})])
    try:
        sheet = read_sheet(client.get("/api/admin/export", params={"range": "day", "date": "2026-09-23"}))
        assert len(sheet._images) == 2
        assert sheet["H2"].value is None
        assert sheet["L2"].value is None
        assert sheet.row_dimensions[2].height == 115
        assert all(image.width <= 200 and image.height <= 150 for image in sheet._images)
    finally:
        app.dependency_overrides.clear()


def test_expired_or_missing_photos_do_not_fail_export(monkeypatch):
    def expired(_reference):
        raise PhotoExpiredError("Photo expired or unavailable")

    monkeypatch.setattr(admin_api, "open_photo", expired)
    client, _attendance = export_client(monkeypatch, [record("2026-09-23", check_in_photo_reference={"file_id": "gone"}, check_out_photo_reference=None)])
    try:
        sheet = read_sheet(client.get("/api/admin/export", params={"range": "day", "date": "2026-09-23"}))
        assert sheet["H2"].value == "Expired / unavailable"
        assert sheet["L2"].value == "Expired / unavailable"
        assert not sheet._images
    finally:
        app.dependency_overrides.clear()


def test_resolve_export_date_range_normalizes_month_boundaries():
    assert admin_api.resolve_export_date_range("months", None, date(2026, 1, 22), date(2026, 3, 4)) == (date(2026, 1, 1), date(2026, 3, 31))
