"""
Admin dashboard - reachable only via admin.your-domain.example, behind Cloudflare Access.

Two layers of protection, deliberately:
1. Host header check - these routes respond only to requests that arrived via
   admin.your-domain.example. Access through assistant.your-domain.example/admin (where the public
   webhook lives) is always rejected. As a result, until DNS/tunnel are
   configured the dashboard simply is not reachable from the internet at all.
2. Cf-Access-Authenticated-User-Email check - the header Cloudflare Access adds
   after a successful authentication. If it is missing, the request did not go
   through Access (for example if the tunnel route was configured before the
   Access policy) and is blocked.

ADMIN_HOST and ADMIN_ALLOWED_EMAIL are configured in .env.
"""
import html as html_lib
import os
import signal
import threading
from pathlib import Path

from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from src.config import ADMIN_ALLOWED_EMAIL, ADMIN_HOST, CF_ACCESS_AUD, CF_ACCESS_TEAM_DOMAIN
from src.db.models import (
    admin_add_user,
    admin_get_user,
    admin_list_user_reminders,
    admin_list_users,
    admin_message_stats,
    admin_message_totals,
    admin_set_user_active,
    deactivate_reminder,
    list_admin_audit_log,
    log_admin_action,
)
from src.scheduler import format_schedule_description
from src.timing_stats import parse_timing_stats

router = APIRouter(prefix="/admin")

_LOG_PATH = Path(__file__).resolve().parent.parent / "logs" / "uvicorn.log"


_jwks_client = None


def _cf_access_email(token: str) -> str | None:
    """Verifies a Cloudflare Access JWT (signature, audience, issuer, expiry) and returns
    its email claim, or None if anything is wrong."""
    global _jwks_client
    import jwt

    try:
        if _jwks_client is None:
            _jwks_client = jwt.PyJWKClient(f"https://{CF_ACCESS_TEAM_DOMAIN}/cdn-cgi/access/certs", cache_keys=True)
        key = _jwks_client.get_signing_key_from_jwt(token).key
        claims = jwt.decode(
            token, key, algorithms=["RS256"], audience=CF_ACCESS_AUD,
            issuer=f"https://{CF_ACCESS_TEAM_DOMAIN}",
        )
    except Exception:
        return None
    email = claims.get("email")
    return email.lower() if isinstance(email, str) else None


def _authorized(request: Request) -> bool:
    """
    Correct Host, plus an identity from Cloudflare Access matching ADMIN_ALLOWED_EMAIL.

    Fails closed: with no ADMIN_ALLOWED_EMAIL the dashboard is simply off. The plain
    Cf-Access-Authenticated-User-Email header is only trustworthy when every request to
    ADMIN_HOST really passes through Cloudflare Access (it can be forged by anyone who
    reaches the app another way), so when CF_ACCESS_TEAM_DOMAIN and CF_ACCESS_AUD are set
    the signed Cf-Access-Jwt-Assertion token is verified instead and the header is ignored.
    """
    host = request.headers.get("host", "").split(":")[0].lower()
    if not ADMIN_HOST or host != ADMIN_HOST.lower():
        return False
    if not ADMIN_ALLOWED_EMAIL:
        return False

    if CF_ACCESS_TEAM_DOMAIN and CF_ACCESS_AUD:
        token = request.headers.get("cf-access-jwt-assertion", "")
        access_email = _cf_access_email(token) if token else None
    else:
        access_email = request.headers.get("cf-access-authenticated-user-email", "").lower() or None
    if not access_email or access_email != ADMIN_ALLOWED_EMAIL.lower():
        return False
    request.state.admin_email = access_email
    return True


def _deny() -> HTMLResponse:
    return HTMLResponse("<h3>403</h3>", status_code=403)


def _admin_email(request: Request) -> str:
    """The identity _authorized() verified for this request (only called after it passed)."""
    return getattr(request.state, "admin_email", "") or request.headers.get("cf-access-authenticated-user-email", "").lower()


_PAGE_TEMPLATE = """<!DOCTYPE html>
<html dir="rtl" lang="he">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>ניהול העוזר האישי</title>
<style>
  :root {{ --bg:#0f172a; --card:#1e293b; --text:#e2e8f0; --muted:#94a3b8; --accent:#38bdf8; --ok:#4ade80; --bad:#f87171; }}
  * {{ box-sizing: border-box; }}
  body {{ margin:0; font-family: -apple-system, "Segoe UI", Arial, sans-serif; background:var(--bg); color:var(--text); }}
  header {{ padding:16px 24px; background:var(--card); display:flex; gap:20px; align-items:center; flex-wrap:wrap; }}
  header h1 {{ font-size:18px; margin:0; }}
  nav a {{ color:var(--accent); text-decoration:none; margin-inline-start:16px; }}
  main {{ padding:24px; max-width:1000px; margin:0 auto; }}
  .card {{ background:var(--card); border-radius:12px; padding:20px; margin-bottom:20px; }}
  table {{ width:100%; border-collapse:collapse; }}
  th, td {{ text-align:right; padding:8px 10px; border-bottom:1px solid #334155; font-size:14px; }}
  th {{ color:var(--muted); font-weight:600; }}
  .ok {{ color:var(--ok); }} .bad {{ color:var(--bad); }}
  input, button {{ padding:8px 12px; border-radius:8px; border:1px solid #334155; background:#0f172a; color:var(--text); font-size:14px; }}
  button {{ cursor:pointer; background:var(--accent); color:#0f172a; border:none; font-weight:600; }}
  button.danger {{ background:var(--bad); }}
  button.subtle {{ background:#334155; color:var(--text); }}
  form.inline {{ display:inline; }}
  pre {{ background:#0f172a; padding:12px; border-radius:8px; overflow-x:auto; font-size:12px; direction:ltr; text-align:left; max-height:500px; overflow-y:auto; }}
  .stat-grid {{ display:grid; grid-template-columns:repeat(auto-fit,minmax(150px,1fr)); gap:12px; }}
  .stat {{ background:#0f172a; border-radius:8px; padding:14px; text-align:center; }}
  .stat .num {{ font-size:26px; font-weight:700; color:var(--accent); }}
  .stat .label {{ font-size:12px; color:var(--muted); margin-top:4px; }}
  .bar-row {{ display:flex; align-items:center; gap:10px; margin-bottom:8px; font-size:13px; }}
  .bar-label {{ width:110px; color:var(--muted); flex-shrink:0; }}
  .bar-track {{ flex:1; background:#0f172a; border-radius:4px; height:22px; position:relative; overflow:hidden; }}
  .bar-fill {{ background:var(--accent); height:100%; border-radius:4px; }}
  .bar-value {{ width:110px; text-align:left; direction:ltr; flex-shrink:0; color:var(--muted); }}
  .msg {{ padding:10px 14px; border-radius:8px; margin-bottom:16px; }}
  .msg.ok {{ background:#14532d; }} .msg.err {{ background:#7f1d1d; }}
</style>
</head>
<body>
<header>
  <h1>🤖 ניהול העוזר האישי</h1>
  <nav>
    <a href="/admin/">משתמשים</a>
    <a href="/admin/stats">סטטיסטיקות</a>
    <a href="/admin/logs">לוגים</a>
    <a href="/admin/system">מערכת</a>
    <a href="/admin/audit">לוג ביקורת</a>
  </nav>
</header>
<main>
{content}
</main>
</body>
</html>"""


def _esc(v) -> str:
    return html_lib.escape(str(v if v is not None else ""))


@router.get("/", response_class=HTMLResponse)
async def users_page(request: Request, msg: str = "", err: str = ""):
    if not _authorized(request):
        return _deny()

    users = admin_list_users()
    rows = ""
    for u in users:
        status = '<span class="ok">פעיל</span>' if u["is_active"] else '<span class="bad">מושבת</span>'
        toggle_label = "השבת" if u["is_active"] else "הפעל"
        toggle_value = "0" if u["is_active"] else "1"
        toggle_class = "danger" if u["is_active"] else "subtle"
        last_seen = _esc(u["last_message_at"] or "—")
        rows += f"""<tr>
            <td>{u['id']}</td>
            <td>{_esc(u['display_name'])}</td>
            <td dir="ltr">{_esc(u['whatsapp_number'])}</td>
            <td>{status}</td>
            <td>{u['message_count']}</td>
            <td>{u['active_reminders']}</td>
            <td dir="ltr">{last_seen}</td>
            <td><a style="color:var(--accent)" href="/admin/users/{u['id']}/reminders">תזכורות</a></td>
            <td>
              <form class="inline" method="post" action="/admin/users/toggle">
                <input type="hidden" name="user_id" value="{u['id']}">
                <input type="hidden" name="is_active" value="{toggle_value}">
                <button class="{toggle_class}" type="submit">{toggle_label}</button>
              </form>
            </td>
        </tr>"""

    notice = ""
    if msg:
        notice = f'<div class="msg ok">{_esc(msg)}</div>'
    elif err:
        notice = f'<div class="msg err">{_esc(err)}</div>'

    content = f"""
{notice}
<div class="card">
  <h2>משתמשים ({len(users)})</h2>
  <table>
    <tr><th>#</th><th>שם</th><th>מספר</th><th>סטטוס</th><th>הודעות</th><th>תזכורות פעילות</th><th>הודעה אחרונה</th><th></th><th></th></tr>
    {rows}
  </table>
</div>
<div class="card">
  <h2>הוספת משתמש</h2>
  <form method="post" action="/admin/users/add">
    <input name="display_name" placeholder="שם (באנגלית)" required>
    <input name="whatsapp_number" placeholder="9725XXXXXXXX" pattern="[0-9+\\-() ]{{9,20}}" required dir="ltr">
    <button type="submit">הוסף</button>
  </form>
  <p style="color:var(--muted);font-size:13px">פורמט בינלאומי, ספרות בלבד, בלי +. המשתמש יוכל לכתוב לבוט מיד אחרי ההוספה.</p>
</div>"""
    return HTMLResponse(_PAGE_TEMPLATE.format(content=content))


@router.post("/users/add")
async def add_user(request: Request, display_name: str = Form(...), whatsapp_number: str = Form(...)):
    if not _authorized(request):
        return _deny()

    # Same normalisation as in chat: a local number (05X) must become
    # international, otherwise it will never match what WhatsApp sends in the
    # webhook.
    digits = "".join(ch for ch in whatsapp_number if ch.isdigit())
    if digits.startswith("00"):
        digits = digits[2:]
    if digits.startswith("0"):
        digits = "972" + digits[1:]
    number = digits
    if not (11 <= len(number) <= 15):
        return RedirectResponse("/admin/?err=מספר לא תקין", status_code=303)

    ok = admin_add_user(number, display_name.strip())
    if not ok:
        return RedirectResponse("/admin/?err=המספר כבר קיים", status_code=303)
    log_admin_action(_admin_email(request), "add_user", details=f"whatsapp_number={number}, display_name={display_name.strip()}")
    return RedirectResponse("/admin/?msg=המשתמש נוסף", status_code=303)


@router.post("/users/toggle")
async def toggle_user(request: Request, user_id: int = Form(...), is_active: int = Form(...)):
    if not _authorized(request):
        return _deny()

    admin_set_user_active(user_id, bool(is_active))
    log_admin_action(
        _admin_email(request), "toggle_user_active", target_user_id=user_id,
        details=f"is_active={bool(is_active)}",
    )
    return RedirectResponse("/admin/?msg=עודכן", status_code=303)


def _bar(label: str, value: float, max_value: float, display: str) -> str:
    """A single bar-chart row. Pure CSS charting - no external libraries in the dashboard."""
    pct = (value / max_value * 100) if max_value > 0 else 0
    return (
        f'<div class="bar-row">'
        f'<div class="bar-label">{_esc(label)}</div>'
        f'<div class="bar-track"><div class="bar-fill" style="width:{pct:.1f}%"></div></div>'
        f'<div class="bar-value">{_esc(display)}</div>'
        f"</div>"
    )


@router.get("/stats", response_class=HTMLResponse)
async def stats_page(request: Request):
    if not _authorized(request):
        return _deny()

    rows = admin_message_stats(days=7)
    by_day: dict[str, dict[str, int]] = {}
    for r in rows:
        by_day.setdefault(r["day"], {"incoming": 0, "outgoing": 0})[r["direction"]] = r["cnt"]

    total_in = sum(d["incoming"] for d in by_day.values())
    total_out = sum(d["outgoing"] for d in by_day.values())

    # Messages-per-day chart
    max_day = max((d["incoming"] + d["outgoing"] for d in by_day.values()), default=0)
    day_bars = ""
    for day in sorted(by_day.keys(), reverse=True):
        d = by_day[day]
        total_day = d["incoming"] + d["outgoing"]
        day_bars += _bar(day, total_day, max_day, f"{total_day} ({d['incoming']}↓ {d['outgoing']}↑)")

    # Intent breakdown and response times, parsed from the [timing] log lines
    timing = parse_timing_stats(_LOG_PATH)
    intent_bars = ""
    if timing["by_intent"]:
        max_intent = max(v["count"] for v in timing["by_intent"].values())
        for intent, v in timing["by_intent"].items():
            intent_bars += _bar(intent, v["count"], max_intent, f"{v['count']} · {v['avg_total']:.2f}s")
    else:
        intent_bars = '<p style="color:var(--muted)">אין עדיין שורות timing בלוג.</p>'

    # Gemini cost estimate. Every inbound message means at least one Gemini call.
    totals = admin_message_totals()
    gemini_calls = totals.get("incoming", 0)
    # Rough estimate: ~1500 input tokens (prompt + history) and ~150 output
    # tokens per call, at Gemini Flash prices at time of writing (USD per
    # million tokens).
    est_cost = gemini_calls * (1500 / 1_000_000 * 0.30 + 150 / 1_000_000 * 2.50)

    latency_html = ""
    if timing["total_requests"]:
        latency_html = f'''
  <div class="stat-grid">
    <div class="stat"><div class="num">{timing["avg_total"]:.2f}s</div><div class="label">זמן תגובה ממוצע</div></div>
    <div class="stat"><div class="num">{timing["slowest"]:.2f}s</div><div class="label">האיטי ביותר</div></div>
    <div class="stat"><div class="num">{timing["by_type"].get("text", 0)}</div><div class="label">טקסט</div></div>
    <div class="stat"><div class="num">{timing["by_type"].get("audio", 0)}</div><div class="label">קוליות</div></div>
  </div>'''

    content = f"""
<div class="card">
  <h2>7 הימים האחרונים</h2>
  <div class="stat-grid">
    <div class="stat"><div class="num">{total_in}</div><div class="label">הודעות נכנסות</div></div>
    <div class="stat"><div class="num">{total_out}</div><div class="label">הודעות יוצאות</div></div>
  </div>
  <div style="margin-top:18px">{day_bars or '<p style="color:var(--muted)">אין נתונים עדיין</p>'}</div>
</div>

<div class="card">
  <h2>ביצועים ופילוח בקשות</h2>
  <p style="color:var(--muted);font-size:13px">מבוסס על שורות ה-timing בלוג הנוכחי (מתאפס עם רוטציית לוג).</p>
  {latency_html}
  <div style="margin-top:18px">{intent_bars}</div>
</div>

<div class="card">
  <h2>עלות Gemini משוערת</h2>
  <div class="stat-grid">
    <div class="stat"><div class="num">{gemini_calls}</div><div class="label">קריאות (סה"כ הודעות נכנסות)</div></div>
    <div class="stat"><div class="num">${est_cost:.2f}</div><div class="label">עלות מצטברת משוערת</div></div>
  </div>
  <p style="color:var(--muted);font-size:12px;margin-top:12px">
    ⚠️ הערכה גסה בלבד — מניחה ~1500 טוקני קלט ו-~150 פלט לקריאה, ולא כוללת הודעות
    קוליות (יקרות יותר) או קריאות נוספות כמו שכתוב טיוטת מייל.
    לחיוב האמיתי יש להסתכל בקונסולת Google Cloud.
  </p>
</div>"""
    return HTMLResponse(_PAGE_TEMPLATE.format(content=content))


@router.get("/logs", response_class=HTMLResponse)
async def logs_page(request: Request, lines: int = 100):
    if not _authorized(request):
        return _deny()

    log_admin_action(_admin_email(request), "view_raw_logs")
    lines = max(10, min(lines, 1000))
    log_text = "(קובץ הלוג לא נמצא)"
    try:
        if _LOG_PATH.exists():
            with open(_LOG_PATH, "r", encoding="utf-8", errors="replace") as f:
                all_lines = f.readlines()
            log_text = "".join(all_lines[-lines:])
    except Exception as e:
        log_text = f"(שגיאה בקריאת הלוג: {e})"

    content = f"""
<div class="card">
  <h2>לוג — {lines} שורות אחרונות</h2>
  <p><a style="color:var(--accent)" href="/admin/logs?lines=100">100</a> ·
     <a style="color:var(--accent)" href="/admin/logs?lines=300">300</a> ·
     <a style="color:var(--accent)" href="/admin/logs?lines=1000">1000</a></p>
  <pre>{_esc(log_text)}</pre>
</div>"""
    return HTMLResponse(_PAGE_TEMPLATE.format(content=content))


@router.get("/audit", response_class=HTMLResponse)
async def audit_page(request: Request):
    """
    New feature (2026-09-26): a real, provable answer to "do you see my
    messages" - every admin action that touches another user's data (or the
    raw log, which could) is listed here, newest first. Viewing this page
    itself is NOT logged (it never touches another user's data, only the
    audit trail of past actions) - logging every audit-log view would just
    add noise without adding any real transparency.
    """
    if not _authorized(request):
        return _deny()

    rows = ""
    for row in list_admin_audit_log(limit=200):
        target = _esc(row["target_display_name"]) if row["target_display_name"] else "—"
        rows += f"""<tr>
            <td dir="ltr">{_esc(row['created_at'])}</td>
            <td>{_esc(row['admin_email'])}</td>
            <td>{_esc(row['action'])}</td>
            <td>{target}</td>
            <td>{_esc(row['details'])}</td>
        </tr>"""

    content = f"""
<div class="card">
  <h2>לוג ביקורת אדמין</h2>
  <p style="color:var(--muted);font-size:13px">כל פעולה שנוגעת בנתונים של משתמש אחר (צפייה בתזכורות שלו, ביטול תזכורת, הוספה/השבתה של משתמש, צפייה בלוג הגולמי) נרשמת כאן - כדי שתהיה תשובה אמיתית ומתועדת לשאלה "האם אתה רואה את מה שאני כותב".</p>
  <table>
    <tr><th>מתי (UTC)</th><th>מי</th><th>פעולה</th><th>על מי</th><th>פרטים</th></tr>
    {rows or "<tr><td colspan='5'>אין עדיין פעולות רשומות</td></tr>"}
  </table>
</div>"""
    return HTMLResponse(_PAGE_TEMPLATE.format(content=content))


@router.get("/users/{user_id}/reminders", response_class=HTMLResponse)
async def user_reminders_page(request: Request, user_id: int, msg: str = ""):
    if not _authorized(request):
        return _deny()

    user = admin_get_user(user_id)
    if user is None:
        return HTMLResponse(_PAGE_TEMPLATE.format(content='<div class="card">משתמש לא נמצא.</div>'))

    log_admin_action(_admin_email(request), "view_user_reminders", target_user_id=user_id)
    reminders = admin_list_user_reminders(user_id)
    rows = ""
    for r in reminders:
        schedule = format_schedule_description(
            r["schedule_type"], r["schedule_time"], r["schedule_days"], user["timezone"]
        )
        recipient = _esc(r["recipient_name"]) if r["recipient_name"] else "—"
        rows += f"""<tr>
            <td>{_esc(r['content'])}</td>
            <td>{_esc(schedule)}</td>
            <td>{recipient}</td>
            <td dir="ltr">{_esc(r['next_trigger_at'])}</td>
            <td>
              <form class="inline" method="post" action="/admin/reminders/cancel">
                <input type="hidden" name="reminder_id" value="{r['id']}">
                <input type="hidden" name="user_id" value="{user_id}">
                <button class="danger" type="submit">בטל</button>
              </form>
            </td>
        </tr>"""

    notice = f'<div class="msg ok">{_esc(msg)}</div>' if msg else ""
    content = f"""
{notice}
<div class="card">
  <h2>תזכורות של {_esc(user['display_name'])}</h2>
  <p><a style="color:var(--accent)" href="/admin/">← חזרה למשתמשים</a></p>
  <table>
    <tr><th>תוכן</th><th>מתי</th><th>נמען</th><th>הפעלה הבאה (UTC)</th><th></th></tr>
    {rows or "<tr><td colspan='5'>אין תזכורות פעילות</td></tr>"}
  </table>
</div>"""
    return HTMLResponse(_PAGE_TEMPLATE.format(content=content))


@router.post("/reminders/cancel")
async def cancel_reminder(request: Request, reminder_id: int = Form(...), user_id: int = Form(...)):
    if not _authorized(request):
        return _deny()

    # Verify ownership rather than trusting the form: even in the admin area,
    # cancelling another user's reminder because of a wrong id in the form is a
    # privacy bug, not merely a glitch.
    cancelled = deactivate_reminder(reminder_id, user_id)
    log_admin_action(
        _admin_email(request), "cancel_reminder", target_user_id=user_id,
        details=f"reminder_id={reminder_id}, cancelled={cancelled}",
    )
    msg = "התזכורת בוטלה" if cancelled else "התזכורת לא נמצאה אצל המשתמש הזה"
    return RedirectResponse(f"/admin/users/{user_id}/reminders?msg={msg}", status_code=303)


@router.get("/system", response_class=HTMLResponse)
async def system_page(request: Request, msg: str = ""):
    if not _authorized(request):
        return _deny()

    log_size = _LOG_PATH.stat().st_size / 1024 if _LOG_PATH.exists() else 0
    notice = f'<div class="msg ok">{_esc(msg)}</div>' if msg else ""

    content = f"""
{notice}
<div class="card">
  <h2>מצב מערכת</h2>
  <div class="stat-grid">
    <div class="stat"><div class="num">{os.getpid()}</div><div class="label">Process ID</div></div>
    <div class="stat"><div class="num">{log_size:.0f} KB</div><div class="label">גודל הלוג</div></div>
  </div>
</div>

<div class="card">
  <h2>הפעלה מחדש</h2>
  <p style="color:var(--muted);font-size:13px">
    ⚠️ <b>שים לב לפני שאתה לוחץ:</b> אין דרך "לאתחל" את השרת מתוך עצמו —
    מה שקורה בפועל הוא שהתהליך <b>נסגר</b>, וסקריפט המעקב
    (start_assistant.ps1) מזהה זאת ומריץ אותו מחדש. המשמעות:
  </p>
  <ul style="color:var(--muted);font-size:13px">
    <li>הבוט <b>לא זמין למשך עד ~30 שניות</b> (מחזור הבדיקה של סקריפט המעקב).</li>
    <li>אם חלון המעקב <b>לא רץ</b> כרגע — הבוט יישאר כבוי עד הפעלה ידנית.</li>
    <li>תזכורות שהיו אמורות לצאת בדיוק בחלון הזה יישלחו באיחור (יש catch-up), לא יאבדו.</li>
  </ul>
  <form method="post" action="/admin/restart" onsubmit="return confirm('להפעיל מחדש? הבוט לא יגיב למשך עד 30 שניות.');">
    <button class="danger" type="submit">הפעל מחדש את השרת</button>
  </form>
</div>"""
    return HTMLResponse(_PAGE_TEMPLATE.format(content=content))


@router.post("/restart")
async def restart_server(request: Request):
    if not _authorized(request):
        return _deny()

    def _shutdown():
        # Delayed exit so the HTTP response reaches the browser before the
        # process dies. The watchdog script detects the shutdown and restarts it.
        os.kill(os.getpid(), signal.SIGTERM)

    threading.Timer(1.0, _shutdown).start()
    return HTMLResponse(
        _PAGE_TEMPLATE.format(
            content="""
<div class="card">
  <h2>השרת נסגר…</h2>
  <p>סקריפט המעקב יפעיל אותו מחדש תוך כ-30 שניות.</p>
  <p style="color:var(--muted);font-size:13px">אם אחרי דקה עדיין אין תגובה — כנראה שסקריפט המעקב לא רץ, ותצטרך להריץ ידנית: <code dir="ltr">Start-ScheduledTask -TaskName "PersonalAssistantWhatsApp"</code></p>
  <p><a style="color:var(--accent)" href="/admin/system">רענן אחרי כמה שניות</a></p>
</div>"""
        )
    )
