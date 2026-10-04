"""scripts/doctor.py and scripts/chat.py - the setup/evaluation helpers."""
import os
import subprocess
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _run(script, args=(), env_extra=None, stdin=""):
    env = {k: v for k, v in os.environ.items() if k.startswith(("PATH", "SYSTEMROOT", "TEMP", "TMP", "HOME", "USERPROFILE"))}
    env["PYTHONUTF8"] = "1"
    env.update(env_extra or {})
    return subprocess.run(
        [sys.executable, os.path.join(ROOT, "scripts", script), *args],
        input=stdin, capture_output=True, text=True, encoding="utf-8", env=env, cwd=ROOT, timeout=60,
    )


@pytest.mark.skipif(os.path.exists(os.path.join(ROOT, ".env")), reason="a local .env would fill in the missing values")
def test_doctor_fails_and_names_what_is_missing_on_an_empty_environment(tmp_path):
    r = _run("doctor.py", env_extra={"DB_PATH": str(tmp_path / "x.db")})
    assert r.returncode == 1
    assert "WHATSAPP_ACCESS_TOKEN" in r.stdout and "GEMINI_API_KEY" in r.stdout


def test_doctor_passes_a_complete_offline_setup(tmp_path):
    from cryptography.fernet import Fernet

    db = tmp_path / "a.db"
    env = {
        "DB_PATH": str(db), "WHATSAPP_ACCESS_TOKEN": "t", "WHATSAPP_PHONE_NUMBER_ID": "1",
        "WHATSAPP_WEBHOOK_VERIFY_TOKEN": "v", "WHATSAPP_APP_SECRET": "s", "GEMINI_API_KEY": "g",
        "TOKEN_ENCRYPTION_KEY": Fernet.generate_key().decode(),
    }
    # no admin yet -> database check warns, and only warnings => exit 0
    r = _run("doctor.py", env_extra=env)
    assert r.returncode == 0, r.stdout
    assert "failed" in r.stdout and "0 failed" in r.stdout


def test_doctor_rejects_a_malformed_encryption_key(tmp_path):
    env = {"DB_PATH": str(tmp_path / "b.db"), "TOKEN_ENCRYPTION_KEY": "not-a-key"}
    r = _run("doctor.py", env_extra=env)
    assert r.returncode == 1 and "valid Fernet key" in r.stdout


def test_chat_requires_a_gemini_key():
    r = _run("chat.py", env_extra={"GEMINI_API_KEY": ""}, stdin="/quit\n")
    assert r.returncode != 0 and "GEMINI_API_KEY" in (r.stdout + r.stderr)


def test_chat_starts_uses_its_own_sandbox_db_and_quits(tmp_path):
    sandbox = tmp_path / "sandbox.db"
    real = tmp_path / "real.db"
    r = _run("chat.py", env_extra={"GEMINI_API_KEY": "fake", "PIKA_SANDBOX_DB": str(sandbox), "DB_PATH": str(real)}, stdin="/help\n/quit\n")
    assert r.returncode == 0, r.stderr
    assert "Pika sandbox" in r.stdout
    assert sandbox.exists()
    assert not real.exists()  # a real DB_PATH must never be touched


def test_backup_creates_a_consistent_copy_and_prunes_old_ones(tmp_path):
    import sqlite3

    db = tmp_path / "live.db"
    conn = sqlite3.connect(db)
    conn.execute("CREATE TABLE t (x)")
    conn.execute("INSERT INTO t VALUES (42)")
    conn.commit()
    conn.close()
    out = tmp_path / "bk"
    out.mkdir()
    for i in range(3):  # pre-existing older backups
        (out / f"assistant_2020-01-0{i + 1}_000000.db").write_bytes(b"old")
    r = _run("backup_db.py", ["--out", str(out), "--keep", "2"], env_extra={"DB_PATH": str(db)})
    assert r.returncode == 0, r.stderr
    kept = sorted(p.name for p in out.iterdir())
    assert len(kept) == 2
    newest = out / kept[-1]
    assert sqlite3.connect(newest).execute("SELECT x FROM t").fetchone() == (42,)


def test_backup_fails_clearly_when_the_database_is_missing(tmp_path):
    r = _run("backup_db.py", ["--out", str(tmp_path)], env_extra={"DB_PATH": str(tmp_path / "nope.db")})
    assert r.returncode == 1 and "database not found" in r.stderr


def _doctor_env(tmp_path, **extra):
    from cryptography.fernet import Fernet

    env = {
        "DB_PATH": str(tmp_path / "d.db"), "WHATSAPP_ACCESS_TOKEN": "t", "WHATSAPP_PHONE_NUMBER_ID": "1",
        "WHATSAPP_WEBHOOK_VERIFY_TOKEN": "v", "WHATSAPP_APP_SECRET": "s", "GEMINI_API_KEY": "g",
        "TOKEN_ENCRYPTION_KEY": Fernet.generate_key().decode(),
        "OPERATOR_NAME": "", "ADMIN_CONTACT_EMAIL": "",
    }
    env.update(extra)
    return env


def test_doctor_warns_when_the_public_legal_pages_would_show_placeholders(tmp_path):
    r = _run("doctor.py", env_extra=_doctor_env(tmp_path))
    assert r.returncode == 0, r.stdout
    assert "OPERATOR_NAME" in r.stdout and "set it before you invite anyone" in r.stdout


def test_doctor_reminds_about_the_gemini_tier_only_when_google_is_connected(tmp_path):
    without = _run("doctor.py", env_extra=_doctor_env(tmp_path))
    assert "Gemini plan" not in without.stdout
    google = {"GOOGLE_CLIENT_ID": "id", "GOOGLE_CLIENT_SECRET": "sec", "GOOGLE_REDIRECT_URI": "https://real.example.org/oauth/callback"}
    with_google = _run("doctor.py", env_extra=_doctor_env(tmp_path, **google))
    assert with_google.returncode == 0, with_google.stdout
    assert "Gemini plan" in with_google.stdout and "billing enabled" in with_google.stdout


def test_doctor_fails_when_the_default_provider_is_unusable(tmp_path):
    unknown = _run("doctor.py", env_extra=_doctor_env(tmp_path, AI_DEFAULT_PROVIDER="claude"))
    assert unknown.returncode == 1 and "not a known provider" in unknown.stdout
    no_key = _run("doctor.py", env_extra=_doctor_env(tmp_path, AI_DEFAULT_PROVIDER="openai"))
    assert no_key.returncode == 1 and "OPENAI_API_KEY / OPENAI_MODEL" in no_key.stdout


def test_doctor_explains_that_openai_needs_a_model_name_and_prices(tmp_path):
    key_only = _run("doctor.py", env_extra=_doctor_env(tmp_path, OPENAI_API_KEY="k"))
    assert key_only.returncode == 0 and "OPENAI_MODEL is not" in key_only.stdout
    unpriced = _run("doctor.py", env_extra=_doctor_env(tmp_path, OPENAI_API_KEY="k", OPENAI_MODEL="m"))
    assert "OpenAI enabled" in unpriced.stdout and "unpriced" in unpriced.stdout
    priced = _run("doctor.py", env_extra=_doctor_env(
        tmp_path, OPENAI_API_KEY="k", OPENAI_MODEL="m", OPENAI_PRICE_INPUT_PER_M="1", OPENAI_PRICE_OUTPUT_PER_M="2"))
    assert "cost report priced" in priced.stdout
