from datetime import date, datetime, timedelta, timezone
from io import BytesIO
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from openpyxl import load_workbook
from PIL import Image

from app.api import attendance as attendance_api
from app.api import admin as admin_api
from app.core.security import current_claims
from app.main import app
from app.services.photo_service import PhotoUnavailableError


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


def exported_iso_date(value):
    return value.date().isoformat() if isinstance(value, datetime) else value.isoformat() if isinstance(value, date) else value


@pytest.mark.parametrize(
    ("params", "expected_bounds", "expected_dates", "expected_filename"),
    [
        ({"range": "day", "date": "2026-09-23"}, ("2026-09-23", "2026-09-23"), ["2026-09-23"], "aurelix-attendance-day-23-09-2026.xlsx"),
        ({"range": "custom", "start_date": "2026-09-22", "end_date": "2026-09-22"}, ("2026-09-22", "2026-09-22"), ["2026-09-22"], "aurelix-attendance-custom-22-09-2026-to-22-09-2026.xlsx"),
        ({"range": "custom", "start_date": "2026-09-20", "end_date": "2026-09-25"}, ("2026-09-20", "2026-09-25"), ["2026-09-22", "2026-09-23"], "aurelix-attendance-custom-20-09-2026-to-25-09-2026.xlsx"),
        ({"range": "custom", "start_date": "2026-09-28", "end_date": "2026-10-05"}, ("2026-09-28", "2026-10-05"), ["2026-10-01"], "aurelix-attendance-custom-28-09-2026-to-05-10-2026.xlsx"),
        ({"range": "custom", "start_date": "2026-12-20", "end_date": "2027-01-10"}, ("2026-12-20", "2027-01-10"), [], "aurelix-attendance-custom-20-12-2026-to-10-01-2027.xlsx"),
        ({"range": "month", "date": "2026-09-23"}, ("2026-09-01", "2026-09-30"), ["2026-09-01", "2026-09-22", "2026-09-23"], "aurelix-attendance-month-01-09-2026-to-30-09-2026.xlsx"),
        ({"range": "months", "start_date": "2026-01-01", "end_date": "2026-03-01"}, ("2026-01-01", "2026-03-31"), ["2026-01-15", "2026-03-31"], "aurelix-attendance-months-01-01-2026-to-31-03-2026.xlsx"),
        ({"range": "year", "date": "2026-09-23"}, ("2026-01-01", "2026-12-31"), ["2026-01-15", "2026-03-31", "2026-09-01", "2026-09-22", "2026-09-23", "2026-10-01"], "aurelix-attendance-year-01-01-2026-to-31-12-2026.xlsx"),
    ],
)
def test_export_uses_computed_date_range_and_returns_only_matching_rows(monkeypatch, params, expected_bounds, expected_dates, expected_filename):
    records = [record("2025-12-31"), record("2026-01-15"), record("2026-03-31"), record("2026-09-01"), record("2026-09-22"), record("2026-09-23"), record("2026-10-01")]
    client, attendance = export_client(monkeypatch, records)
    try:
        response = client.get("/api/admin/export", params=params)
        sheet = read_sheet(response)
        assert sheet["A2"].number_format == "dd-mm-yyyy"
        assert attendance.filters[-1] == {"date": {"$gte": expected_bounds[0], "$lte": expected_bounds[1]}}
        rows = list(sheet.iter_rows(min_row=2, values_only=True))
        assert all(row[1] == date.fromisoformat(exported_iso_date(row[0])).strftime("%A") for row in rows)
        actual_dates = [exported_iso_date(row[0]) for row in rows if row[2] == "EMP-1" and row[13] != "ABSENT"]
        assert set(actual_dates) == set(expected_dates)
        assert actual_dates == sorted(actual_dates, reverse=True)
        expected_range_dates = [
            (date.fromisoformat(expected_bounds[0]) + timedelta(days=offset)).isoformat()
            for offset in range((date.fromisoformat(expected_bounds[1]) - date.fromisoformat(expected_bounds[0])).days + 1)
        ]
        absent_rows = [row for row in rows if row[2] == "EMP-2"]
        assert [exported_iso_date(row[0]) for row in absent_rows] == sorted(expected_range_dates, reverse=True)
        assert all(row[13] == "ABSENT" for row in absent_rows)
        for employee_id in ("EMP-1", "EMP-2"):
            employee_rows = [row for row in rows if row[2] == employee_id]
            assert [exported_iso_date(row[0]) for row in employee_rows] == sorted(expected_range_dates, reverse=True)
        assert len(rows) == len(expected_range_dates) * 2
        assert len(attendance.records) == 7
        assert response.headers["content-type"].startswith("application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
        assert response.headers["content-disposition"].startswith("attachment; filename=")
        assert expected_filename in response.headers["content-disposition"]
    finally:
        app.dependency_overrides.clear()


@pytest.mark.parametrize(
    ("attendance_date", "expected_day"),
    [
        ("2026-09-30", "Wednesday"),
        ("2026-10-01", "Thursday"),
        ("2026-12-31", "Thursday"),
        ("2027-01-01", "Friday"),
        ("2024-02-29", "Thursday"),
    ],
)
def test_export_day_column_uses_actual_calendar_weekday(monkeypatch, attendance_date, expected_day):
    client, _attendance = export_client(monkeypatch, [record(attendance_date)])
    try:
        sheet = read_sheet(client.get("/api/admin/export", params={"range": "day", "date": attendance_date}))

        assert exported_iso_date(sheet["A2"].value) == attendance_date
        assert sheet["A2"].number_format == "dd-mm-yyyy"
        assert sheet["B2"].value == expected_day
    finally:
        app.dependency_overrides.clear()


def test_two_export_periods_produce_different_workbook_contents(monkeypatch):
    client, _attendance = export_client(monkeypatch, [record("2026-09-22", "EMP-1"), record("2026-10-01", "EMP-2")])
    try:
        day_sheet = read_sheet(client.get("/api/admin/export", params={"range": "day", "date": "2026-09-22"}))
        month_sheet = read_sheet(client.get("/api/admin/export", params={"range": "month", "date": "2026-10-01"}))
        day_rows = list(day_sheet.iter_rows(min_row=2, values_only=True))
        assert [(exported_iso_date(row[0]), row[2], row[13]) for row in day_rows] == [
            ("2026-09-22", "EMP-1", "PRESENT"),
            ("2026-09-22", "EMP-2", "ABSENT"),
        ]
        month_rows = list(month_sheet.iter_rows(min_row=2, values_only=True))
        assert len(month_rows) == 62
        assert [exported_iso_date(row[0]) for row in month_rows if row[2] == "EMP-1"] == [f"2026-10-{day:02d}" for day in range(31, 0, -1)]
        assert all(row[13] == "ABSENT" for row in month_rows if row[2] == "EMP-1")
        assert sum(row[13] == "PRESENT" and exported_iso_date(row[0]) == "2026-10-01" for row in month_rows if row[2] == "EMP-2") == 1
        assert sum(row[13] == "ABSENT" for row in month_rows if row[2] == "EMP-2") == 30
    finally:
        app.dependency_overrides.clear()


def test_custom_export_includes_exact_dates_across_month_and_year_boundaries(monkeypatch):
    client, attendance = export_client(monkeypatch, [record("2026-09-29", "EMP-1"), record("2027-01-01", "EMP-1")])
    try:
        response = client.get("/api/admin/export", params={"range": "custom", "start_date": "2026-12-20", "end_date": "2027-01-10"})
        sheet = read_sheet(response)
        rows = list(sheet.iter_rows(min_row=2, values_only=True))
        dates = {(date(2026, 12, 20) + timedelta(days=offset)).isoformat() for offset in range(22)}

        assert attendance.filters[-1] == {"date": {"$gte": "2026-12-20", "$lte": "2027-01-10"}}
        assert len(rows) == 44
        assert {(exported_iso_date(row[0]), row[2]) for row in rows} == {(day, employee_id) for day in dates for employee_id in ("EMP-1", "EMP-2")}
        assert all(row[13] == ("PRESENT" if exported_iso_date(row[0]) == "2027-01-01" and row[2] == "EMP-1" else "ABSENT") for row in rows)
        assert len(attendance.records) == 2
    finally:
        app.dependency_overrides.clear()


def test_custom_export_rejects_start_date_after_end_date(monkeypatch):
    client, attendance = export_client(monkeypatch, [])
    try:
        response = client.get("/api/admin/export", params={"range": "custom", "start_date": "2026-10-05", "end_date": "2026-09-28"})
        assert response.status_code == 422
        assert response.json()["detail"] == "Start date must not be after end date"
        assert not attendance.filters
    finally:
        app.dependency_overrides.clear()


def test_custom_export_requires_both_dates(monkeypatch):
    client, attendance = export_client(monkeypatch, [])
    try:
        response = client.get("/api/admin/export", params={"range": "custom", "start_date": "2026-09-22"})
        assert response.status_code == 422
        assert response.json()["detail"] == "Start and end dates are required for a custom date range"
        assert not attendance.filters
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
        rows = {row[2]: row for row in sheet.iter_rows(min_row=2, values_only=True)}

        assert rows["EMP-INACTIVE"][3] == "Inactive Employee"
        assert rows["EMP-INACTIVE"][13] == "PRESENT"
        assert rows["EMP-MISSING"][3] == "Former Person"
        assert rows["EMP-MISSING"][13] == "PRESENT"
        assert rows["EMP-1"][13] == rows["EMP-2"][13] == "ABSENT"
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
        assert [cell.value for cell in sheet[1]] == ["Date", "Day", "Employee ID", "Employee", "Email", "Department", "Check In", "Check In Location", "Check-in Photo", "Check Out", "Working Hours", "Check Out Location", "Check-out Photo", "Status"]
        assert [sheet[f"N{row}"].value for row in (2, 3)] == ["ABSENT", "ABSENT"]
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
        assert sheet["G2"].value == "02:17:33 pm IST"
        assert sheet["J2"].value == "02:45:10 pm IST"
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

        assert sheet["K2"].value == timedelta(hours=8, minutes=40, seconds=27)
        assert sheet["K2"].number_format == "[h]:mm:ss"
        assert sheet["J3"].value == "—"
        assert sheet["K3"].value == "—"
        assert sheet["K3"].number_format == "[h]:mm:ss"
        assert sheet["G4"].value == "—"
        assert sheet["K4"].value == "—"
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
        assert sheet["K2"].value == "—"
    finally:
        app.dependency_overrides.clear()


def test_export_embeds_old_check_in_and_check_out_photos_without_expiry(monkeypatch):
    image_buffer = BytesIO()
    Image.new("RGB", (800, 400), "navy").save(image_buffer, format="JPEG")
    image_data = image_buffer.getvalue()
    monkeypatch.setattr(admin_api, "open_photo", lambda _reference: BytesIO(image_data))
    old_date = "2023-09-23"
    old_expiry = datetime(2023, 9, 24, tzinfo=timezone.utc)
    client, _attendance = export_client(monkeypatch, [record(old_date, check_in_photo_reference={"file_id": "in", "expires_at": old_expiry}, check_out_photo_reference={"file_id": "out", "expires_at": old_expiry})])
    try:
        sheet = read_sheet(client.get("/api/admin/export", params={"range": "day", "date": old_date}))
        assert len(sheet._images) == 2
        assert sheet["I2"].value is None
        assert sheet["M2"].value is None
        assert sheet.row_dimensions[2].height == 115
        assert all(image.width <= 200 and image.height <= 150 for image in sheet._images)
    finally:
        app.dependency_overrides.clear()


def test_unavailable_or_missing_photos_do_not_fail_export(monkeypatch):
    def unavailable(_reference):
        raise PhotoUnavailableError("Photo unavailable")

    monkeypatch.setattr(admin_api, "open_photo", unavailable)
    client, _attendance = export_client(monkeypatch, [record("2026-09-23", check_in_photo_reference={"file_id": "gone"}, check_out_photo_reference=None)])
    try:
        sheet = read_sheet(client.get("/api/admin/export", params={"range": "day", "date": "2026-09-23"}))
        assert sheet["I2"].value == "Unavailable"
        assert sheet["M2"].value == "—"
        assert not sheet._images
    finally:
        app.dependency_overrides.clear()


def test_exports_reflect_undo_actions_and_keep_unaffected_photo(monkeypatch):
    old_expiry = datetime(2020, 1, 1, tzinfo=timezone.utc)
    attendance_record = record(
        "2026-09-23",
        _id="mongo-1",
        attendance_id="att-1",
        check_in_photo_reference={"file_id": "in", "content_type": "image/jpeg", "expires_at": old_expiry},
        check_out_photo_reference={"file_id": "out", "content_type": "image/jpeg", "expires_at": old_expiry},
    )

    class MutableAttendance:
        def __init__(self):
            self.records = [attendance_record]

        def find(self, query, _projection=None):
            bounds = query.get("date", {})
            return MemoryCursor([
                item for item in self.records
                if bounds.get("$gte", "") <= item["date"] <= bounds.get("$lte", "9999-12-31")
            ])

        def find_one(self, query):
            return next((item for item in self.records if item.get("attendance_id") == query.get("attendance_id")), None)

        def update_one(self, query, update):
            target = next((item for item in self.records if item.get("_id") == query.get("_id")), None)
            if not target:
                return SimpleNamespace(matched_count=0)
            for key, condition in query.items():
                if key == "_id":
                    continue
                if isinstance(condition, dict) and "$ne" in condition:
                    if target.get(key) == condition["$ne"]:
                        return SimpleNamespace(matched_count=0)
                elif target.get(key) != condition:
                    return SimpleNamespace(matched_count=0)
            target.update(update["$set"])
            return SimpleNamespace(matched_count=1)

    class Employees:
        def find(self, query, _projection=None):
            if "employee_id" in query:
                return [{"employee_id": "EMP-1", "full_name": "Asha Rao", "email": "asha@example.com", "department": "Operations"}]
            return [{"employee_id": "EMP-1", "full_name": "Asha Rao", "email": "asha@example.com", "department": "Operations", "role": "employee", "is_active": True}]

    class Audit:
        def insert_one(self, _document):
            return None

    attendance = MutableAttendance()
    db = SimpleNamespace(attendance=attendance, employees=Employees(), audit_logs=Audit())
    monkeypatch.setattr(attendance_api, "get_db", lambda: db)
    monkeypatch.setattr(admin_api, "get_db", lambda: db)
    image_buffer = BytesIO()
    Image.new("RGB", (32, 24), "navy").save(image_buffer, format="JPEG")
    image_data = image_buffer.getvalue()
    monkeypatch.setattr(admin_api, "open_photo", lambda _reference: BytesIO(image_data))
    app.dependency_overrides[current_claims] = lambda: {"sub": "ADM-1", "role": "admin"}
    client = TestClient(app)
    try:
        checkout_undo = client.post("/api/attendance/admin/att-1/undo-check-out")
        assert checkout_undo.status_code == 200
        checkout_sheet = read_sheet(client.get("/api/admin/export", params={"range": "day", "date": "2026-09-23"}))
        assert checkout_sheet["J2"].value == "—"
        assert checkout_sheet["K2"].value == "—"
        assert len(checkout_sheet._images) == 1
        assert checkout_sheet["I2"].value is None
        assert checkout_sheet["M2"].value == "—"
        assert attendance_record["check_in_photo_reference"]["file_id"] == "in"
        assert attendance_record["check_out_photo_reference"] is None

        checkin_undo = client.post("/api/attendance/admin/att-1/undo-check-in")
        assert checkin_undo.status_code == 200
        checkin_sheet = read_sheet(client.get("/api/admin/export", params={"range": "day", "date": "2026-09-23"}))
        assert checkin_sheet["G2"].value == "—"
        assert checkin_sheet["J2"].value == "—"
        assert checkin_sheet["I2"].value == "—"
        assert checkin_sheet["M2"].value == "—"
        assert not checkin_sheet._images
        assert attendance_record["check_in_photo_reference"] is None
        assert attendance_record["check_out_photo_reference"] is None
        assert attendance_record["final_status"] == "ABSENT"
    finally:
        app.dependency_overrides.clear()


def test_resolve_export_date_range_normalizes_month_boundaries():
    assert admin_api.resolve_export_date_range("months", None, date(2026, 1, 22), date(2026, 3, 4)) == (date(2026, 1, 1), date(2026, 3, 31))
    assert admin_api.resolve_export_date_range("custom", None, date(2026, 12, 20), date(2027, 1, 10)) == (date(2026, 12, 20), date(2027, 1, 10))
