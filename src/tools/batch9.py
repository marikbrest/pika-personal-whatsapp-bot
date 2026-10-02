"""
Batch 9 of the function-calling migration (2026-09-14): confirm_suggestion -
the confirm/dismiss side of the "proactive suggestion from a forwarded
message" feature (see webhook_handler._suggest_action_from_forwarded for the
proposal side). Only one tool here, not four like earlier batches: this
feature is a NEW capability layered on top of the already-complete migration
(batches 1-8 covered every intent the old classifier had), not another slice
of it - so there is exactly one new tool to register, the confirmation gate
itself, following the same "conditionally offered, physically excluded
otherwise" pattern respond_to_email_draft (batch 6) already established for
pending_draft, just keyed on pending_suggestion instead.
"""
from src.tools.registry import Tool, register
from src.webhook_handler import _handle_confirm_suggestion_tool


def _validate_confirm_suggestion_args(args: dict) -> bool:
    return args.get("action") in ("confirm", "dismiss")


confirm_suggestion_tool = register(Tool(
    name="confirm_suggestion",
    description=(
        "Handles the user's reply to a PROPOSED action from something forwarded to them earlier "
        "(action=confirm to actually go ahead and do it, action=dismiss to not do it). Only usable "
        "when a suggestion is actually pending right now (the code excludes this tool otherwise, so "
        "if you are being offered it, one is pending). Do NOT use this for a brand-new request - "
        "only for a direct yes/no/never-mind reply to the specific proposal already shown."
    ),
    parameters={
        "type": "object",
        "properties": {"action": {"type": "string", "enum": ["confirm", "dismiss"]}},
        "required": ["action"],
    },
    handler=lambda user, args: _handle_confirm_suggestion_tool(user, args),
    validate=_validate_confirm_suggestion_args,
))
