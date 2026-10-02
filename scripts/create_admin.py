"""
Bootstrap the first admin user.

New users can only be added by an existing admin (via chat or the dashboard),
so the very first admin has to be created out-of-band. Run once, after
configuring .env:

    python scripts/create_admin.py 972501234567 "Your Name"

The number is the admin's WhatsApp number in international format, digits only
(no +). Safe to re-run: an existing number is just promoted to admin.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.db.models import admin_add_user, get_connection, init_db  # noqa: E402


def main() -> int:
    if len(sys.argv) != 3 or not sys.argv[1].isdigit():
        print(__doc__)
        return 1
    number, name = sys.argv[1], sys.argv[2]
    init_db()
    created = admin_add_user(number, name)
    conn = get_connection()
    try:
        conn.execute("UPDATE users SET is_admin = 1, is_active = 1 WHERE whatsapp_number = ?", (number,))
        conn.commit()
    finally:
        conn.close()
    print(f"{'Created' if created else 'Updated existing'} admin user {name} ({number}).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
