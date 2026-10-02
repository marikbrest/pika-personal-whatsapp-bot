# What does it cost to run?

Short version: **a few dollars a month** for a small family, almost all of it Gemini tokens.
Everything else is free or something you already own.

## Real numbers

From the author's own instance (6 active users, ~290 messages in the last 30 days, proactive
mode on for one user part of the time, plus a lot of development testing, so treat it as an
upper-ish bound for a family this size):

| Provider (30 days) | Calls | Tokens | Estimated cost* |
| --- | ---: | ---: | ---: |
| Gemini text generation | 809 | 3.52M in / 0.10M out | ~$3.0 |
| Gemini embeddings (semantic search) | 485 | 7K in | < $0.01 |
| Ship24 package tracking | 22 | – | free tier |

\* using the per-million-token prices configured in `src/webhook_handler.py`
(`_GEMINI_GENERATE_PRICE_PER_M`, `_GEMINI_EMBED_PRICE_PER_M`). **Prices change — check Google's
current rate card** and update those constants; the bot's usage report uses them.

A typical call sends ~4.3K input tokens: your message, the last few messages for context, your saved
contacts/facts, and the tool list. That context is why input tokens dominate.

## Everything else

| Item | Cost |
| --- | --- |
| WhatsApp Cloud API | Free to receive and to reply within 24h of the user's last message. Template messages outside that window are billed per message by Meta (see their current rate card; for reference, 11 *Utility* reminder templates cost the author about $0.05). *Marketing*-categorised templates cost more. |
| Google Calendar / Gmail / Drive APIs | Free within normal quotas |
| Weather (Open-Meteo), market quotes (Yahoo) | Free, no key |
| Cloudflare Tunnel | Free |
| Hosting | The machine you already run it on; it needs ~one small always-on process and one SQLite file |
| Real billing export (optional) | BigQuery storage for the billing export is pennies |

## Keeping an eye on it

- Ask the bot *"how much have I spent?"* for a month-to-date report.
- The **cost guard** (`OWNER_WHATSAPP_NUMBER`, `COST_ALERT_BUDGET_USD`, default $15) sends a report
  every two days and an immediate alert if the month crosses the budget — to the owner only.
- Proactive mode adds one small Gemini call per detected calendar/email event, not per poll.
