"""
Loads configuration from .env
"""
import os
from dotenv import load_dotenv

load_dotenv()

WHATSAPP_ACCESS_TOKEN = os.getenv("WHATSAPP_ACCESS_TOKEN")
WHATSAPP_PHONE_NUMBER_ID = os.getenv("WHATSAPP_PHONE_NUMBER_ID")
WHATSAPP_WEBHOOK_VERIFY_TOKEN = os.getenv("WHATSAPP_WEBHOOK_VERIFY_TOKEN")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
GOOGLE_CLIENT_ID = os.getenv("GOOGLE_CLIENT_ID")
GOOGLE_CLIENT_SECRET = os.getenv("GOOGLE_CLIENT_SECRET")
GOOGLE_REDIRECT_URI = os.getenv("GOOGLE_REDIRECT_URI", "https://your-domain.example/oauth/callback")
DB_PATH = os.getenv("DB_PATH", "./data/assistant.db")
# Regional defaults - set these if you are not in Israel. DEFAULT_TIMEZONE is given to new
# users and used for the daily jobs' wall-clock times; DEFAULT_LOCATION is the city used when
# someone asks for the weather without naming one.
DEFAULT_TIMEZONE = os.getenv("DEFAULT_TIMEZONE", "Asia/Jerusalem")
DEFAULT_LOCATION = os.getenv("DEFAULT_LOCATION", "Tel Aviv")
# Language code your WhatsApp message templates were approved in (e.g. "he", "en", "en_US").
WHATSAPP_TEMPLATE_LANGUAGE = os.getenv("WHATSAPP_TEMPLATE_LANGUAGE", "he")
ADMIN_HOST = os.getenv("ADMIN_HOST", "admin.your-domain.example")
ADMIN_ALLOWED_EMAIL = os.getenv("ADMIN_ALLOWED_EMAIL", "")
# Optional but recommended: verify the signed Cloudflare Access JWT instead of trusting the
# Cf-Access-Authenticated-User-Email header. TEAM_DOMAIN looks like "myteam.cloudflareaccess.com";
# AUD is the Application Audience tag of the Access application protecting ADMIN_HOST.
CF_ACCESS_TEAM_DOMAIN = os.getenv("CF_ACCESS_TEAM_DOMAIN", "").strip().removeprefix("https://").rstrip("/")
CF_ACCESS_AUD = os.getenv("CF_ACCESS_AUD", "").strip()
# Shown on the /about, /privacy and /terms pages (see src/legal_pages.py) - the Google
# OAuth consent screen requires a privacy page with a real contact address.
ADMIN_CONTACT_EMAIL = os.getenv("ADMIN_CONTACT_EMAIL", "")
# Who runs this instance (the data controller), named on /privacy and /terms.
OPERATOR_NAME = os.getenv("OPERATOR_NAME", "").strip()
# Public https address of this bot (no trailing slash), e.g. https://assistant.example.com. Used to link new users to
# /privacy and /terms in the welcome message; leave empty to skip that message.
PUBLIC_BASE_URL = os.getenv("PUBLIC_BASE_URL", "").strip().rstrip("/")
WHATSAPP_APP_SECRET = os.getenv("WHATSAPP_APP_SECRET")
TOKEN_ENCRYPTION_KEY = os.getenv("TOKEN_ENCRYPTION_KEY")
ZABBIX_API_URL = os.getenv("ZABBIX_API_URL", "http://localhost:8081/api_jsonrpc.php")
ZABBIX_API_TOKEN = os.getenv("ZABBIX_API_TOKEN")
UNIFI_HOST = os.getenv("UNIFI_HOST", "https://192.168.1.1")
UNIFI_API_KEY = os.getenv("UNIFI_API_KEY")
SHIP24_API_KEY = os.getenv("SHIP24_API_KEY")
FIREWALLA_MSP_DOMAIN = os.getenv("FIREWALLA_MSP_DOMAIN")
FIREWALLA_API_TOKEN = os.getenv("FIREWALLA_API_TOKEN")
# Real Google Cloud billing (2026-09-14) - separate from GEMINI_API_KEY's own
# project by nature (see src/integrations/gcp_billing.py's module docstring
# for why this is its own service account, not reused from anywhere else).
GCP_BILLING_SA_KEY_PATH = os.getenv("GCP_BILLING_SA_KEY_PATH")
GCP_BILLING_PROJECT_ID = os.getenv("GCP_BILLING_PROJECT_ID")
GCP_BILLING_DATASET = os.getenv("GCP_BILLING_DATASET")
# Cost-guard (see scheduler.check_and_send_cost_report): the periodic report
# and the budget-crossed alert both go ONLY to this number (the admin's
# own), never to every admin the way e.g. google_token_health does -
# explicitly a narrower audience than "every admin" on purpose, so a future
# second admin doesn't start getting these too without being asked.
OWNER_WHATSAPP_NUMBER = os.getenv("OWNER_WHATSAPP_NUMBER")
COST_ALERT_BUDGET_USD = float(os.getenv("COST_ALERT_BUDGET_USD", "15"))
