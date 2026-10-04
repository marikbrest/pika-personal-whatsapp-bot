# Roadmap

What would make Pika better, roughly in priority order. Pick something and say so in the
issue; see [CONTRIBUTING.md](./CONTRIBUTING.md) and [docs/ADDING_A_TOOL.md](./docs/ADDING_A_TOOL.md).

## Wanted (open to contributors)

- **English (and other-language) replies.** The bot understands English, but the messages the code
  itself composes (confirmations, lists, errors) are Hebrew string literals. Extract them into
  per-language catalogs with a `LOCALE` setting. Biggest barrier for non-Hebrew users.
- **More LLM providers.** OpenAI is built in as an optional per-user provider
  ([docs/ADDING_A_PROVIDER.md](./docs/ADDING_A_PROVIDER.md)); Anthropic or a local OpenAI-compatible server would each be one adapter
  module plus a registry entry.
- **Other channels.** WhatsApp specifics live in `src/integrations/whatsapp.py` and the webhook;
  a Telegram (or Signal/Matrix) adapter would reuse the whole tool pipeline.
- **Deployment recipes:** systemd and Caddy are done (`docs/DEPLOY.md`); a Traefik example and a Kubernetes manifest are open.
- **Welcome message for new users** with the privacy policy and terms links ([#17](https://github.com/marikbrest/pika-personal-whatsapp-bot/issues/17)).
- **`scripts/delete_user.py`**: remove a user and all their data in one step ([#18](https://github.com/marikbrest/pika-personal-whatsapp-bot/issues/18)).
- **Real screenshots** of the setup flow for `docs/SETUP_META.md`.
- **Type hints and `mypy`** on the core modules.
- **Test coverage report** in CI.

## Ideas (needs discussion first)

- iOS Shortcuts / Focus-mode signals feeding the proactive "busy" status.
- Trip and event preparation (flight detection, destination weather, packing doc).
- Dockerised optional integrations (Zabbix/UniFi examples) behind compose profiles.

## Done

See the [changelog](./CHANGELOG.md).
