# Roadmap

What would make Pika better, roughly in priority order. Pick something and say so in the
issue; see [CONTRIBUTING.md](./CONTRIBUTING.md) and [docs/ADDING_A_TOOL.md](./docs/ADDING_A_TOOL.md).

## Wanted (open to contributors)

- **English (and other-language) replies.** The bot understands English, but the messages the code
  itself composes (confirmations, lists, errors) are Hebrew string literals. Extract them into
  per-language catalogs with a `LOCALE` setting. Biggest barrier for non-Hebrew users.
- **Other LLM providers.** Gemini is called through a thin layer
  (`src/integrations/gemini.py`, `src/tools/gemini_adapter.py`); abstract it so OpenAI/Anthropic/local
  models can be swapped in.
- **Other channels.** WhatsApp specifics live in `src/integrations/whatsapp.py` and the webhook;
  a Telegram (or Signal/Matrix) adapter would reuse the whole tool pipeline.
- **Deployment recipes:** a systemd unit, a Caddy/Traefik compose example as an alternative to
  Cloudflare Tunnel, a Kubernetes manifest.
- **Real screenshots** of the setup flow for `docs/SETUP_META.md`.
- **Type hints and `mypy`** on the core modules.
- **Test coverage report** in CI.

## Ideas (needs discussion first)

- iOS Shortcuts / Focus-mode signals feeding the proactive "busy" status.
- Trip and event preparation (flight detection, destination weather, packing doc).
- Dockerised optional integrations (Zabbix/UniFi examples) behind compose profiles.

## Done

See the [changelog](./CHANGELOG.md).
