"""scripts/create_admin.py - bootstraps the first admin (chicken-and-egg: only an admin can add users)."""
import importlib.util
import pathlib
import sys

from src.db.models import get_user_by_whatsapp_number

_PATH = pathlib.Path(__file__).resolve().parent.parent / "scripts" / "create_admin.py"


def _run(monkeypatch, *args):
    spec = importlib.util.spec_from_file_location("create_admin_script", _PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    monkeypatch.setattr(sys, "argv", ["create_admin.py", *args])
    return module.main()


def test_creates_a_new_admin(db_path, monkeypatch):
    assert _run(monkeypatch, "972500000001", "Admin One") == 0
    user = get_user_by_whatsapp_number("972500000001")
    assert user["is_admin"] == 1 and user["is_active"] == 1


def test_promotes_an_existing_user_and_is_idempotent(db_path, make_user, monkeypatch):
    make_user(whatsapp_number="972500000002", display_name="Existing")
    assert _run(monkeypatch, "972500000002", "Existing") == 0
    assert _run(monkeypatch, "972500000002", "Existing") == 0
    assert get_user_by_whatsapp_number("972500000002")["is_admin"] == 1


def test_rejects_a_non_numeric_number(db_path, monkeypatch, capsys):
    assert _run(monkeypatch, "+972-50", "X") == 1
