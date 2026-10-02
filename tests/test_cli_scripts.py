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
