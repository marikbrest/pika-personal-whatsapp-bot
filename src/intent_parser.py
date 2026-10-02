"""
Intent detection from a text or voice message, via Gemini.
PRD.md section 5 - message flow.

Returns a dict matching one of:
  {"intent": "reminder", "reminder": {..., "recipient_name": str|None}, "reply": "..."}
  {"intent": "chat", "reply": "<free-form reply>", "feature_request_offer": "<short English description of an unsupported capability the user asked for, only when the reply itself offers to notify the developer - see the chat schema block below> | None"}
  {"intent": "add_contact", "contact": {"name": str, "whatsapp_number": str}, "reply": "..."}
  {"intent": "connect_google", "reply": "..."}  <- request to connect Gmail/Calendar/Drive; the link itself is added by the code, not by Gemini
  {"intent": "calendar", "calendar": {"action": "query"|"create"|"update", ...}, "reply": "..."}  <- reply here is a placeholder only. The real answer is built in code after actually querying/writing the calendar; Gemini cannot see the calendar at classification time
  {"intent": "weather", "weather": {"location": str|None, "day_offset": int, "is_range": bool, "range_days": int|None}, "reply": "..."}  <- reply here is also a placeholder, for the same reason: Gemini does not know the real weather
  {"intent": "market", "market": {"symbol": str}, "reply": "..."}  <- again a placeholder; Gemini does not know real-time prices
  {"intent": "email_read", "email_read": {"query": str|None, "max_results": int}, "reply": "..."}  <- placeholder reply; the actual emails are fetched in code
  {"intent": "email_draft", "email_draft": {"to": str, "subject": str, "body": str}, "reply": "..."}  <- the bot proposes wording, it does NOT send. Here the reply IS the real content (the proposed draft), not a placeholder
  {"intent": "email_action", "email_action": {"action": "send"|"cancel"|"edit", "edit_instructions": str|None}, "reply": "..."}  <- only when a draft is pending (see the context block below)
  {"intent": "reminder_manage", "reminder_manage": {"action": "list"|"cancel"|"reschedule"|"snooze"|"edit_content", ...}, "reply": "..."}  <- placeholder reply; the real content is built in code
  {"intent": "user_manage", "user_manage": {"action": "list"|"add"|"disable", "whatsapp_number": str|None, "display_name": str|None}, "reply": "..."}  <- manages bot access permissions. Placeholder reply; the code checks admin rights and performs the action
  {"intent": "morning_brief", "reply": "..."}  <- an on-demand summary of calendar, weather and unread email. Placeholder reply; the code assembles it
  {"intent": "zabbix_status", "reply": "..."}  <- admin-only home-infra monitoring status (Zabbix). Placeholder reply; the code queries Zabbix for real
  {"intent": "saved_link", "saved_link": {"action": "save"|"list"|"forget", "url": str|None, "match": str|None}, "reply": "..."}  <- permanent copy of a shared URL, only when explicitly requested (never automatic, same principle as memory)
  {"intent": "semantic_search", "semantic_search": {"query": str}, "reply": "..."}  <- searches past conversation + saved links by meaning, not keyword. Placeholder reply; the code embeds the query and searches for real
  {"intent": "package_status", "package_status": {"tracking_number": str|None, "description": str|None}, "reply": "..."}  <- "where's my package", or the user directly gives a tracking number to add. Scans Gmail too either way. Placeholder reply; the code does the real lookup
  {"intent": "usage_status", "reply": "..."}  <- admin-only API usage/cost report (Gemini tokens, Ship24 quota). Placeholder reply; the code reads real logged usage
  {"intent": "memory", "memory": {"action": "save"|"list"|"forget"|"forget_all", "fact_key": str|None, "fact_value": str|None}, "reply": "..."}  <- explicit long-term memory. Placeholder reply; the code performs the action
  {"intent": "task_manage", "task_manage": {"action": "add"|"list"|"done"|"delete"|"clear", "content": str|None, "match": str|None, "list_name": str|None}, "reply": "..."}  <- shopping / to-do lists. Placeholder reply; the code performs the action
  {"intent": "drive", "drive": {"action": "search"|"save_note", "query": str|None, "filename": str|None, "content": str|None}, "reply": "..."}  <- Google Drive: search existing files, or save new text as a file. Placeholder reply; the code performs the real Drive call
  {"intent": "web_search", "web_search": {"query": str}, "reply": "..."}  <- a question needing current/factual info from the real web, not the model's own training knowledge. Placeholder reply; the code performs a real Gemini call with Google Search grounding and returns a real answer with real sources
  {"intent": "email_analyze", "email_analyze": {"action": "summarize"|"unanswered", "query": str|None}, "reply": "..."}  <- deep analysis of Gmail: summarize one thread + extract commitments/deadlines, or list sent emails still waiting for a reply. Placeholder reply; the code fetches the real thread/messages and (for summarize) calls Gemini over the real fetched content
  {"intent": "watch_manage", "watch_manage": {"action": "add"|"list"|"cancel", "watch_type": "email_reply"|"web_page"|None, "query": str|None, "url": str|None, "match": str|None}, "reply": "..."}  <- generic watch/notify-on-change engine: an email reply or a web page. Placeholder reply; the code registers/lists/cancels the real watch
  {"intent": "unclear", "reply": "<fallback>"}  <- when Gemini fails entirely (12.3)

reminder.recipient_name is only populated when the reminder is meant for
someone else and matches an existing contact name supplied in the prompt;
otherwise it is None (an ordinary reminder for the user themselves).

For a voice message the dict also contains "transcript" - the transcription, to
be stored as raw_content in the messages table so the conversation history
stays readable as text rather than raw audio.
"""
from datetime import datetime
from zoneinfo import ZoneInfo

from src.config import DEFAULT_TIMEZONE
from src.integrations.gemini import call_gemini_json, call_gemini_json_with_media

FALLBACK_REPLY = "לא הבנתי, תוכל לנסח אחרת?"

_RESPONSE_SCHEMA = """נתח את ההודעה וסווג אותה לאחת מ-24 קטגוריות, והחזר JSON בפורמט מדויק:

1. אם זו בקשה ליצור תזכורת (חד-פעמית, יומית, או שבועית), בין אם לעצמך ובין אם למישהו אחר:
{{
  "intent": "reminder",
  "reminder": {{
    "content": "<תוכן התזכורת בקצרה>",
    "schedule_type": "once" | "daily" | "weekly",
    "schedule_time": "<לוני-פעמי: 'YYYY-MM-DDTHH:MM:SS' זמן מלא | ליומי/שבועי: 'HH:MM'>",
    "schedule_days": "<רק לשבועי: רשימת ימים מופרדת בפסיקים מתוך mon,tue,wed,thu,fri,sat,sun | אחרת null>",
    "recipient_name": "<שם איש קשר קיים בדיוק כפי שמופיע ברשימת אנשי הקשר למטה, אם התזכורת עבור מישהו אחר ('תזכיר לאמא', 'תגיד לדני'). אחרת null (תזכורת לעצמך)>"
  }},
  "reply": "<אישור קצר וידידותי בעברית שמסביר מה נקבע ולמי>"{transcript_field}
}}

2. אם זו הודעת שיחה כללית, שאלה, או משהו שלא קשור לתזכורת/איש קשר (כולל שאלות על אנשי הקשר השמורים):
{{
  "intent": "chat",
  "reply": "<תשובה טבעית, קצרה, וידידותית בעברית>",
  "feature_request_offer": "<תיאור קצר באנגלית של היכולת המבוקשת - רק אם רלוונטי, ראה בהמשך, אחרת null>"{transcript_field}
}}

אם המשתמש מבקש ממך במפורש לעשות משהו שאין לך כרגע דרך אמיתית לספק (אין כלי/יכולת מתאימה, ואתה לא סתם לא בטוח איך לנסח את הבקשה) - אל תמציא שביצעת את זה. בתשובה (reply) הסבר בקצרה וביושר שזה לא נתמך כרגע, ושאל במפורש: "האם תרצה שאני אשלח למפתח בקשה לעשות את זה?". במקרה כזה בלבד, מלא גם את feature_request_offer בתיאור קצר, ברור וממוקד (באנגלית) של מה שביקשו - הוא יישלח בפועל למפתח אם המשתמש יאשר. אל תשתמש בזה עבור שיחה רגילה, שאלה שאתה כן יודע לענות עליה, או כל בקשה שיש לה כלי מתאים ברשימה למעלה.

חשוב: הבוט כן יודע ליצור תמונה חדשה מתיאור וכן יודע לערוך תמונה שנשלחה אליו לאחרונה - אלה יכולות אמיתיות וקיימות, גם אם אינן מופיעות כקטגוריה נפרדת ברשימה למעלה. אם המשתמש שואל האם אתה יכול לצייר/ליצור/לעצב/לערוך תמונה, או מבקש זאת בלי לתאר מה בדיוק - ענה בחיוב (chat, בלי feature_request_offer) ובקש ממנו לתאר מה לצייר, או לשלוח את התמונה לעריכה אם מדובר בעריכה. לעולם אל תטען שאין לך את היכולת הזאת.

3. אם זו בקשה להוסיף/לעדכן איש קשר (שם + מספר טלפון):
{{
  "intent": "add_contact",
  "contact": {{
    "name": "<שם איש הקשר, מתורגם/מומר לאנגלית (למשל 'אמא' -> 'Mom', 'אבא' -> 'Dad', שם פרטי -> תעתיק לאותיות לטיניות)>",
    "whatsapp_number": "<מספר בפורמט בינלאומי, ספרות בלבד בלי +, למשל 972501234567. אם המשתמש נתן מספר ישראלי מקומי (05X-XXXXXXX) — המר אותו ל-972XXXXXXXXX>"
  }},
  "reply": "<אישור קצר שאיש הקשר נשמר>"{transcript_field}
}}

4. אם זו בקשה לחבר/לאשר גישה ל-Gmail או ליומן Google, בכל ניסוח ובכל שפה ("תחבר לי את הג'ימייל", "תחבר יומן", "אני רוצה שתגש למייל שלי", "connect my mail", "connect gmail", "link my google account", "hook up my calendar"):
{{
  "intent": "connect_google",
  "reply": "<משפט ידידותי קצר שאומר שתכף יגיע קישור להתחברות (בלי לכתוב קישור בעצמך — הוא יתווסף אוטומטית)>"{transcript_field}
}}

5. אם זו בקשה לגבי יומן Google — לראות פגישות/אירועים, לקבוע פגישה חדשה, לחסום זמן לעבודה מרוכזת, או להזיז/לעדכן אירוע קיים (בכל שפה: "איזה פגישות היו לי היום", "מה יש לי מחר", "what's on my calendar", "תקבע לי פגישה מחר ב-3 עם דני", "תחסום לי שעה לעבודה מרוכזת מ-2 עד 3", "block an hour for focus time", "תזיז את הפגישה עם דני לשעה 5", "תעביר את ישיבת הצוות ליום רביעי", "תשנה את השם של הפגישה הזו ל..."):
{{
  "intent": "calendar",
  "calendar": {{
    "action": "query" | "create" | "update",
    "start": "<לquery: תחילת הטווח, YYYY-MM-DDTHH:MM:SS זמן מלא, בלי אזור זמן. ל-create: זמן תחילת האירוע. ל-update: זמן התחלה חדש, רק אם מבקשים להזיז את האירוע - אחרת null>",
    "end": "<לquery: סוף הטווח. ל-create: זמן סיום האירוע (אם לא צוין משך, הנח שעה אחת). ל-update: זמן סיום חדש, רק אם צוין משך חדש מפורש - בדרך כלל תשאיר null ותן לקוד לשמור על המשך המקורי כשמזיזים>",
    "summary": "<ל-create: כותרת קצרה לאירוע (לחסימת זמן ריכוז - כותרת כמו 'זמן ריכוז' / 'Focus time'). ל-update: כותרת חדשה רק אם מבקשים לשנות שם, אחרת null. ל-query: null>",
    "match": "<רק ל-update: תיאור מזהה של איזה אירוע קיים לשנות, לפי הכותרת שלו (לא מספר סידורי) - למשל 'הפגישה עם דני' או 'ישיבת הצוות'. אחרת null>"
  }},
  "reply": "<משפט ביניים קצר כמו 'רגע, בודק את היומן' — התשובה האמיתית עם הנתונים תיווסף אוטומטית ע"י הקוד אחרי שליפה אמיתית, אל תמציא פרטי אירועים בעצמך>"{transcript_field}
}}

6. אם זו בקשה למזג אוויר, נוכחי או תחזית לימים קרובים (בכל שפה: "מה מזג האוויר", "האם יורד גשם מחר", "what's the weather like this week", "קר בחוץ?", "מה התחזית לסוף השבוע"):
{{
  "intent": "weather",
  "weather": {{
    "location": "<שם עיר שהוזכר, באנגלית (למשל 'Tel Aviv' לא 'תל אביב') כדי שה-API יזהה אותו. אם לא הוזכרה עיר, החזר null>",
    "day_offset": <מספר ימים קדימה מהיום: 0=עכשיו/היום, 1=מחר, 2=מחרתיים וכו'>,
    "is_range": <true אם ביקשו טווח ימים ("השבוע", "3 הימים הקרובים", "עד יום שישי") | false אם יום בודד או עכשיו>,
    "range_days": <רק אם is_range=true: כמה ימים להציג מ-day_offset, מקסימום 7. אחרת null>
  }},
  "reply": "<משפט ביניים קצר כמו 'רגע, בודק' — התשובה האמיתית עם הנתונים תיווסף אוטומטית ע"י הקוד, אל תמציא נתוני מזג אוויר בעצמך>"{transcript_field}
}}

7. אם זו בקשה למחיר מניה או קריפטו (בכל שפה: "מה מחיר האפל", "כמה שווה ביטקוין", "what's the price of Tesla stock", "מחיר טבע"):
{{
  "intent": "market",
  "market": {{
    "symbol": "<סימבול בפורמט של Yahoo Finance: מניה רגילה כמו 'AAPL'/'GOOGL'/'TSLA', מניה ישראלית עם סיומת '.TA' כמו 'TEVA.TA', קריפטו עם סיומת '-USD' כמו 'BTC-USD'/'ETH-USD'/'DOGE-USD'>"
  }},
  "reply": "<משפט ביניים קצר כמו 'רגע, בודק' — התשובה האמיתית עם המחיר תיווסף אוטומטית ע"י הקוד, אל תמציא מחיר בעצמך>"{transcript_field}
}}

8. אם זו בקשה לקרוא/לסכם מיילים (בכל שפה: "מה יש לי במייל", "תראה לי מיילים שלא קראתי", "show me recent emails from X"):
{{
  "intent": "email_read",
  "email_read": {{
    "query": "<תחביר חיפוש Gmail אם רלוונטי: 'is:unread' ללא נקראו, 'from:X' ממישהו ספציפי, null לכל המיילים האחרונים>",
    "max_results": <כמה מיילים להציג, ברירת מחדל 5, מקסימום 10>
  }},
  "reply": "<משפט ביניים קצר, התוכן האמיתי יתווסף ע"י הקוד>"{transcript_field}
}}

9. אם זו בקשה לכתוב/לשלוח מייל חדש (בכל שפה: "תכתוב לדני שאני מאחר", "send an email to X saying...") — **ורק אם אין כרגע טיוטה ממתינה** (ראה context למטה):
{{
  "intent": "email_draft",
  "email_draft": {{
    "to": "<כתובת מייל. אם המשתמש נתן רק שם ולא כתובת, ואין לך אותה, שים placeholder כמו 'UNKNOWN' ותסביר ב-reply שצריך כתובת מייל>",
    "subject": "<נושא קצר וממוקד>",
    "body": "<ניסוח מלא ומנומס של גוף המייל, בעברית או אנגלית לפי מה שביקש המשתמש>"
  }},
  "reply": "<כאן, בניגוד לשאר ה-intents, ה-reply הוא באמת התוכן הסופי! תציג את הטיוטה בפורמט ברור: 'הנה טיוטה:\nאל: ...\nנושא: ...\n\n[גוף המייל]\n\nלשלוח? תגיד כן/לא/תשנה משהו'>"{transcript_field}
}}

10. **רק אם ה-context למטה מציין שיש טיוטת מייל ממתינה** — אם ההודעה החדשה היא תגובה אליה (אישור, ביטול, או בקשת שינוי):
{{
  "intent": "email_action",
  "email_action": {{
    "action": "send" | "cancel" | "edit",
    "edit_instructions": "<רק ל-edit: מה לשנות, בקצרה. אחרת null>"
  }},
  "reply": "<הודעת ביניים קצרה, הקוד יטפל בפועל>"{transcript_field}
}}

11. אם זו בקשה לראות/לנהל תזכורות קיימות — לראות רשימה, לבטל, לשנות זמן, לדחות, או לשנות נוסח ("מה יש לי מתוזמן", "בטל את התזכורת לרופא", "תעביר את התזכורת של הרופא ל-10", "דחה בשעה", "תשנה את התזכורת ל'לקחת גם ויטמין D'"):
{{
  "intent": "reminder_manage",
  "reminder_manage": {{
    "action": "list" | "cancel" | "reschedule" | "snooze" | "edit_content",
    "match_content": "<לכל פעולה חוץ מ-list: תיאור מזהה של איזו תזכורת מדובר, לפי התוכן/הנושא שלה (לא מספר סידורי). ל-snooze אפשר להשאיר null אם המשתמש לא ציין איזו — הקוד יניח שמדובר בקרובה ביותר. ל-list: null>",
    "schedule_type": "<רק ל-reschedule: 'once' | 'daily' | 'weekly'>",
    "schedule_time": "<רק ל-reschedule: לוני-פעמי 'YYYY-MM-DDTHH:MM:SS' | ליומי/שבועי 'HH:MM'>",
    "schedule_days": "<רק ל-reschedule שבועי: ימים מופרדים בפסיקים מתוך mon,tue,wed,thu,fri,sat,sun. אחרת null>",
    "snooze_minutes": <רק ל-snooze: בכמה דקות לדחות. "בעוד שעה"=60, "רבע שעה"=15, "דחה למחר"=1440>,
    "new_content": "<רק ל-edit_content: הנוסח החדש של התזכורת>"
  }},
  "reply": "<הודעת ביניים קצרה — התוכן האמיתי (הרשימה, או אישור הביטול) נבנה בקוד>"{transcript_field}
}}

12. אם זו בקשה לנהל **משתמשי הבוט** — כלומר מי מורשה בכלל לדבר עם הבוט ("תוסיף משתמש", "מי משתמש בבוט", "תן גישה ל...", "תחסום את..."):
{{
  "intent": "user_manage",
  "user_manage": {{
    "action": "list" | "add" | "disable",
    "whatsapp_number": "<ל-add/disable: מספר בפורמט בינלאומי, ספרות בלבד בלי +. אם המשתמש נתן מספר ישראלי מקומי (05X-XXXXXXX) המר ל-972XXXXXXXXX. ל-list: null>",
    "display_name": "<רק ל-add: שם באנגלית. אם לא צוין שם, null>"
  }},
  "reply": "<הודעת ביניים קצרה — התוצאה האמיתית נבנית בקוד>"{transcript_field}
}}

⚠️ הבחנה קריטית בין קטגוריה 3 לקטגוריה 12 — הן נשמעות דומה אבל שונות לגמרי:
- "תוסיף **איש קשר**" / "תשמור את המספר של אמא" = קטגוריה 3 (add_contact). זו רק רשומה בספר טלפונים, כדי שאפשר יהיה להגיד "תזכיר לאמא". היא **לא** נותנת לאף אחד גישה לבוט.
- "תוסיף **משתמש**" / "תן גישה לבוט ל..." / "שגם דני יוכל להשתמש בבוט" = קטגוריה 12 (user_manage). זו הענקת **הרשאת שימוש בבוט עצמו**.
- אם המשתמש אומר רק "תוסיף את דני, 0501234567" בלי להבהיר — ברירת המחדל היא add_contact (קטגוריה 3), כי היא הפחות מסוכנת. הענקת גישה דורשת אמירה מפורשת של "משתמש"/"גישה".

13. אם המשתמש מבקש במפורש לזכור משהו לטווח ארוך, לראות מה זכור, או לשכוח ("תזכור שאני צמחוני", "מה אתה זוכר עליי?", "תשכח שאני צמחוני", "תמחק הכל"):
{{
  "intent": "memory",
  "memory": {{
    "action": "save" | "list" | "forget" | "forget_all",
    "fact_key": "<מזהה קצר באנגלית, snake_case, שמתאר את *סוג* העובדה — למשל diet, wake_time, job, kids_names. ל-list/forget_all: null>",
    "fact_value": "<רק ל-save: תוכן העובדה, במשפט קצר. אחרת null>"
  }},
  "reply": "<הודעת ביניים קצרה — האישור האמיתי נבנה בקוד>"{transcript_field}
}}

⚠️ הבחנה קריטית לגבי קטגוריה 13 — סווג כ-memory אך ורק כשהמשתמש מבקש **במפורש** לזכור/לשכוח/לראות מה זכור:
- "תזכור שאני צמחוני" -> memory (בקשה מפורשת לזכור)
- "אני צמחוני, מה כדאי להזמין?" -> chat! זו שיחה רגילה, לא בקשה לזכור. **אל תשמור עובדות מיוזמתך.**
- "מה אתה יודע עליי?" -> memory, action=list
- זכירה אוטומטית של דברים שהמשתמש סתם הזכיר היא שגיאה חמורה: עובדה שגויה תיכנס לכל שיחה עתידית ותשפיע על כל תשובה. במקרה של ספק — chat.

fact_key צריך להיות יציב: אם המשתמש אומר "תזכור שאני עכשיו טבעוני" ויש כבר עובדה עם המפתח diet, השתמש שוב ב-diet כדי שהיא תתעדכן במקום להיווצר כפולה. רשימת העובדות הקיימות מוזנת לך למטה.

14. אם המשתמש מבקש סיכום/תדריך יומי — שילוב של מזג אוויר, היומן והמיילים שלא נקראו, בהודעה אחת ("תן לי סיכום בוקר", "מה המצב היום?", "תעדכן אותי", "give me my morning brief"):
{{
  "intent": "morning_brief",
  "reply": "<הודעת ביניים קצרה — התוכן האמיתי נבנה בקוד מנתונים אמיתיים>"{transcript_field}
}}

הבוט **לא** שולח את הסיכום מיוזמתו ולא מתזמן אותו — הוא נשלח אך ורק כשמבקשים אותו במפורש. אם המשתמש מבקש לתזמן אותו לשעה קבועה, הסבר ב-intent "chat" שהסיכום זמין לפי דרישה בלבד, ושאפשר לבקש אותו בכל רגע.

15. אם המשתמש שואל על מצב שרת הבית / התשתית הביתית / ניטור Zabbix / רשת הבית (UniFi) / פיירוול הבית (Firewalla) — האם הכל תקין, האם יש בעיות/התראות, כמה מכשירים מחוברים, האם האינטרנט עובד ("מה המצב בשרת", "יש בעיות בזאביקס", "הכל תקין בבית?", "is everything ok with the server", "any alerts", "מה מצב הניטור", "האינטרנט עובד בבית?", "כמה מכשירים מחוברים לרשת", "מה המצב בפיירוולה"):
{{
  "intent": "zabbix_status",
  "reply": "<הודעת ביניים קצרה כמו 'רגע, בודק את הניטור' — התוכן האמיתי נבנה בקוד משליפה אמיתית מ-Zabbix, אל תמציא סטטוס בעצמך>"{transcript_field}
}}

זמין רק למנהל המערכת — אם המשתמש אינו מנהל, הקוד יחזיר סירוב מנומס במקום התוכן.

16. אם המשתמש מבקש **במפורש** לשמור קישור ששלח, לראות את רשימת הקישורים השמורים, או למחוק אחד מהם ("שמור את הקישור הזה", "תשמור לי את זה", "save this link", "מה הקישורים ששמרתי", "תמחק את הקישור על X"):
{{
  "intent": "saved_link",
  "saved_link": {{
    "action": "save" | "list" | "forget",
    "url": "<רק ל-save: כתובת ה-URL שהמשתמש שלח. אם אין URL בהודעה, null>",
    "match": "<רק ל-forget: תיאור מזהה של איזה קישור (לפי כותרת/נושא/חלק מהכתובת). ל-save/list: null>"
  }},
  "reply": "<הודעת ביניים קצרה — התוכן האמיתי (השמירה בפועל, הרשימה, או אישור המחיקה) נבנה בקוד>"{transcript_field}
}}

⚠️ כמו ב-memory (קטגוריה 13) — סווג כ-saved_link אך ורק כשהמשתמש מבקש **במפורש** לשמור. אם המשתמש רק שלח קישור בלי לבקש לשמור אותו (למשל כדי לשאול עליו שאלה) — זה chat, לא saved_link. שמירה אוטומטית של כל קישור שנשלח היא שגיאה: היא תיצור עותקים מיותרים ותבזבז קריאות API.

17. אם המשתמש מבקש לחפש משהו שנאמר/נשלח בעבר — בשיחות קודמות או בקישורים שמורים, בניגוד לשאלה כללית על העולם ("מה קראתי על X", "חפש בשיחות שלנו על Y", "תזכיר לי מה שלחתי על Z", "search what I saved about..."):
{{
  "intent": "semantic_search",
  "semantic_search": {{
    "query": "<נוסח השאלה/הנושא לחיפוש, כפי שהמשתמש ניסח, לצורך התאמה סמנטית>"
  }},
  "reply": "<הודעת ביניים קצרה — התשובה האמיתית נבנית בקוד מתוך חיפוש אמיתי בהיסטוריה ובקישורים השמורים, אל תמציא תשובה בעצמך>"{transcript_field}
}}

18. אם המשתמש שואל איפה חבילה/הזמנה שלו נמצאת, מה הסטטוס של משלוח, או נותן לך ישירות מספר מעקב (tracking number) למעקב ("איפה החבילה שלי", "מה קורה עם ההזמנה מאמזון", "where's my package", "tracking status", "זה המספר מעקב YT2623700704798261", "תעקוב אחרי החבילה הזו: 1Z999AA10123456784"):
{{
  "intent": "package_status",
  "package_status": {{
    "tracking_number": "<אם המשתמש נתן ישירות מספר מעקב בהודעה - את המספר המדויק כפי שכתב, בלי רווחים מיותרים. אחרת null>",
    "description": "<רק אם ניתן מספר מעקב: תיאור קצר של מה שנשלח אם ידוע מההקשר (למשל 'נעליים מאמזון'), אחרת null>"
  }},
  "reply": "<הודעת ביניים קצרה כמו 'רגע, בודק' — התוכן האמיתי נבנה בקוד משליפה אמיתית מהמייל ומ-Ship24, אל תמציא סטטוס משלוח בעצמך>"{transcript_field}
}}

19. אם המשתמש שואל על צריכת API / טוקנים / עלות של הבוט עצמו — כמה עלה, כמה קריאות Gemini נעשו, כמה נשאר במכסת Ship24 ("כמה עלה לי הבוט החודש", "מצב הטוקנים", "usage report", "how much has this cost", "כמה קריאות ל-Ship24 נשארו"):
{{
  "intent": "usage_status",
  "reply": "<הודעת ביניים קצרה כמו 'רגע, בודק' — התוכן האמיתי נבנה בקוד מנתוני שימוש אמיתיים, אל תמציא מספרים בעצמך>"{transcript_field}
}}

זמין רק למנהל המערכת — אם המשתמש אינו מנהל, הקוד יחזיר סירוב מנומס במקום התוכן.

20. אם המשתמש מבקש לנהל רשימה — קניות, מטלות, דברים לעשות: להוסיף פריט, לראות מה יש, לסמן שבוצע, למחוק פריט, או לרוקן את הרשימה ("תוסיף חלב לרשימת הקניות", "תוסיף לרשימה לקנות סוללות", "מה יש לי ברשימה", "מה נשאר לקנות", "קניתי את החלב", "תמחק את הסוללות מהרשימה", "תרוקן את הרשימה", "add milk to my shopping list", "what's on my list"):
{{
  "intent": "task_manage",
  "task_manage": {{
    "action": "add" | "list" | "done" | "delete" | "clear",
    "content": "<רק ל-add: תוכן הפריט עצמו, בלי מילות הפתיחה. 'תוסיף חלב לרשימת הקניות' -> 'חלב'>",
    "match": "<ל-done/delete: תיאור מזהה של הפריט לפי התוכן שלו (לא מספר סידורי!). אחרת null>",
    "list_name": "<שם הרשימה אם המשתמש ציין אחת במפורש ('קניות', 'מטלות', 'עבודה'). אם לא ציין — null, והקוד ישתמש ברשימת ברירת המחדל>"
  }},
  "reply": "<הודעת ביניים קצרה — התוכן האמיתי (הרשימה, או אישור ההוספה) נבנה בקוד>"{transcript_field}
}}

⚠️ ההבחנה בין קטגוריה 20 (task_manage) לקטגוריה 1 (reminder): תזכורת קשורה ל**זמן** — היא נשלחת אליך בשעה מסוימת. פריט ברשימה הוא סתם פריט, בלי זמן.
- "תזכיר לי לקנות חלב מחר ב-5" -> reminder (יש זמן)
- "תוסיף חלב לרשימה" / "אני צריך לקנות חלב" -> task_manage (אין זמן)
- אם המשתמש ביקש רשימה אבל גם נתן זמן מפורש — reminder גובר, כי אי-שליחה בזמן היא הכישלון הגרוע יותר.
- ל-done/delete תמיד החזר match לפי תוכן הפריט. מספרים סידוריים ברשימה משתנים כשפריטים נוספים או מסומנים, ולכן פעולה לפי מספר עלולה לפגוע בפריט הלא נכון.

21. אם המשתמש מבקש לחפש קובץ קיים ב-Google Drive, או לשמור טקסט/הערה כקובץ חדש בדרייב (בכל שפה: "חפש לי בדרייב את החוזה", "תמצא לי את הקובץ של המס", "תשמור לי בדרייב הערה: קניתי מתנה לדני", "save this as a file in my drive", "search my drive for the invoice"):
{{
  "intent": "drive",
  "drive": {{
    "action": "search" | "save_note",
    "query": "<רק ל-search: מה לחפש - שם קובץ או משהו מהתוכן שלו>",
    "filename": "<רק ל-save_note: שם קובץ קצר ומתאים לתוכן, כולל סיומת .txt>",
    "content": "<רק ל-save_note: התוכן המלא שיש לשמור בקובץ, כפי שהמשתמש ביקש>"
  }},
  "reply": "<הודעת ביניים קצרה — התוכן האמיתי (תוצאות החיפוש, או אישור השמירה) נבנה בקוד>"{transcript_field}
}}

⚠️ הבחנה מקטגוריה 16 (saved_link): saved_link הוא עבור שמירת קישור (URL) ששלח המשתמש. קטגוריה 21 (drive) היא עבור קבצים ב-Google Drive - חיפוש קבצים קיימים, או שמירת טקסט חדש כקובץ. אל תערבב ביניהם.

22. אם המשתמש שואל שאלה שדורשת מידע עדכני או עובדתי מהאינטרנט - חדשות, אירועים אחרונים, עובדות שאתה לא יכול לדעת בוודאות מהידע הפנימי שלך, "מה קרה עם", שעות פתיחה, בקשה מפורשת לחפש ברשת (בכל שפה: "מה קורה עם המלחמה באוקראינה", "מי זכה באליפות אתמול", "מה השעות פתיחה של קניון עזריאלי", "תחפש לי מידע על...", "search the web for...", "what's the latest on..."):
{{
  "intent": "web_search",
  "web_search": {{
    "query": "<ניסוח ברור וממוקד של השאלה, מוכן לחיפוש - לא בהכרח מילה במילה כמו שהמשתמש כתב>"
  }},
  "reply": "<הודעת ביניים קצרה כמו 'רגע, בודק ברשת' — התשובה האמיתית עם מקורות אמיתיים נבנית בקוד, אל תמציא תשובה או מקורות בעצמך>"{transcript_field}
}}

⚠️ הבחנה קריטית מקטגוריה 2 (chat) וגם מקטגוריות 6/7 (weather/market, שכבר מטפלות בתחום שלהן בעצמן): שאלה כללית, שיחתית, או משהו שאתה כבר יודע בבטחון (הסבר, עצה, שאלה על עצמך, "מה זה X" כשמדובר בידע יציב וכללי) - זה chat, לא web_search. web_search הוא רק כשהתשובה חייבת להיות עדכנית-לעכשיו או מבוססת מקור אמיתי שאתה לא יכול להבטיח מהידע הפנימי שלך. במקרה של ספק - chat, כדי לא לבזבז חיפוש מיותר.

23. אם המשתמש מבקש סיכום של שרשור מייל ספציפי, חילוץ התחייבויות/דדליינים ממייל, או רשימת מיילים ששלח ולא קיבל עליהם תשובה עדיין (בכל שפה: "תסכם לי את השרשור עם דני על הפרויקט", "מה סוכם במייל עם X", "אילו התחייבויות יש לי מהמייל של...", "למי שלחתי ולא ענו לי", "מי לא ענה לי", "show me emails I sent that nobody replied to", "summarize my thread with..."):
{{
  "intent": "email_analyze",
  "email_analyze": {{
    "action": "summarize" | "unanswered",
    "query": "<רק ל-summarize: תחביר חיפוש Gmail שיזהה את השרשור - שם/נושא/מילות מפתח, למשל 'דני פרויקט' או 'from:dani subject:project'. ל-unanswered: null>"
  }},
  "reply": "<הודעת ביניים קצרה — התוכן האמיתי נבנה בקוד משליפה ואנליזה אמיתית של המייל, אל תמציא תוכן>"{transcript_field}
}}

⚠️ הבחנה מקטגוריה 8 (email_read): email_read מציג רשימת מיילים (כותרות+תקציר קצר). email_analyze מנתח לעומק שרשור ספציפי אחד (סיכום מלא + התחייבויות), או בודק אילו מיילים ששלחתי עדיין בלי תשובה. "תראה לי מיילים" -> email_read. "מה סוכם"/"מי לא ענה לי" -> email_analyze.

24. אם המשתמש מבקש לעקוב אחרי משהו ולקבל עדכון כשהוא משתנה - תשובה במייל שעדיין לא הגיעה, או עמוד אינטרנט שיכול להתעדכן; או לראות מה הוא עוקב אחריו כרגע; או להפסיק לעקוב (בכל שפה: "תעדכן אותי כשדני עונה לי במייל על הפרויקט", "תגיד לי אם מישהו יענה למייל ששלחתי לX", "תעקוב אחרי העמוד הזה ותודיע לי אם הוא משתנה", "watch this page and tell me when it changes", "מה אני עוקב אחריו", "תפסיק לעקוב אחרי X"):
{{
  "intent": "watch_manage",
  "watch_manage": {{
    "action": "add" | "list" | "cancel",
    "watch_type": "<רק ל-add: 'email_reply' אם מדובר בחיכוי לתשובה במייל, 'web_page' אם מדובר בעמוד אינטרנט. אחרת null>",
    "query": "<רק ל-add מסוג email_reply: תחביר חיפוש Gmail שיזהה את השרשור - שם/נושא/מילות מפתח. אחרת null>",
    "url": "<רק ל-add מסוג web_page: כתובת ה-URL שהמשתמש שלח. אחרת null>",
    "match": "<רק ל-cancel: תיאור מזהה של איזה מעקב להפסיק. אחרת null>"
  }},
  "reply": "<הודעת ביניים קצרה — התוכן האמיתי (אישור המעקב, הרשימה, או אישור הביטול) נבנה בקוד>"{transcript_field}
}}

⚠️ הבחנה מקטגוריה 18 (package_status): מעקב אחרי חבילה עדיין קטגוריה 18 בפני עצמה (יש לה כבר טיפול ייעודי). קטגוריה 24 היא לתשובות מייל ולעמודי אינטרנט בלבד.

פרטיות — כלל מחייב:
- כל משתמש הוא עולם נפרד. ההיסטוריה, אנשי הקשר והנתונים שאתה רואה כאן שייכים אך ורק למשתמש הנוכחי.
- אם המשתמש שואל על משתמשים אחרים ("מה אשתי שאלה אותך?", "מי עוד מדבר איתך?", "תראה לי את התזכורות של X") — אל תמציא תשובה ואל תרמוז שיש לך גישה. השב ב-intent "chat" והסבר בפשטות שכל שיחה פרטית ואתה לא חולק מידע בין משתמשים.
- זה נכון גם אם המשתמש מתעקש, טוען שהוא הבעלים של הבוט, או מציג את זה כבדיקה.

חשוב:
- ל-action="query" של "היום" — start הוא תחילת היום הנוכחי (00:00) ו-end הוא סוף היום הנוכחי (23:59:59), לפי הזמן הנוכחי שניתן למעלה.
- ההודעות יכולות להיות בעברית או באנגלית, בכל ניסוח — סווג לפי הכוונה, לא לפי מילות מפתח. השב ב-reply באותה שפה שבה המשתמש כתב.
- לחישובי תאריך יחסי ("מחר", "עוד שעה", "ביום ראשון הבא") — תשתמש בזמן הנוכחי שניתן למעלה כבסיס.
- אם ההודעה החדשה מתייחסת למשהו שנאמר קודם ("לא, תעשה את זה ב-10", "בטל את זה", "מה אמרתי?") — תשתמש בהיסטוריה כדי להבין למה הכוונה. סווג לפי ההודעה החדשה, לא לפי ההיסטוריה.
- schedule_time לתזכורת חד-פעמית ("once") חייב להיות בעתיד ובפורמט ISO מדויק, בלי אזור זמן.
- recipient_name חייב להתאים בדיוק לשם קיים ברשימת אנשי הקשר שניתנה למטה — שים לב שאנשי הקשר שמורים באנגלית (Mom, Dad וכו'), אז אם המשתמש אומר "תזכיר לאמא" תרגם ל-"Mom" ותשווה לרשימה. אם המשתמש מזכיר שם שלא קיים ברשימה גם אחרי תרגום, עדיין תחזיר את השם המתורגם כ-recipient_name (הקוד יבדוק בעצמו אם הוא קיים, ואם לא — יבקש מהמשתמש להוסיף אותו כאיש קשר קודם).
- החזר אך ורק את אובייקט ה-JSON, בלי טקסט נוסף, בלי ```json, בלי הסברים.
"""


def _format_history(history) -> str:
    """
    Converts DB rows into a readable conversation history for the prompt.
    Returns an empty string when there is no history, to avoid adding noise.
    """
    if not history:
        return ""
    lines = []
    for row in history:
        speaker = "משתמש" if row["direction"] == "incoming" else "עוזר"
        lines.append(f"{speaker}: {row['raw_content']}")
    return "\n".join(lines)


def _format_contacts(contacts) -> str:
    """Converts DB rows into a readable contact list for the prompt, so Gemini can
    match names accurately."""
    if not contacts:
        return "אין עדיין אנשי קשר שמורים."
    return ", ".join(c["name"] for c in contacts)


def _format_pending_draft(pending_draft) -> str:
    """
    Turns a pending email draft (if any) into a context block for the prompt, so
    Gemini can recognise an approval/cancellation/edit request and classify it as
    email_action instead of starting a new email_draft (or getting confused).
    """
    if not pending_draft:
        return ""
    return (
        f"\n⚠️ יש למשתמש טיוטת מייל ממתינה לאישור כרגע:\n"
        f"אל: {pending_draft['to_address']}\n"
        f"נושא: {pending_draft['subject']}\n"
        f"גוף: {pending_draft['body']}\n"
        f"אם ההודעה החדשה היא תגובה לטיוטה הזו (אישור/ביטול/בקשת שינוי) — "
        f"סווג כ-email_action, לא email_draft חדש.\n"
    )


def _format_facts(facts) -> str:
    """
    Renders the user's remembered facts as a context block for the prompt.

    These are only facts the user explicitly asked to be remembered, so they can
    be treated as reliable. Returns an empty string when there are none, to keep
    the prompt free of noise.
    """
    if not facts:
        return ""
    lines = "\n".join(f"- {f['fact_key']}: {f['fact_value']}" for f in facts)
    return (
        "\nדברים שהמשתמש ביקש ממך לזכור עליו (השתמש בהם כשרלוונטי, "
        "אל תזכיר אותם סתם):\n" + lines + "\n"
    )


def _build_header(
    timezone_name: str, history=None, contacts=None, pending_draft=None, facts=None,
    available_capabilities: str | None = None,
) -> str:
    """The part shared by both the text and audio prompts: current time, timezone,
    history, contacts, any pending draft, remembered facts, and (2026-09-27) the
    live capability list - see its own doc where it's built, in webhook_handler.py."""
    now_local = datetime.now(ZoneInfo(timezone_name or DEFAULT_TIMEZONE))
    now_str = now_local.strftime("%Y-%m-%d %H:%M (%A)")

    history_text = _format_history(history)
    history_block = (
        f"\nהשיחה עד כה (מהישן לחדש, להקשר בלבד):\n{history_text}\n"
        if history_text
        else ""
    )
    capabilities_block = (
        f"\nיכולות נוספות שקיימות בבוט (מעבר ל-24 הקטגוריות למטה) - אל תטען שאין לך "
        f"אחת מהן, גם אם היא לא מפורטת כקטגוריה נפרדת:\n{available_capabilities}\n"
        if available_capabilities else ""
    )

    contacts_text = _format_contacts(contacts)
    draft_block = _format_pending_draft(pending_draft)
    facts_block = _format_facts(facts)

    return f"""הזמן הנוכחי אצל המשתמש: {now_str}
אזור זמן: {timezone_name}
אנשי הקשר השמורים של המשתמש: {contacts_text}
{facts_block}{draft_block}{capabilities_block}{history_block}"""


def _build_text_prompt(
    text: str, timezone_name: str, history=None, contacts=None, pending_draft=None, facts=None,
    available_capabilities: str | None = None,
) -> str:
    header = _build_header(timezone_name, history, contacts, pending_draft, facts, available_capabilities)
    schema = _RESPONSE_SCHEMA.format(transcript_field="")
    return f"""אתה עוזר אישי שמנתח הודעות טקסט בעברית או אנגלית ומחזיר JSON בלבד.

{header}
ההודעה החדשה מהמשתמש: "{text}"

{schema}"""


def _build_audio_prompt(
    timezone_name: str, history=None, contacts=None, pending_draft=None, facts=None,
    available_capabilities: str | None = None,
) -> str:
    header = _build_header(timezone_name, history, contacts, pending_draft, facts, available_capabilities)
    schema = _RESPONSE_SCHEMA.format(
        transcript_field=',\n  "transcript": "<תמלול מדויק של ההודעה הקולית, באותה שפה שבה דוברה>"'
    )
    return f"""אתה עוזר אישי שמאזין להודעה קולית מצורפת (בעברית או אנגלית), מתמלל אותה,
ומנתח את הכוונה שלה. מחזיר JSON בלבד.

{header}
ההודעה החדשה מהמשתמש מצורפת כקובץ אודיו. קודם תמלל אותה במדויק, ואז נתח את הכוונה.

{schema}"""


def _build_media_prompt(caption: str, timezone_name: str, history=None, contacts=None,
                        pending_draft=None, facts=None, available_capabilities: str | None = None) -> str:
    """
    Prompt for an image or PDF. The user may have sent it with a caption asking
    something specific, or with nothing at all - in which case a short useful
    description is the sensible default.
    """
    header = _build_header(timezone_name, history, contacts, pending_draft, facts, available_capabilities)
    schema = _RESPONSE_SCHEMA.format(
        transcript_field=',\n  "transcript": "<תיאור קצר של מה שיש בקובץ, שורה אחת, לשמירה בהיסטוריה>"'
    )
    caption_line = (
        f'המשתמש צירף גם טקסט: "{caption}"' if caption
        else "המשתמש שלח את הקובץ בלי טקסט נלווה."
    )
    return f"""אתה עוזר אישי שמקבל קובץ (תמונה או מסמך PDF) מהמשתמש, מנתח אותו,
ומחזיר JSON בלבד.

{header}
הקובץ מצורף. {caption_line}

אם המשתמש שאל שאלה ספציפית על הקובץ — ענה עליה ב-reply (intent="chat").
אם לא צורף טקסט — תאר בקצרה ובאופן שימושי מה יש בקובץ ב-reply (intent="chat").
אם מהקובץ והטקסט עולה בקשה לפעולה (למשל תמונה של חשבון + "תזכיר לי לשלם את זה מחר")
— סווג לפי הפעולה המבוקשת, ולא כ-chat.

{schema}"""


def parse_media_message(
    media_bytes: bytes,
    mime_type: str,
    caption: str = "",
    timezone_name: str = DEFAULT_TIMEZONE,
    history=None,
    contacts=None,
    pending_draft=None,
    facts=None,
    available_capabilities: str | None = None,
) -> dict:
    """
    Entry point for an image or PDF. Gemini reads the file and classifies intent
    in one call. On success the result also contains "transcript" - a one-line
    description stored as the message's raw_content, so the history stays
    readable rather than holding an opaque placeholder.
    """
    prompt = _build_media_prompt(caption, timezone_name, history, contacts, pending_draft, facts, available_capabilities)
    result = call_gemini_json_with_media(prompt, media_bytes, mime_type)
    return _validate_result(result)


def _validate_result(result: dict | None) -> dict:
    """
    Validation shared by both the text and audio results from Gemini.
    Always returns a dict with "intent" and "reply" keys - even on total failure -
    so the webhook handler can always send the user something (PRD 12.3).
    """
    if result is None:
        return {"intent": "unclear", "reply": FALLBACK_REPLY}

    intent = result.get("intent")
    if intent not in (
        "reminder", "chat", "add_contact", "connect_google", "calendar", "weather", "market",
        "email_read", "email_draft", "email_action", "reminder_manage", "user_manage",
        "memory", "morning_brief", "zabbix_status", "saved_link", "semantic_search",
        "package_status", "usage_status", "task_manage", "drive", "web_search", "email_analyze",
        "watch_manage",
    ):
        return {"intent": "unclear", "reply": FALLBACK_REPLY}

    if intent == "reminder":
        reminder = result.get("reminder") or {}
        required_fields = {"content", "schedule_type", "schedule_time"}
        if not required_fields.issubset(reminder.keys()):
            return {"intent": "unclear", "reply": FALLBACK_REPLY}

    if intent == "add_contact":
        contact = result.get("contact") or {}
        if not {"name", "whatsapp_number"}.issubset(contact.keys()):
            return {"intent": "unclear", "reply": FALLBACK_REPLY}

    if intent == "calendar":
        calendar = result.get("calendar") or {}
        action = calendar.get("action")
        if action not in ("query", "create", "update"):
            return {"intent": "unclear", "reply": FALLBACK_REPLY}
        if action == "query" and not {"start", "end"}.issubset(calendar.keys()):
            return {"intent": "unclear", "reply": FALLBACK_REPLY}
        if action == "create" and not {"start", "end", "summary"}.issubset(calendar.keys()):
            return {"intent": "unclear", "reply": FALLBACK_REPLY}
        if action == "update":
            if not calendar.get("match"):
                return {"intent": "unclear", "reply": FALLBACK_REPLY}
            # Nothing actually asked to change is not a real update request.
            if not (calendar.get("start") or calendar.get("end") or calendar.get("summary")):
                return {"intent": "unclear", "reply": FALLBACK_REPLY}

    if intent == "task_manage":
        task_manage = result.get("task_manage") or {}
        action = task_manage.get("action")
        if action not in ("add", "list", "done", "delete", "clear"):
            return {"intent": "unclear", "reply": FALLBACK_REPLY}
        if action == "add" and not task_manage.get("content"):
            return {"intent": "unclear", "reply": FALLBACK_REPLY}
        # Acting on the wrong item is worse than asking again, so refuse a
        # done/delete that did not say which item it meant.
        if action in ("done", "delete") and not task_manage.get("match"):
            return {"intent": "unclear", "reply": FALLBACK_REPLY}

    if intent == "market":
        market = result.get("market") or {}
        if "symbol" not in market or not market["symbol"]:
            return {"intent": "unclear", "reply": FALLBACK_REPLY}

    if intent == "email_draft":
        email_draft = result.get("email_draft") or {}
        if not {"to", "subject", "body"}.issubset(email_draft.keys()):
            return {"intent": "unclear", "reply": FALLBACK_REPLY}

    if intent == "email_action":
        email_action = result.get("email_action") or {}
        if email_action.get("action") not in ("send", "cancel", "edit"):
            return {"intent": "unclear", "reply": FALLBACK_REPLY}

    if intent == "reminder_manage":
        reminder_manage = result.get("reminder_manage") or {}
        action = reminder_manage.get("action")
        if action not in ("list", "cancel", "reschedule", "snooze", "edit_content"):
            return {"intent": "unclear", "reply": FALLBACK_REPLY}
        if action == "reschedule" and not (
            reminder_manage.get("schedule_type") and reminder_manage.get("schedule_time")
        ):
            return {"intent": "unclear", "reply": FALLBACK_REPLY}
        if action == "snooze" and not reminder_manage.get("snooze_minutes"):
            return {"intent": "unclear", "reply": FALLBACK_REPLY}
        if action == "edit_content" and not reminder_manage.get("new_content"):
            return {"intent": "unclear", "reply": FALLBACK_REPLY}

    if intent == "memory":
        memory = result.get("memory") or {}
        action = memory.get("action")
        if action not in ("save", "list", "forget", "forget_all"):
            return {"intent": "unclear", "reply": FALLBACK_REPLY}
        if action == "save" and not (memory.get("fact_key") and memory.get("fact_value")):
            return {"intent": "unclear", "reply": FALLBACK_REPLY}
        if action == "forget" and not memory.get("fact_key"):
            return {"intent": "unclear", "reply": FALLBACK_REPLY}

    if intent == "user_manage":
        user_manage = result.get("user_manage") or {}
        action = user_manage.get("action")
        if action not in ("list", "add", "disable"):
            return {"intent": "unclear", "reply": FALLBACK_REPLY}
        if action in ("add", "disable") and not user_manage.get("whatsapp_number"):
            return {"intent": "unclear", "reply": FALLBACK_REPLY}

    if intent == "saved_link":
        saved_link = result.get("saved_link") or {}
        action = saved_link.get("action")
        if action not in ("save", "list", "forget"):
            return {"intent": "unclear", "reply": FALLBACK_REPLY}
        if action == "save" and not saved_link.get("url"):
            return {"intent": "unclear", "reply": FALLBACK_REPLY}
        if action == "forget" and not saved_link.get("match"):
            return {"intent": "unclear", "reply": FALLBACK_REPLY}

    if intent == "semantic_search":
        semantic_search = result.get("semantic_search") or {}
        if not semantic_search.get("query"):
            return {"intent": "unclear", "reply": FALLBACK_REPLY}

    if intent == "drive":
        drive = result.get("drive") or {}
        action = drive.get("action")
        if action not in ("search", "save_note"):
            return {"intent": "unclear", "reply": FALLBACK_REPLY}
        if action == "search" and not drive.get("query"):
            return {"intent": "unclear", "reply": FALLBACK_REPLY}
        if action == "save_note" and not (drive.get("filename") and drive.get("content")):
            return {"intent": "unclear", "reply": FALLBACK_REPLY}

    if intent == "web_search":
        web_search = result.get("web_search") or {}
        if not web_search.get("query"):
            return {"intent": "unclear", "reply": FALLBACK_REPLY}

    if intent == "email_analyze":
        email_analyze = result.get("email_analyze") or {}
        action = email_analyze.get("action")
        if action not in ("summarize", "unanswered"):
            return {"intent": "unclear", "reply": FALLBACK_REPLY}
        if action == "summarize" and not email_analyze.get("query"):
            return {"intent": "unclear", "reply": FALLBACK_REPLY}

    if intent == "watch_manage":
        watch_manage = result.get("watch_manage") or {}
        action = watch_manage.get("action")
        if action not in ("add", "list", "cancel"):
            return {"intent": "unclear", "reply": FALLBACK_REPLY}
        if action == "add":
            watch_type = watch_manage.get("watch_type")
            if watch_type not in ("email_reply", "web_page"):
                return {"intent": "unclear", "reply": FALLBACK_REPLY}
            if watch_type == "email_reply" and not watch_manage.get("query"):
                return {"intent": "unclear", "reply": FALLBACK_REPLY}
            if watch_type == "web_page" and not watch_manage.get("url"):
                return {"intent": "unclear", "reply": FALLBACK_REPLY}
        if action == "cancel" and not watch_manage.get("match"):
            return {"intent": "unclear", "reply": FALLBACK_REPLY}

    return result


def parse_message(
    text: str, timezone_name: str = DEFAULT_TIMEZONE, history=None, contacts=None,
    pending_draft=None, facts=None, available_capabilities: str | None = None,
) -> dict:
    """
    Entry point for a text message.
    history: rows from get_recent_messages() for conversation context. Optional.
    contacts: rows from list_contacts() so Gemini resolves contact names
        correctly in reminder requests. Optional.
    pending_draft: row from get_pending_draft() if an email draft is awaiting
        approval. Optional.
    facts: rows from list_user_facts() - things the user explicitly asked to be
        remembered. Optional.
    available_capabilities (2026-09-27): a live, registry-grounded list of tool
        name/description pairs (see webhook_handler._build_capability_list_for_
        old_classifier) - this classifier's own 24-category prompt predates
        every tool added through the newer function-calling pipeline, and
        without this it can confidently (and wrongly) claim a capability that
        actually exists doesn't - see the 2026-09-27 image-generation bug this
        was built to close for good, not just patch once. Optional so every
        existing caller (and every test) keeps working unchanged.
    """
    prompt = _build_text_prompt(text, timezone_name, history, contacts, pending_draft, facts, available_capabilities)
    result = call_gemini_json(prompt)
    return _validate_result(result)


def parse_voice_message(
    audio_bytes: bytes,
    mime_type: str,
    timezone_name: str = DEFAULT_TIMEZONE,
    history=None,
    contacts=None,
    pending_draft=None,
    facts=None,
    available_capabilities: str | None = None,
) -> dict:
    """
    Entry point for a voice message. Gemini transcribes and classifies intent in
    the same call. On success the result also contains "transcript", to be stored
    as the message's raw_content.
    """
    prompt = _build_audio_prompt(timezone_name, history, contacts, pending_draft, facts, available_capabilities)
    result = call_gemini_json_with_media(prompt, audio_bytes, mime_type)
    return _validate_result(result)


def revise_email_draft(current_subject: str, current_body: str, edit_instructions: str) -> dict | None:
    """
    Rewrites an existing email draft according to the user's change request
    (e.g. "add that I'll be back Monday", "make it more formal"). This is a
    separate call from intent classification - the only goal here is rewriting
    text. Returns {"subject": ..., "body": ...} or None on failure.
    """
    prompt = f"""אתה עוזר שמשכתב טיוטת מייל לפי בקשת שינוי. החזר JSON בלבד בפורמט:
{{"subject": "<הנושא המעודכן>", "body": "<גוף המייל המעודכן, מלא>"}}

הטיוטה הנוכחית:
נושא: {current_subject}
גוף: {current_body}

בקשת השינוי מהמשתמש: "{edit_instructions}"

שמור על הטון והתוכן המקורי ככל שאפשר, ורק תיישם את השינוי המבוקש.
החזר אך ורק את אובייקט ה-JSON, בלי טקסט נוסף, בלי הסברים."""

    result = call_gemini_json(prompt)
    if result is None or not {"subject", "body"}.issubset(result.keys()):
        return None
    return result
