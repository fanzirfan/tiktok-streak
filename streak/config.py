"""Konfigurasi streak dari environment variables.

Nilai default threshold:
- 7 hari tanpa repost  -> reminder Telegram ke diri sendiri (tiap 1 hari)
- 30 hari tanpa repost -> email SMTP ke orang ketiga (tiap 2 hari)
"""

import os


def _int(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, "") or default)
    except ValueError:
        return default


def _bool(name: str) -> bool:
    return os.environ.get(name, "").strip().lower() in ("1", "true", "yes", "y")


# --- Target ---------------------------------------------------------------
TIKTOK_USERNAME = os.environ.get("TIKTOK_USERNAME", "").lstrip("@")
STATE_FILE = os.environ.get("STATE_FILE", "state.json")

# --- Threshold (jam) ------------------------------------------------------
REMIND_AFTER_HOURS = _int("REMIND_AFTER_HOURS", 24 * 7)    # 7 hari  -> reminder
ALERT_AFTER_HOURS = _int("ALERT_AFTER_HOURS", 24 * 30)     # 30 hari -> email
REMIND_EVERY_HOURS = _int("REMIND_EVERY_HOURS", 24)        # reminder ulang tiap 1 hari
ALERT_EVERY_HOURS = _int("ALERT_EVERY_HOURS", 24 * 2)      # email ulang tiap 2 hari
# Scrape gagal berapa kali berturut sebelum dianggap "monitor blind"
SCRAPE_FAIL_LIMIT = _int("SCRAPE_FAIL_LIMIT", 8)
BLIND_WARN_EVERY_HOURS = _int("BLIND_WARN_EVERY_HOURS", 24)

# --- Telegram (reminder ke diri sendiri) ----------------------------------
TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "")

# --- SMTP (email ke orang ketiga) -----------------------------------------
SMTP_HOST = os.environ.get("SMTP_HOST", "")
SMTP_PORT = _int("SMTP_PORT", 587)
SMTP_USER = os.environ.get("SMTP_USER", "")
SMTP_APP_PASSWORD = os.environ.get("SMTP_APP_PASSWORD", "")
EMAIL_FROM = os.environ.get("EMAIL_FROM", "") or os.environ.get("SMTP_USER", "")
ALERT_EMAIL_TO = os.environ.get("ALERT_EMAIL_TO", "")

# --- Opsional: watchdog eksternal (healthchecks.io) -----------------------
HEALTHCHECK_URL = os.environ.get("HEALTHCHECK_URL", "")

# --- Mode test ------------------------------------------------------------
DRY_RUN = _bool("DRY_RUN")          # log keputusan, nggak kirim apa pun
FORCE_REMIND = _bool("FORCE_REMIND")  # tes kirim reminder langsung
FORCE_ALERT = _bool("FORCE_ALERT")    # tes kirim email alert langsung

_TEMPLATES_DIR = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "..", "templates"
)
