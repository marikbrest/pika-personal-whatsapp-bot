"""
Stage A of the function-calling migration (see the 2026-09-13 session for the
full plan): a provider-neutral tool registry. Nothing here imports from
google.genai - src/tools/gemini_adapter.py is the only file that translates
this into what the Gemini SDK wants, so a future provider swap or an MCP
export only ever touches that one file.

Two verified-live design decisions this registry encodes (see
smoke_function_call.py / smoke_reply_arg.py from the same session):

1. Gemini's function-calling mode is ANY, not AUTO - every message maps to
   exactly one of N named tools, always (chat and unclear are real,
   registered tools, not "the model declined to call anything"). This is
   the closest match to today's intent_parser.py, which already treats
   "chat"/"unclear" as explicit categories in an exhaustive whitelist, not a
   fallback state.

2. In ANY mode, Gemini does NOT emit free text alongside a function call
   (response.text comes back None - confirmed empirically). Today, ~5 of 20
   intents (reminder, add_contact, connect_google, email_draft) rely on
   Gemini's own generated sentence AS the reply, not a code-computed one.
   Rather than a second Gemini round-trip to get that text (which today's
   single-call design never needed), renders_own_reply=False tools simply
   carry a "reply_text" argument as part of their OWN schema, filled by
   Gemini in the same call as every other argument - verified this produces
   natural, correctly-language-matched text exactly like today's
   result["reply"] does. No tool registered so far needs more than one
   Gemini call; the step-loop in gemini_adapter.py exists for a FUTURE tool
   that genuinely needs to see its own result before responding (e.g. "read
   this email thread, now summarize it"), not because today's 20 intents do.
"""
from dataclasses import dataclass
from typing import Callable


@dataclass(frozen=True)
class Tool:
    name: str
    description: str
    # Plain JSON Schema (type: object, properties, required) - passed
    # straight through to FunctionDeclaration(parameters_json_schema=...),
    # which the installed SDK (google-genai 2.8.0) accepts natively; no
    # hand-written Schema-object conversion needed (confirmed via
    # inspect_sdk.py against the real installed package, not guessed from
    # docs).
    parameters: dict
    # (user: dict, args: dict) -> str | None
    #   renders_own_reply=True  - the return value IS the reply sent to the user.
    #   renders_own_reply=False - the return value is ignored (or None); the
    #     reply is args["reply_text"], which "required" below enforces exists.
    handler: Callable[[dict, dict], "str | None"]
    renders_own_reply: bool = True
    admin_only: bool = False
    # Optional (args: dict) -> bool, for the "required only when action==X"
    # shape of rule intent_parser._validate_result already hand-codes per
    # intent today (e.g. task_manage: content required only for action="add")
    # - plain JSON Schema's flat "required" list cannot express that
    # conditional, and the installed Gemini Schema type has no if/then/else
    # composition (confirmed via inspect_sdk.py). A tool with no real
    # conditional rules just omits this. False => execute_tool falls back to
    # FALLBACK_REPLY without ever calling handler - same "ask again rather
    # than guess" principle as today's _validate_result.
    validate: "Callable[[dict], bool] | None" = None

    def __post_init__(self):
        if not self.renders_own_reply:
            required = self.parameters.get("required", [])
            assert "reply_text" in required, (
                f"tool {self.name!r} has renders_own_reply=False but its schema does not "
                f"require 'reply_text' - Gemini would have nowhere reliable to put the "
                f"user-facing sentence. Add a 'reply_text' property and list it in 'required'."
            )


_REGISTRY: dict[str, Tool] = {}


def register(tool: Tool) -> Tool:
    """Adds a tool to the global registry. Returns the tool itself so this
    can be used as `weather_tool = register(Tool(...))` at module load time -
    every tools/*.py module calls this once per tool it defines, and
    importing that module is what makes the tool exist; see tools_for()."""
    if tool.name in _REGISTRY:
        raise ValueError(f"tool {tool.name!r} is already registered")
    _REGISTRY[tool.name] = tool
    return tool


def all_tools() -> list[Tool]:
    return list(_REGISTRY.values())


def get_tool(name: str) -> Tool | None:
    return _REGISTRY.get(name)


def tools_for(user: dict) -> list[Tool]:
    """
    The subset of tools a given user's message may be classified against.
    Excluding an admin-only tool here means Gemini physically cannot choose
    it for a non-admin user - real defence, not just a token-count
    optimization - but it is a SECOND layer, not a replacement for the
    handler's own is_admin check: a bug here (or a future tool registered
    without going through tools_for) must not become a privilege
    escalation. Handlers keep checking admin status themselves, exactly as
    _handle_zabbix_status/_handle_usage_status/_handle_user_manage already
    do today.

    user is `.get()`-accessed via dict(user), not user.get(...) directly:
    real production callers pass a sqlite3.Row (from
    get_user_by_whatsapp_number), which supports row["key"] but has no
    .get() method at all - AttributeError, not a wrong answer. This was
    latent since Stage A (every test here used a plain dict) and only
    surfaced once batch 5 registered the first admin_only=True tool, which
    finally made the right-hand side of the `or` actually evaluate for a
    real webhook-driven user instead of always short-circuiting on
    `not t.admin_only` first. dict(row) works for both a sqlite3.Row and an
    already-plain dict, so this fix does not change behaviour for any
    existing caller.
    """
    is_admin = dict(user).get("is_admin")
    return [t for t in all_tools() if not t.admin_only or is_admin]
