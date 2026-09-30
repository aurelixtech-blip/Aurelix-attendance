import getpass
import os
import sys
from pathlib import Path
from pydantic import EmailStr, TypeAdapter

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.core.security import hash_password
from app.db.mongodb import get_db, init_indexes
from app.models.employee import employee_document


def main():
    email = os.getenv("ADMIN_EMAIL") or input("Admin email: ").strip()
    password = os.getenv("ADMIN_PASSWORD") or getpass.getpass("Admin password: ")
    full_name = os.getenv("ADMIN_NAME") or input("Admin full name: ").strip()
    employee_id = os.getenv("ADMIN_EMPLOYEE_ID") or input("Admin employee ID: ").strip()
    recovery_email_input = os.getenv("ADMIN_RECOVERY_EMAIL") or input("Admin recovery email: ").strip()
    recovery_email = str(TypeAdapter(EmailStr).validate_python(recovery_email_input)).lower()
    init_indexes()
    db = get_db()
    document = employee_document(employee_id, full_name, email, "Operations", "admin", hash_password(password), recovery_email)
    db.employees.update_one({"email": email.lower()}, {"$set": document}, upsert=True)
    print(f"Admin account ready for {email}")


if __name__ == "__main__":
    main()
