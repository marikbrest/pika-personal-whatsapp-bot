# Adding a capability (a "tool")

Every thing Pika can do is a **tool**: a name, a description Gemini reads to decide when
to use it, a JSON schema for its arguments, and a Python handler. Gemini is forced to
pick exactly one tool per message (`chat` and `unclear` are tools too), so adding a
capability means adding a tool - there is no separate intent list to update.

## 1. Write the handler

Handlers take `(user, args)` and return the reply text. `user` is the sender's database
row (use `user["id"]`, `user["timezone"]`, `user["display_name"]`); `args` is whatever the
model filled in from your schema. Keep the real work in a function in
`src/webhook_handler.py` (or an integration under `src/integrations/`) so it can be
unit-tested without Gemini.

```python
def _handle_coin_flip(user: dict, args: dict) -> str:
    import random
    return "Heads" if random.random() < 0.5 else "Tails"
```

## 2. Register the tool

Create `src/tools/my_tools.py` (or add to an existing module):

```python
from src.tools.registry import Tool, register
from src.webhook_handler import _handle_coin_flip

coin_flip_tool = register(Tool(
    name="coin_flip",
    description=(
        "Flips a coin. Use for 'flip a coin', 'heads or tails', 'תטיל מטבע'. "
        "Do NOT use for choosing between named options."
    ),
    parameters={"type": "object", "properties": {}},
    handler=lambda user, args: _handle_coin_flip(user, args),
))
```

Notes that matter:

- **The description is the prompt.** Include example phrasings (in the languages your users
  speak) *and* an explicit "Do NOT use for ..." line pointing at neighbouring tools - most
  misrouting bugs are two descriptions overlapping.
- `parameters` is plain JSON Schema (`type: object`, `properties`, `required`).
- If an argument is only required for some actions, pass `validate=lambda args: ...`
  returning `False` to make the bot ask again instead of guessing.
- `admin_only=True` hides the tool from non-admin users entirely. Still check
  `user["is_admin"]` inside the handler - tool filtering is a second layer, not the only one.
- If the handler cannot produce the reply itself, set `renders_own_reply=False` and require a
  `reply_text` argument in the schema; Gemini fills it in the same call.

## 3. Wire it in

1. Add `from src.tools import my_tools  # noqa: F401` to `src/tools/__init__.py`. Forgetting this
   makes the tool silently invisible.
2. Add it to `_CAPABILITY_GROUPS` in `src/webhook_handler.py` with a one-line user-facing
   description. This feeds *"what can you do?"* and the fallback classifier's capability list -
   a tool missing here is never mentioned to users and can be wrongly denied as unsupported.
3. Add the name to the expected set in `tests/test_tool_registry.py`.

## 4. Test it

Unit-test the handler directly (see `tests/test_tool_batch*.py` for the pattern, with the
`db_path` and `make_user` fixtures from `tests/conftest.py`). External services must be
mocked - the suite never touches the network. Then try it end to end without WhatsApp:

```bash
python scripts/chat.py
you> flip a coin
```

## 5. Proactive output?

Anything the bot sends *unprompted* must go through `src/proactive.py`
(`assess_and_deliver`), never straight to `send_text_message`, so quiet hours, the daily
cap and the opt-in setting are respected.
