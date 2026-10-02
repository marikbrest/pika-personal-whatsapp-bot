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
family members' numbers.

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
