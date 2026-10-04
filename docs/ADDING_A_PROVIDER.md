# AI providers: using OpenAI, and adding another one

Gemini is the default and does everything. **OpenAI** is built in as an optional second provider. Each user chooses for
themselves; the choice also applies to their proactive alerts. This page explains how it works and how to add a third
provider (Anthropic, a local model behind an OpenAI-compatible server, ...).

## Using OpenAI

1. In `.env` set **both** `OPENAI_API_KEY` and `OPENAI_MODEL` (there is deliberately no default model name: names change, and
   a stale default would fail silently). Optionally set the `OPENAI_PRICE_*` variables so the cost report can price it.
2. `python scripts/doctor.py` tells you what is missing. Restart the bot.
3. A user says *"switch to OpenAI"* (or *"עבור ל־OpenAI"*) / *"switch to Gemini"* / *"which model am I using?"*. These exact
   phrases need no model call, so they still work while the selected provider is down. A forwarded or quoted message can
   never change the setting. `AI_DEFAULT_PROVIDER` decides what users get until they choose.

What OpenAI handles: tool selection, JSON answers, image and PDF understanding, voice (transcribed first, then
classified), web search. What stays on Gemini regardless: **embeddings** (memory search) and **image generation/editing**.

**Privacy:** a user on OpenAI has their content sent to OpenAI instead of Gemini. Requests use `store: false`; that is a
request, not a zero-retention guarantee. When OpenAI is enabled, `/privacy` and the welcome message name it
automatically. There is **no silent failover**: if the chosen provider is down, the user is told and nothing is sent to the
other one.

## How it is built

- `src/ai.py` holds the registry (`PROVIDERS`), the per-request provider (a `ContextVar`, so two users handled at once
  never see each other's provider), `use_user` / `use_provider` / `@for_user`, and the chat commands.
- The four LLM entry points check `current_provider()` and hand over to that provider's adapter module:
  `call_gemini_json`, `call_gemini_json_with_media`, `search_web` (in `src/integrations/gemini.py`) and `classify_with_tools`
  (`src/tools/gemini_adapter.py`). Domain tools, confirmations and permission rules run in the same dispatcher for every
  provider; an adapter never executes an action.
- The provider is set around message handling (`_process_single_message`) and around per-user background jobs (proactive
  assessment, email classification, the nagging reminders' text). A new background job that calls the LLM **for a user**
  must do the same (`with use_user(user):` or `@for_user`) or it will silently use the default provider.
- The user's choice lives in `ai_preferences` (no CHECK constraint, so a new provider needs no migration); usage of non-Gemini
  providers goes to `ai_usage_log`.

## Adding a provider

1. Write `src/integrations/<name>.py` with four functions (see `src/integrations/openai.py` for a complete example):
   - `classify_with_tools(prompt, tools) -> (tool_name, args) | None` - **exactly one** call, from the given tools,
     with arguments validated against that tool's schema (`openai._valid_args`); anything else returns `None`;
   - `call_json(prompt) -> dict | None`;
   - `call_json_with_media(prompt, media_bytes, mime_type) -> dict | None`;
   - `search_web(query) -> {"answer": str, "sources": [{"title", "uri"}, ...]} | None`.
2. Rules the adapter must keep: no retries and no fallback to another provider; never log prompts, responses, keys or exception
   messages; ask the service not to retain the data where it has a setting for that; log token usage with
   `log_ai_usage(provider, model, ...)` so it shows in the cost report.
3. Add a `ProviderSpec` to `PROVIDERS` in `src/ai.py` (name, label, chat aliases, adapter module, `is_configured`, `model`) and the
   settings it needs to `src/config.py`, `.env.example` and `scripts/doctor.py`.
4. Make `src/legal_pages.py` (and `docs/PRIVACY_FOR_OPERATORS*.md`) name it as a recipient when it is enabled.
5. Tests: copy the shape of `tests/test_ai_providers.py` (no network; invalid tool responses are never executed; secrets never
   reach the log; the four entry points hand over; per-user isolation).
