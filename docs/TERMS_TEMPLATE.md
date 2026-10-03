# Terms and privacy: what to publish if you run Pika for other people

> **Not legal advice.** This is a starting point for a small, invitation-only, personal or family
> deployment. Laws differ by country. If you open the bot to the public, to customers or to an
> organization, have a lawyer review your pages.

Pika is software under the MIT license: it comes **"as is", with no warranty** (see
[LICENSE](../LICENSE)). That disclaimer covers the software between its authors and you. The moment you run it
for someone else, **you** are the service provider and the data controller, and the pages below are *your*
statements to *your* users.

| Page | URL | What it is |
| --- | --- | --- |
| Privacy policy | `https://<your-host>/privacy` | English + Hebrew, matches what the code does |
| Terms of use | `https://<your-host>/terms` | English + Hebrew |
| About | `https://<your-host>/about` | Homepage URL for Google's OAuth consent screen |

Set **`OPERATOR_NAME`** (you, as the person responsible) and **`ADMIN_CONTACT_EMAIL`** in `.env`. Until they
are set, the pages show a visible placeholder - do not hand out the link before that.

## What the pages promise, and what you must do to keep it true

| The page says | Keep it true by |
| --- | --- |
| Google content is used per Google's *Limited Use* requirements, not read by people, not used for ads | Use a **billing-enabled (paid) Gemini API project** if anyone connects Gmail/Calendar/Drive. On Google's unpaid tier, submitted content may be used to improve Google's products and reviewed by humans, which conflicts with Limited Use. |
| The operator can technically read the data | Nothing - just never tell users otherwise. |
| Users are told about a security incident | Actually tell them if the server, a backup or a key leaks. |
| Material changes are announced in the chat before they apply | Message your users before changing what the bot sends where. |
| Deletion on request; backups keep a copy "for a limited time" | Decide a backup retention period (the backup script keeps 14 local copies by default) and honor deletion requests, including removing a contact's number on request. |
| Children use it only with a parent's consent | Only register kids' numbers with their parent's agreement. |

## Before you invite anyone

1. Read [PRIVACY_FOR_OPERATORS.md](./PRIVACY_FOR_OPERATORS.md) and tell people, in plain language, what leaves
   your machine (Gemini, Meta) and that **you can technically read the database**.
2. Send them the `/privacy` and `/terms` links, and only add people who agreed.
3. If you submit the Google OAuth app for verification with Gmail scopes, Google may require a security assessment
   for "restricted" scopes once the app is used beyond a small number of users. Staying in a small private group
   avoids most of this; opening it up does not.
4. Personal or household use is exempt from some data-protection duties in some places (for example under GDPR),
   but once you serve people outside your household, assume the law applies to you. In Israel that is the Privacy
   Protection Law, 1981, including Amendment 13.
5. If you change what the bot sends where, update `src/legal_pages.py` and `LEGAL_PAGES_UPDATED`. A test
   (`tests/test_legal_pages.py`) guards the key statements.

Questions about these templates: [marik.brest@gmail.com](mailto:marik.brest@gmail.com) (the project author - not a lawyer, and not the operator of your bot).
