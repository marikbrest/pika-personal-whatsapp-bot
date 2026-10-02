## What and why

## How I tested it
- [ ] `pytest -q` passes
- [ ] New behaviour has a test (bug fixes: a regression test)
- [ ] Tried it with `python scripts/chat.py` (if user-facing)

## Checklist
- [ ] No real phone numbers, names, tokens or emails in code, tests or fixtures
- [ ] New tool? Added to `src/tools/__init__.py`, `_CAPABILITY_GROUPS` and `tests/test_tool_registry.py` (see docs/ADDING_A_TOOL.md)
- [ ] Anything the bot sends unprompted goes through `src/proactive.py`
