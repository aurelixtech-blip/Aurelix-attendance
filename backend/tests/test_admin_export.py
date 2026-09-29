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
        employee_ids = set(query["employee_id"]["$in"])
        return [employee for employee in self.employees if employee["employee_id"] in employee_ids]


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


def export_client(monkeypatch, records):
    attendance = MemoryAttendance(records)
    employees = MemoryEmployees([
        {"employee_id": "EMP-1", "full_name": "Asha Rao", "email": "asha@example.com", "department": "Operations"},
        {"employee_id": "EMP-2", "full_name": "Dev Patel", "email": "dev@example.com", "department": "Engineering"},
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
        assert [row[0] for row in sheet.iter_rows(min_row=2, values_only=True)] == expected_dates
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
        assert [row[0] for row in day_sheet.iter_rows(min_row=2, values_only=True)] == ["2026-09-22"]
        assert [row[0] for row in month_sheet.iter_rows(min_row=2, values_only=True)] == ["2026-10-01"]
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
        assert sheet.max_row == 1
        assert [cell.value for cell in sheet[1]] == ["Date", "Employee ID", "Employee", "Email", "Department", "Check In", "Check In Location", "Check-in Photo", "Check Out", "Working Hours", "Check Out Location", "Check-out Photo", "Status"]
    finally:
        app.dependency_overrides.clear()


def test_export_serializes_timezone_aware_mongo_timestamps(monkeypatch):
    client, _attendance = export_client(monkeypatch, [record(
        "2026-09-23",
        check_in_time=datetime(2026, 9, 23, 3, 30, tzinfo=timezone.utc),
        check_out_time=datetime(2026, 9, 23, 12, 30, tzinfo=timezone.utc),
    )])
    try:
        sheet = read_sheet(client.get("/api/admin/export", params={"range": "day", "date": "2026-09-23"}))
        assert sheet["F2"].value == "2026-09-23T03:30:00+00:00"
        assert sheet["I2"].value == "2026-09-23T12:30:00+00:00"
    finally:
        app.dependency_overrides.clear()


def test_export_writes_working_hours_as_excel_duration_and_marks_open_records(monkeypatch):
    records = [
        record("2026-09-23", check_in_time="2026-09-23T09:30:15+05:30", check_out_time="2026-09-23T18:10:42+05:30"),
        record("2026-09-23", check_in_time="2026-09-23T09:30:15+05:30", check_out_time=None),
    ]
    client, _attendance = export_client(monkeypatch, records)
    try:
        sheet = read_sheet(client.get("/api/admin/export", params={"range": "day", "date": "2026-09-23"}))

        assert sheet["J2"].value == timedelta(hours=8, minutes=40, seconds=27)
        assert sheet["J2"].number_format == "[h]:mm:ss"
        assert sheet["J3"].value == "—"
        assert sheet["J3"].number_format == "[h]:mm:ss"
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
