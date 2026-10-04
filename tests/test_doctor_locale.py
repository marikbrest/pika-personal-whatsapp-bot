"""scripts/doctor.py LOCALE check (2026-10-04): LOCALE must be supported and should match the template language."""
import importlib.util
import pathlib

SCRIPT = pathlib.Path(__file__).resolve().parent.parent / "scripts" / "doctor.py"


def _run_check(monkeypatch, capsys, **env):
    for name in ("LOCALE", "WHATSAPP_TEMPLATE_LANGUAGE"):
        monkeypatch.delenv(name, raising=False)
    for name, value in env.items():
        monkeypatch.setenv(name, value)
    spec = importlib.util.spec_from_file_location("doctor_under_test", SCRIPT)
    doctor = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(doctor)
    doctor.check_required_env()
    return capsys.readouterr().out


def test_unsupported_locale_fails(monkeypatch, capsys):
    out = _run_check(monkeypatch, capsys, LOCALE="fr")
    assert "FAIL" in out and "LOCALE" in out and "not a supported language" in out


def test_locale_and_template_language_that_differ_warn(monkeypatch, capsys):
    out = _run_check(monkeypatch, capsys, LOCALE="en", WHATSAPP_TEMPLATE_LANGUAGE="he")
    assert "LOCALE vs WHATSAPP_TEMPLATE_LANGUAGE" in out and "WARN" in out


def test_matching_locale_and_template_language_is_ok(monkeypatch, capsys):
    out = _run_check(monkeypatch, capsys, LOCALE="en", WHATSAPP_TEMPLATE_LANGUAGE="en_US")
    assert "LOCALE" in out and "en" in out and "vs WHATSAPP_TEMPLATE_LANGUAGE" not in out
