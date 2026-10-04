"""Public privacy-policy and terms-of-use pages (English + Hebrew), served by src/main.py.

Kept in their own module so tests can render them without importing the app (which starts the
scheduler). Every factual statement here must match what the code actually does - keep in sync
with docs/PRIVACY_FOR_OPERATORS.md and webhook_handler._PRIVACY_EXPLANATION. If you change what
is sent where, update LEGAL_PAGES_UPDATED too. This is a template, not legal advice.
"""
from html import escape

from src import config
from src.config import ADMIN_CONTACT_EMAIL, OPERATOR_NAME

LEGAL_PAGES_UPDATED = "2026-10-03"

_PAGE_HEAD = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>{title}</title>
<meta name="viewport" content="width=device-width, initial-scale=1">
<style>body{{font-family:sans-serif;max-width:720px;margin:40px auto;padding:0 16px;line-height:1.6}}
section[dir=rtl]{{border-top:1px solid #ccc;margin-top:40px;padding-top:8px}}
small{{color:#666}}</style>
</head>
<body>
<p><a href="#en">English</a> · <a href="#he">עברית</a></p>
"""


def _contact() -> str:
    if not ADMIN_CONTACT_EMAIL:
        return "(set ADMIN_CONTACT_EMAIL in .env)"
    email = escape(ADMIN_CONTACT_EMAIL)
    return f'<a href="mailto:{email}">{email}</a>'


def _operator(lang: str) -> str:
    if OPERATOR_NAME:
        return escape(OPERATOR_NAME)
    return "(set OPERATOR_NAME in .env)" if lang == "en" else "(יש להגדיר OPERATOR_NAME בקובץ ‎.env)"


def _openai_enabled() -> bool:
    """The OpenAI paragraph is shown only on installs where the operator has actually turned OpenAI on."""
    return bool(config.OPENAI_API_KEY and config.OPENAI_MODEL)


def privacy_html() -> str:
    """English + Hebrew on one page (Google's consent screen takes a single URL)."""
    contact = _contact()
    return _PAGE_HEAD.format(title="Privacy Policy - Personal WhatsApp Assistant") + f"""
<section id="en" dir="ltr">
<h1>Privacy Policy</h1>
<p><small>Last updated: {LEGAL_PAGES_UPDATED}</small></p>
<p>This WhatsApp assistant is a private, invitation-only service. It is run by
<b>{_operator("en")}</b> (the "operator"), who decides how your information is used and is
responsible for it. Contact: {contact}. It is not a commercial or public service.</p>

<h2>What we collect, and why</h2>
<ul>
<li><b>What you send:</b> messages, voice notes, photos and PDFs, and facts or contacts you ask the
assistant to remember - to understand and answer you.</li>
<li><b>If you connect Google:</b> access to your Calendar, Gmail and Drive, used only for what you ask
(for example "what's on my calendar", "summarize my latest emails", "save this to Drive") and, if you turn
it on, for proactive alerts.</li>
<li><b>Technical data:</b> your WhatsApp number and name, message times, and usage counts needed to run
and pay for the service.</li>
</ul>
<p>You do not have to provide any of this; without it, the related feature simply will not work.</p>

<h2>Who receives it</h2>
<ul>
<li><b>Google (Gemini API)</b> processes your messages, the recent conversation, your saved contact names
and facts, attachments you send, and - only when you ask, or in the background if you enable proactive
mode (off by default) - email and calendar content. Web searches also go through Google Search.</li>
{('<li><b>OpenAI</b>, if you choose it (say "switch to OpenAI"; it is off by default for you), receives the same kinds of content '
'instead of Gemini for your conversations and your proactive alerts. Requests are sent with <code>store=false</code>, which is a request not to '
'keep them, not a guarantee. Memory search and image generation always use Gemini. You can switch back at any time.</li>') if _openai_enabled() else ""}
<li><b>Meta (WhatsApp Cloud API)</b> carries every message between you and the assistant.</li>
<li><b>Other providers</b> receive only the minimum for a lookup you request: Ship24 (tracking numbers),
Open-Meteo (city names, for weather), Yahoo Finance (ticker symbols).</li>
</ul>
<p>These providers process data under their own terms and privacy policies, and their servers may be
outside your country (for example in the United States). The operator chooses the Gemini plan; on some
Google plans Google may use submitted content to improve its services - ask the operator which plan is used.
Your information is not sold, not used for advertising, and not shared with anyone else, unless the law requires it.</p>

<h2>Google user data</h2>
<p>Information received from Google APIs is used only to provide the features you asked for, is not used
for advertising, is not sold, and is not read by people except with your permission, for security, or to
comply with the law. The use of information received from Google APIs adheres to the
<a href="https://developers.google.com/terms/api-services-user-data-policy">Google API Services User Data Policy</a>,
including the Limited Use requirements. You can revoke access at any time at
<a href="https://myaccount.google.com/permissions">myaccount.google.com/permissions</a>.</p>

<h2>Storage, security and who can see it</h2>
<ul>
<li>Data is stored in a database on the operator's own server. Google access tokens are encrypted; the rest
of the database is not encrypted at rest.</li>
<li>Other users of the assistant cannot see your data. The admin dashboard, as shipped, shows only counts and
active reminders, never message or email content, and every admin view is logged.</li>
<li><b>The operator has access to the server and can technically read the stored data.</b> Use this assistant
only if you trust the operator.</li>
<li>Reasonable measures are taken to protect the data, but no system is completely secure. If a security
incident affects your data, the operator will tell you.</li>
</ul>

<h2>How long it is kept</h2>
<p>Your conversation history, contacts and remembered facts are kept until you delete them or stop using the
assistant and ask for deletion. Background bookkeeping for proactive alerts is deleted automatically after 60
days. Backups may keep a copy for a limited time after deletion.</p>

<h2>Your rights</h2>
<ul>
<li>See what is stored about you: ask the assistant "show me what you have on me" (a summary), or ask the
operator for a full copy.</li>
<li>Correct it: ask the assistant to update or forget a remembered fact, or contact the operator.</li>
<li>Delete it: ask the assistant "delete my history", or contact the operator to remove everything.</li>
<li>Stop proactive alerts or disconnect Google at any time.</li>
<li>If you believe your privacy was violated, you may complain to your data-protection authority
(in Israel: the Privacy Protection Authority).</li>
</ul>

<h2>Other people's information</h2>
<p>If you save a contact or ask the assistant to message someone, you share that person's name and number with
the assistant. Only do this for people who would expect it. Contacts who receive a reminder can ask the
operator to stop messages and delete their number.</p>

<h2>Children</h2>
<p>The assistant may be used by children in the operator's family only with a parent's or guardian's consent;
the parent or guardian may exercise the rights above on the child's behalf.</p>

<h2>Changes</h2>
<p>If this policy changes in a material way, users will be told through the assistant before the change applies.</p>
<p>Questions or requests: {contact} · <a href="/terms">Terms of use</a></p>
</section>

<section id="he" dir="rtl" lang="he">
<h1>מדיניות פרטיות</h1>
<p><small>עודכן לאחרונה: {LEGAL_PAGES_UPDATED}</small></p>
<p>עוזר הוואטסאפ הזה הוא שירות פרטי בהזמנה בלבד. הוא מופעל על ידי <b>{_operator("he")}</b>
("המפעיל"), שמחליט כיצד המידע שלכם משמש ואחראי לו. ליצירת קשר: {contact}. זה אינו שירות מסחרי או ציבורי.</p>

<h2>איזה מידע נאסף ולמה</h2>
<ul>
<li><b>מה שאתם שולחים:</b> הודעות, הודעות קוליות, תמונות וקובצי PDF, ועובדות או אנשי קשר שביקשתם
מהעוזר לזכור - כדי להבין אתכם ולענות.</li>
<li><b>אם חיברתם את Google:</b> גישה ליומן, ל-Gmail ולדרייב, לשימוש רק במה שביקשתם (למשל "מה יש לי
ביומן", "תסכם לי את המיילים האחרונים", "תשמור את זה בדרייב"), ואם הפעלתם - גם להתראות יזומות.</li>
<li><b>מידע טכני:</b> מספר הוואטסאפ והשם שלכם, זמני הודעות ונתוני שימוש הדרושים להפעלת השירות ולתשלום עליו.</li>
</ul>
<p>אין חובה למסור את המידע הזה; בלעדיו, היכולת הקשורה בו פשוט לא תעבוד.</p>

<h2>מי מקבל את המידע</h2>
<ul>
<li><b>Google (Gemini API)</b> מעבדת את ההודעות שלכם, את השיחה האחרונה, שמות אנשי קשר ועובדות
שמורים, קבצים שצירפתם, ו - רק כשאתם מבקשים, או ברקע אם הפעלתם מצב יזום (כבוי כברירת מחדל) - תוכן
של מיילים ויומן. חיפושים ברשת עוברים גם דרך Google Search.</li>
{('<li><b>OpenAI</b>, אם תבחרו בו (כותבים "עבור ל-OpenAI"; כברירת מחדל הוא כבוי אצלכם), מקבל את אותם סוגי תוכן במקום Gemini '
'עבור השיחות שלכם וההתראות היזומות. הבקשות נשלחות עם <code>store=false</code>, שהוא בקשה לא לשמור אותן ולא הבטחה. '
'חיפוש בזיכרון ויצירת תמונות תמיד משתמשים ב-Gemini. אפשר לחזור ל-Gemini בכל עת.</li>') if _openai_enabled() else ""}
<li><b>מטא (WhatsApp Cloud API)</b> מעבירה כל הודעה בינכם לבין העוזר.</li>
<li><b>ספקים נוספים</b> מקבלים רק את המינימום לבדיקה שביקשתם: Ship24 (מספרי מעקב), Open-Meteo
(שמות ערים, למזג אוויר), Yahoo Finance (סימולי מניות).</li>
</ul>
<p>הספקים האלה מעבדים מידע לפי התנאים ומדיניות הפרטיות שלהם, והשרתים שלהם עשויים להימצא מחוץ לישראל
(למשל בארצות הברית). המפעיל בוחר את תוכנית ה-Gemini; בחלק מהתוכניות של Google היא רשאית להשתמש בתוכן
שנשלח כדי לשפר את שירותיה - אפשר לשאול את המפעיל באיזו תוכנית משתמשים. המידע שלכם לא נמכר, לא משמש
לפרסום ולא מועבר לאף אחד אחר, אלא אם החוק מחייב זאת.</p>

<h2>מידע ממשתמשי Google</h2>
<p>מידע שמתקבל מממשקי Google משמש רק לספק את היכולות שביקשתם, לא משמש לפרסום, לא נמכר ולא נקרא על
ידי בני אדם, אלא בהסכמתכם, לצורכי אבטחה או לפי דרישת החוק. השימוש במידע שמתקבל מממשקי Google עומד
ב<a href="https://developers.google.com/terms/api-services-user-data-policy">מדיניות נתוני המשתמש של Google API Services</a>,
כולל דרישות השימוש המוגבל (Limited Use). אפשר לבטל את הגישה בכל עת בכתובת
<a href="https://myaccount.google.com/permissions">myaccount.google.com/permissions</a>.</p>

<h2>אחסון, אבטחה ומי יכול לראות</h2>
<ul>
<li>המידע נשמר בבסיס נתונים בשרת של המפעיל. אסימוני הגישה של Google מוצפנים; שאר בסיס הנתונים אינו מוצפן במנוחה.</li>
<li>משתמשים אחרים של העוזר לא רואים את המידע שלכם. לוח הניהול, כפי שהוא מגיע בתוכנה, מציג רק ספירות
ותזכורות פעילות, לעולם לא תוכן של הודעות או מיילים, וכל צפייה של מנהל נרשמת.</li>
<li><b>למפעיל יש גישה לשרת, ולכן הוא יכול טכנית לקרוא את המידע השמור.</b> השתמשו בעוזר רק אם אתם סומכים על המפעיל.</li>
<li>ננקטים אמצעים סבירים להגנה על המידע, אבל אף מערכת אינה מאובטחת לחלוטין. אם אירוע אבטחה יפגע
במידע שלכם, המפעיל יודיע לכם.</li>
</ul>

<h2>כמה זמן המידע נשמר</h2>
<p>היסטוריית השיחות, אנשי הקשר והעובדות השמורות נשמרים עד שתמחקו אותם, או עד שתפסיקו להשתמש בעוזר
ותבקשו מחיקה. רישומי הרקע של ההתראות היזומות נמחקים אוטומטית אחרי 60 יום. גיבויים עשויים לשמור עותק
לזמן מוגבל לאחר המחיקה.</p>

<h2>הזכויות שלכם</h2>
<ul>
<li>לעיין במידע השמור עליכם: לבקש מהעוזר "תראה לי מה יש לך עליי" (סיכום), או לבקש מהמפעיל עותק מלא.</li>
<li>לתקן אותו: לבקש מהעוזר לעדכן או לשכוח עובדה שמורה, או לפנות למפעיל.</li>
<li>למחוק אותו: לבקש מהעוזר "תמחק את ההיסטוריה שלי", או לפנות למפעיל כדי למחוק הכל.</li>
<li>להפסיק התראות יזומות או לנתק את Google בכל עת.</li>
<li>אם אתם סבורים שפרטיותכם נפגעה, אפשר להגיש תלונה לרשות להגנת הפרטיות.</li>
</ul>

<h2>מידע על אנשים אחרים</h2>
<p>כששומרים איש קשר או מבקשים מהעוזר לשלוח הודעה למישהו, משתפים עם העוזר את השם והמספר של אותו אדם.
עשו זאת רק לגבי אנשים שהיו מצפים לכך. אנשי קשר שמקבלים תזכורת יכולים לבקש מהמפעיל להפסיק את ההודעות
ולמחוק את המספר שלהם.</p>

<h2>ילדים</h2>
<p>ילדים במשפחת המפעיל רשאים להשתמש בעוזר רק בהסכמת הורה או אפוטרופוס, וההורה או האפוטרופוס רשאים
לממש את הזכויות שלמעלה בשם הילד.</p>

<h2>שינויים</h2>
<p>אם המדיניות תשתנה באופן מהותי, המשתמשים יקבלו הודעה דרך העוזר לפני שהשינוי ייכנס לתוקף.</p>
<p>שאלות או בקשות: {contact} · <a href="/terms">תנאי שימוש</a></p>
</section>
</body>
</html>"""


def terms_html() -> str:
    contact = _contact()
    return _PAGE_HEAD.format(title="Terms of Use - Personal WhatsApp Assistant") + f"""
<section id="en" dir="ltr">
<h1>Terms of Use</h1>
<p><small>Last updated: {LEGAL_PAGES_UPDATED}</small></p>
<p>This assistant is a private, free, invitation-only service run by <b>{_operator("en")}</b>
(the "operator"). By using it you agree to these terms.</p>
<ol>
<li><b>Personal use.</b> Use it for your own personal or family needs. Do not use it for anything unlawful,
to harass anyone, to send messages people did not agree to receive, or to try to break or misuse it.
You must also follow WhatsApp's and Google's own terms.</li>
<li><b>AI can be wrong.</b> Answers, summaries, search results, reminders and drafts are produced by an AI model
and may be incomplete, outdated or wrong. Check anything important - dates, times, amounts, addresses,
recipients - before relying on it. Nothing the assistant says is medical, legal, financial or other
professional advice.</li>
<li><b>You approve actions.</b> The assistant asks for your confirmation before sending an email or creating a
calendar event. Read what you approve; you are responsible for it.</li>
<li><b>Not for emergencies.</b> Reminders and alerts may be late or not delivered at all (for example if a
server, WhatsApp or Google is unavailable). Do not rely on the assistant for emergencies, medication, or
anything where a missed message could cause harm.</li>
<li><b>No guarantee.</b> The service is provided free of charge, "as is" and "as available", without any warranty.
To the extent permitted by law, the operator is not liable for indirect or consequential damage, or for loss
arising from missed, delayed or incorrect messages, or from actions you approved.</li>
<li><b>Access.</b> The operator may change, suspend or end the service, or remove your access, at any time. You may
stop using it at any time and ask for your data to be deleted.</li>
<li><b>Privacy.</b> How your information is handled is described in the <a href="/privacy">Privacy Policy</a>,
which is part of these terms.</li>
<li><b>Changes.</b> If these terms change in a material way, you will be told through the assistant before the
change applies. If you keep using the assistant after that, the updated terms apply.</li>
</ol>
<p>Questions: {contact}</p>
</section>

<section id="he" dir="rtl" lang="he">
<h1>תנאי שימוש</h1>
<p><small>עודכן לאחרונה: {LEGAL_PAGES_UPDATED}</small></p>
<p>העוזר הוא שירות פרטי, חינמי ובהזמנה בלבד, שמופעל על ידי <b>{_operator("he")}</b> ("המפעיל").
השימוש בו מהווה הסכמה לתנאים האלה.</p>
<ol>
<li><b>שימוש אישי.</b> השתמשו בו לצרכים האישיים או המשפחתיים שלכם. אין להשתמש בו לשום דבר בלתי חוקי,
להטרדה, לשליחת הודעות למי שלא הסכים לקבל אותן, או לניסיון לשבש אותו או לנצל אותו לרעה. יש לפעול
גם לפי תנאי השימוש של וואטסאפ ושל Google.</li>
<li><b>בינה מלאכותית יכולה לטעות.</b> תשובות, סיכומים, תוצאות חיפוש, תזכורות וטיוטות נוצרים על ידי מודל
בינה מלאכותית ועלולים להיות חלקיים, לא עדכניים או שגויים. בדקו כל דבר חשוב - תאריכים, שעות, סכומים,
כתובות, נמענים - לפני שאתם סומכים עליו. שום דבר שהעוזר אומר אינו ייעוץ רפואי, משפטי, פיננסי או מקצועי אחר.</li>
<li><b>אתם מאשרים את הפעולות.</b> העוזר מבקש את אישורכם לפני שליחת מייל או יצירת אירוע ביומן. קראו את
מה שאתם מאשרים; האחריות עליו היא שלכם.</li>
<li><b>לא למקרי חירום.</b> תזכורות והתראות עלולות להתעכב או לא להגיע כלל (למשל אם השרת, וואטסאפ או Google
אינם זמינים). אל תסתמכו על העוזר במקרי חירום, לתרופות, או בכל מצב שבו הודעה שלא הגיעה עלולה לגרום נזק.</li>
<li><b>ללא אחריות.</b> השירות ניתן ללא תשלום, "כמות שהוא" ("as is") ו"לפי זמינות", ללא כל התחייבות. במידה
שהחוק מתיר, המפעיל אינו אחראי לנזק עקיף או תוצאתי, או להפסד שנגרם מהודעות שלא הגיעו, שהתעכבו או
שהיו שגויות, או מפעולות שאישרתם.</li>
<li><b>גישה.</b> המפעיל רשאי לשנות, להשעות או להפסיק את השירות, או להסיר את הגישה שלכם, בכל עת. אתם רשאים
להפסיק להשתמש בכל עת ולבקש מחיקת המידע שלכם.</li>
<li><b>פרטיות.</b> הטיפול במידע שלכם מתואר ב<a href="/privacy">מדיניות הפרטיות</a>, שהיא חלק מהתנאים האלה.</li>
<li><b>שינויים.</b> אם התנאים ישתנו באופן מהותי, תקבלו הודעה דרך העוזר לפני שהשינוי ייכנס לתוקף. אם תמשיכו
להשתמש בעוזר אחרי כן, התנאים המעודכנים יחולו.</li>
</ol>
<p>שאלות: {contact}</p>
</section>
</body>
</html>"""
