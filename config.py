"""Configuration for Weco Reachout Bot."""
import os
from dotenv import load_dotenv

load_dotenv()


def _clean(name: str, default: str = "") -> str:
    return (os.getenv(name, default) or default).strip()


# Slack
SLACK_BOT_TOKEN = _clean("SLACK_BOT_TOKEN")
SLACK_SIGNING_SECRET = _clean("SLACK_SIGNING_SECRET")
VAYUM_SLACK_USER_ID = _clean("VAYUM_SLACK_USER_ID")

# LLM (LiteLLM proxy, OpenAI-compatible)
LLM_API_KEY = _clean("LLM_API_KEY")
LLM_BASE_URL = _clean("LLM_BASE_URL", "https://litellm-research.weco-ai.com")
LLM_MODEL = _clean("LLM_MODEL", "claude-sonnet-4")

# Notion
NOTION_API_KEY = _clean("NOTION_API_KEY")
NOTION_USER_ACTIVITY_DB_ID = _clean(
    "NOTION_USER_ACTIVITY_DB_ID", "3232e4b52eba44079d3a8d46705e5cd8"
)

# Supabase
SUPABASE_URL = _clean("SUPABASE_URL")
SUPABASE_KEY = _clean("SUPABASE_KEY")

# Emojis that trigger a reachout draft on reaction_added.
# Comma-separated list. Defaults cover the common envelope/email reactions
# users reach for: ✉️ (envelope), 📧 (e-mail), 📩 (envelope_with_arrow), 📨 (incoming_envelope).
TRIGGER_EMOJIS = {
    e.strip()
    for e in _clean(
        "TRIGGER_EMOJIS",
        "envelope,e-mail,email,envelope_with_arrow,incoming_envelope,love_letter",
    ).split(",")
    if e.strip()
}

# Sender identity baked into the email
SENDER_NAME = _clean("SENDER_NAME", "Vayum")

# Server
PORT = int(os.getenv("PORT", "8080"))
HOST = os.getenv("HOST", "0.0.0.0")
