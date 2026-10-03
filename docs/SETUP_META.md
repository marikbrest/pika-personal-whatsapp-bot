# Setting up WhatsApp (Meta Cloud API)

This is the longest part of installing Pika. Meta renames menus fairly often, so treat the
names below as landmarks rather than exact labels. Official docs:
<https://developers.facebook.com/docs/whatsapp/cloud-api/get-started>

You need a Facebook account and a phone number that is *not* already registered with the
regular WhatsApp app (a test number provided by Meta works for development).

## 1. Create the app

1. Go to <https://developers.facebook.com/apps/> -> **Create app**.
2. Choose the **Business** type (use case "Other" / "Connect with customers through WhatsApp").
3. Create or pick a **Business portfolio** when asked.
4. In the app dashboard, add the **WhatsApp** product.

## 2. Get the three values for `.env`

In **WhatsApp -> API Setup**:

| `.env` variable | Where |
| --- | --- |
| `WHATSAPP_PHONE_NUMBER_ID` | "Phone number ID" under the *From* number (not the phone number itself) |
| `WHATSAPP_ACCESS_TOKEN` | the temporary token on that page (valid ~24h - fine for a first test) |
| `WHATSAPP_APP_SECRET` | **App settings -> Basic -> App secret** (click *Show*) |

`WHATSAPP_WEBHOOK_VERIFY_TOKEN` is not given by Meta - invent any random string; you type
the same string into Meta in step 4.

While in development you can only message numbers you add under **API Setup -> To -> Manage
phone number list** (each gets a one-time confirmation code). Add your own number and the
family members' numbers. (Details and troubleshooting: [Adding a person](#adding-a-person-two-separate-approvals).)

## 3. Start the bot and expose it

Run the bot (Docker or Python - see the main README) and give it a public HTTPS URL, for
example with a Cloudflare Tunnel pointing at `http://localhost:8000`. Then check everything
from your machine:

```bash
python scripts/doctor.py --online --url https://assistant.example.com
```

## 4. Register the webhook

**WhatsApp -> Configuration -> Webhook -> Edit**:

- **Callback URL**: `https://assistant.example.com/webhook`
- **Verify token**: the string you chose for `WHATSAPP_WEBHOOK_VERIFY_TOKEN`

Click **Verify and save**. If Meta rejects it, `doctor.py --url ...` tells you why (almost always:
the bot is not reachable, or it was started before you edited `.env`). Then, under *Webhook
fields*, **subscribe to `messages`**.

## 5. First message

Create yourself as admin and send a message to the bot's number:

```bash
python scripts/create_admin.py 972501234567 "Your Name"
```

Messages from numbers that are not users are silently ignored by design.

## Adding a person: two separate approvals

A new person needs to be approved in **two independent places**. Missing either one looks the same
from their side: the bot never answers.

| Layer | What it is | How |
| --- | --- | --- |
| **1. Meta** | Meta only lets a WhatsApp number *in development mode* receive messages from your bot if it is on your recipient list. | **WhatsApp -> API Setup -> step 1 "Send messages" -> To -> Manage phone number list -> Add phone number.** Meta sends that person a one-time code on WhatsApp; they give it to you and you enter it. Meta limits how many recipients a test setup may have (currently 5). |
| **2. The bot** | The bot's own allowlist. A message from any number that is not a user is silently ignored. | Either say to the bot (as admin) *"add user: Dad, 0501234567"*, or use the admin dashboard, or run `python scripts/create_admin.py <number> "<name>"` for another admin. |

Once your app is **live** (App mode switched to *Live*, with your own verified phone number from step 6), layer 1
no longer applies: anyone can message the bot, and only layer 2 decides who gets an answer.

**Number format** is the same everywhere: international, digits only, no `+`, no spaces or dashes
(`972501234567`, not `050-1234567` or `+972 50 123 4567`).

### If someone gets no answer

| Symptom | Likely cause | Fix |
| --- | --- | --- |
| Nothing at all, the bot never reacts | They are not a user in the bot (layer 2) | Add them, then ask them to write again; check the bot's log (`docker compose logs bot`, or `logs/uvicorn.log` on Windows) to see whether the message arrived at all |
| Meta shows an error like "recipient not in allowed list" (code `131030`) when the bot replies | Development mode and they are not on Meta's recipient list (layer 1) | Add them under *Manage phone number list* |
| They never received the confirmation code | Wrong number, or they have not opened WhatsApp on that number recently | Re-send from Meta; try the number in international format |
| The code was entered but they still get nothing | The number was added in Meta but not in the bot, or the other way round | Check both layers |
| Works for a day, then stops for replies you send first | The 24-hour window closed; you need a template for proactive messages | See the template table in the main README |

> Meta renames these menus often and the limits (for example the number of test recipients) change.
> The menu names above are from memory, not checked against the live interface on every release. If
> something does not match, the official documentation is at
> <https://developers.facebook.com/docs/whatsapp/cloud-api/get-started>.

## 6. Going to production

- **Permanent token**: the temporary token expires. In **Business settings -> Users -> System
  users** create a system user, give it your app with *Full control*, and generate a token with
  the `whatsapp_business_messaging` and `whatsapp_business_management` permissions. Put it in
  `WHATSAPP_ACCESS_TOKEN`.
- **Your own number**: add and verify a real number under **WhatsApp -> API Setup -> Add phone
  number**. Anyone can then message it without being on a test list.
- **Business verification** may be required by Meta to raise limits or use some features.
- **Message templates** (needed for anything the bot sends more than 24 hours after the user's
  last message: reminders, alerts, proactive updates) - see the template table in the main README.
  Create them in **WhatsApp Manager -> Message templates**; approval usually takes minutes to a
  day. Keep the wording plainly transactional so Meta categorises them as *Utility*.
