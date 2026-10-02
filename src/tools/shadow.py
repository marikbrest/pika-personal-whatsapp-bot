"""
Stage B of the function-calling migration: shadow-mode comparison. Every
text message the OLD classifier (intent_parser.parse_message) already
handled is ALSO run through the new tools pipeline, purely for comparison
logging. The old classifier's result is what the user actually gets - this
module's output is never sent to anyone and never affects any reply, never
touches result/reply, never re-runs a side effect (it only classifies, it
never calls execute_tool - a shadow-mode "add milk to my list" must not
actually add milk to the list twice).

Runs on a background thread (same "never add to the user's perceived
response time" pattern as db.models._embed_message_in_background) and is
exception-isolated at every layer - a Gemini failure, a bug in the new
pipeline, a DB write failure - none of it can ever affect what the live bot
sends back. That is the entire point of a shadow phase.
"""
import json
import threading


def _run(user: dict, text_body: str, whatsapp_message_id: str, old_intent: str) -> None:
    # Late imports, deliberately not at module top level: src.tools.pilot
    # imports _handle_weather/_handle_market/_handle_task_manage FROM
    # webhook_handler.py, and THIS module is imported BY webhook_handler.py
    # at ITS top level - importing src.tools here at call time, not at this
    # module's import time, breaks that cycle. Safe by the time this actually
    # runs (on a background thread, well after webhook_handler.py has
    # finished loading) - same "late import, avoids a circular import"
    # convention already used elsewhere in this codebase (scheduler.py,
    # db/models.py).
    from src.db.models import log_shadow_classification
    from src.tools.gemini_adapter import classify_with_tools
    from src.tools.registry import tools_for
    import src.tools  # noqa: F401 - populates the registry on first import

    new_tool = None
    new_args = None
    error = None
    try:
        tools = tools_for(user)
        result = classify_with_tools(text_body, tools)
        if result is None:
            error = "classify_with_tools returned None"
        else:
            new_tool, new_args = result
    except Exception as e:
        error = f"{type(e).__name__}: {e}"

    try:
        log_shadow_classification(
            whatsapp_message_id=whatsapp_message_id,
            raw_content=text_body,
            old_intent=old_intent,
            new_tool=new_tool,
            new_args=json.dumps(new_args, ensure_ascii=False) if new_args is not None else None,
            error=error,
        )
    except Exception as e:
        print(f"[shadow] logging the comparison itself failed (non-fatal): {e}")


def run_shadow_classification(user: dict, text_body: str, whatsapp_message_id: str, old_intent: str) -> None:
    """
    Fire-and-forget. Never raises into the caller and never blocks it - the
    actual Gemini call happens on the spawned thread, not here, so this
    returns immediately regardless of how long classification takes.
    """
    threading.Thread(
        target=_run, args=(user, text_body, whatsapp_message_id, old_intent), daemon=True
    ).start()
