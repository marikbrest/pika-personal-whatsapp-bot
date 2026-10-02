"""
Executes a classified tool call and returns the final reply text - the one
place that knows both "how a reply gets produced" (registry.py's
renders_own_reply contract) and "what to do when Gemini's own schema
compliance wasn't actually honored" (validate + missing-field fallbacks).
Provider-neutral: takes a Tool and plain args, nothing from gemini_adapter.
"""
from src.integrations.google_oauth import GoogleNotConfiguredError
from src.intent_parser import FALLBACK_REPLY
from src.tools.registry import Tool

GOOGLE_NOT_CONFIGURED_REPLY = (
    "חיבור לגוגל (יומן, ג'ימייל, דרייב) עוד לא הוגדר בבוט הזה. "
    "מי שמפעיל את הבוט צריך להגדיר GOOGLE_CLIENT_ID ו-GOOGLE_CLIENT_SECRET (ראו README)."
)


def execute_tool(tool: Tool, user: dict, args: dict) -> str:
    if tool.validate is not None and not tool.validate(args):
        return FALLBACK_REPLY

    if not tool.renders_own_reply:
        # required=["reply_text"] is enforced at registration (Tool.__post_init__),
        # but that only proves the SCHEMA asks for it - Gemini's own compliance
        # isn't literally guaranteed (same reasoning intent_parser._validate_result
        # already applies to every "required" field it hand-checks today), so this
        # still falls back rather than risk sending an empty WhatsApp message.
        reply_text = args.get("reply_text") or FALLBACK_REPLY
        try:
            tool.handler(user, args)  # side effect only; return value unused
        except GoogleNotConfiguredError:
            return GOOGLE_NOT_CONFIGURED_REPLY
        except Exception as e:
            print(f"[tools] handler for {tool.name!r} raised (reply_text still sent): {e}")
        return reply_text

    try:
        result = tool.handler(user, args)
    except GoogleNotConfiguredError:
        return GOOGLE_NOT_CONFIGURED_REPLY
    except Exception as e:
        print(f"[tools] handler for {tool.name!r} raised: {e}")
        return "משהו השתבש, אפשר לנסות שוב?"
    return result or FALLBACK_REPLY
