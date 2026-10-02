"""
New feature (2026-09-26): image generation/editing, plus a generic
"ask the developer" escape hatch for unsupported requests. the admin asked for
three things: (1) "create me an image of..." support, (2) editing a photo
the user sends, (3) when asked for something the bot cannot do, offer to
send the developer a request instead of just saying no.

edit_image follows the exact same "conditionally offered, physically
excluded otherwise" pattern respond_to_email_draft (batch 6) and
confirm_suggestion (batch 9) already established - see
webhook_handler._classify_text_with_cutover's pending_image_upload
threading. send_feature_request_to_developer is NOT gated the same way:
it is reached either directly (a user explicitly asking to forward a
request) or via confirm_suggestion's already-existing, already-tested
generic dispatch (see webhook_handler._process_single_message's
feature_request_offer hook, right after the "chat" result is resolved) -
no new gated tool or table was needed for that confirm step, since
pending_suggestions/confirm_suggestion already do exactly this for any
tool+args pair, not just ones proposed from a forwarded message.
"""
from src.tools.registry import Tool, register
from src.webhook_handler import _handle_edit_image, _handle_generate_image, _handle_send_feature_request

generate_image_tool = register(Tool(
    name="generate_image",
    description=(
        "Generates a brand-new image from a text description - use for any request to draw/create/"
        "generate a picture/image/drawing, in any language ('צור לי תמונה של...', 'תצייר לי...', "
        "'generate an image of a...'). Do NOT use this to modify a photo the user already sent (use "
        "edit_image for that, when offered)."
    ),
    parameters={
        "type": "object",
        "properties": {
            "prompt": {
                "type": "string",
                "description": (
                    "A detailed, vivid, English-language image-generation prompt, expanded from the "
                    "user's own request (translate if it wasn't in English) - include style, subject, "
                    "setting and mood as implied by what they asked for."
                ),
            },
        },
        "required": ["prompt"],
    },
    handler=lambda user, args: _handle_generate_image(user, args),
))

edit_image_tool = register(Tool(
    name="edit_image",
    description=(
        "Edits the photo the user just sent (or sent a few minutes ago) per their instructions - e.g. "
        "'תהפוך את זה לשחור-לבן', 'תוסיף לי כובע', 'תמחק את הרקע', 'תהפוך אותה לציור'. Only usable "
        "when a recent photo upload is actually available (the code excludes this tool otherwise, so "
        "if you are being offered it, one is available). Do NOT use this for a general question about "
        "the photo's content, and do NOT use this to create a brand-new image from scratch (use "
        "generate_image for that)."
    ),
    parameters={
        "type": "object",
        "properties": {
            "instructions": {
                "type": "string",
                "description": "A clear, specific description of what to change about the image.",
            },
        },
        "required": ["instructions"],
    },
    handler=lambda user, args: _handle_edit_image(user, args),
))

send_feature_request_to_developer_tool = register(Tool(
    name="send_feature_request_to_developer",
    description=(
        "Sends a note to the bot's developer about a capability the user wants that does not exist "
        "yet. Use this either when the user directly asks you to pass along a feature request/bug "
        "report ('תעביר למפתח בקשה ש...', 'תגיד למי שבנה אותך ש...'), OR when confirming yes to an "
        "offer already made in a previous reply to do exactly that after explaining something is not "
        "supported. Do NOT use this to actually attempt the unsupported action itself, and do NOT use "
        "it for ordinary feedback/chat with no actual request attached."
    ),
    parameters={
        "type": "object",
        "properties": {
            "request_text": {
                "type": "string",
                "description": (
                    "A short, clear description of the requested capability - in the user's own words "
                    "or translated, whichever best conveys what they actually want."
                ),
            },
        },
        "required": ["request_text"],
    },
    handler=lambda user, args: _handle_send_feature_request(user, args),
))
