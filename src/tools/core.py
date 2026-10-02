"""
chat/unclear - registered alongside every domain tool, not optional add-ons.
ANY mode forces Gemini to pick one of whatever tools it's given on every
single call; without a legitimate "none of the specific tools fit" option,
ordinary small talk would get force-fit into whichever domain tool happens
to be registered, which is exactly the kind of silent misclassification this
whole migration exists to catch, not reproduce. Mirrors intent_parser.py
treating "chat"/"unclear" as explicit categories in its whitelist today, not
a fallback state.
"""
from src.intent_parser import FALLBACK_REPLY
from src.tools.registry import Tool, register

chat_tool = register(Tool(
    name="chat",
    description=(
        "General conversation, small talk, a question about the assistant itself, or "
        "anything that is not a request matching any other specific tool."
    ),
    parameters={
        "type": "object",
        "properties": {
            "reply_text": {
                "type": "string",
                "description": "A natural, helpful reply in the SAME language the user wrote in.",
            },
        },
        "required": ["reply_text"],
    },
    handler=lambda user, args: None,  # no side effect; reply comes from reply_text
    renders_own_reply=False,
))

unclear_tool = register(Tool(
    name="unclear",
    description=(
        "The message could not be understood or matched to any tool, even considering "
        "the whole conversation so far. Use only when genuinely nothing else fits."
    ),
    parameters={"type": "object", "properties": {}},
    handler=lambda user, args: FALLBACK_REPLY,
))
