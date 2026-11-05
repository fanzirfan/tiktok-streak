"""Pengirim notifikasi: Telegram Bot API + SMTP email.

Stdlib-only. Semua fungsi melempar Exception kalau gagal — pemanggil
(check.py) yang mutusin lanjut atau nggak per channel.
"""

import json
import smtplib
import urllib.parse
import urllib.request
from email.header import Header
from email.mime.text import MIMEText

TIMEOUT = 30


def send_telegram(bot_token: str, chat_id: str, text: str) -> None:
    if not bot_token or not chat_id:
        raise RuntimeError("TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID belum diset")
    url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
    payload = json.dumps(
        {"chat_id": chat_id, "text": text, "disable_web_page_preview": True}
    ).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
        body = json.loads(resp.read().decode("utf-8"))
    if not body.get("ok"):
        raise RuntimeError(f"Telegram API nggak ok: {body}")


def send_email(
    host: str,
    port: int,
    user: str,
    password: str,
    from_addr: str,
    to_addr: str,
    subject: str,
    body: str,
) -> None:
    if not (host and user and password and from_addr and to_addr):
        raise RuntimeError(
            "konfigurasi SMTP belum lengkap "
            "(SMTP_HOST/SMTP_USER/SMTP_APP_PASSWORD/EMAIL_FROM/ALERT_EMAIL_TO)"
        )
    msg = MIMEText(body, "plain", "utf-8")
    msg["From"] = from_addr
    msg["To"] = to_addr
    msg["Subject"] = Header(subject, "utf-8")

    if port == 465:
        server = smtplib.SMTP_SSL(host, port, timeout=TIMEOUT)
    else:
        server = smtplib.SMTP(host, port, timeout=TIMEOUT)
        server.starttls()
    try:
        server.login(user, password)
        server.sendmail(from_addr, [to_addr], msg.as_string())
    finally:
        server.quit()


def ping_healthcheck(url: str, ok: bool = True) -> None:
    """Ping healthchecks.io (atau kompatibel). ok=False -> endpoint /fail."""
    if not url:
        return
    if not ok:
        url = url.rstrip("/") + "/fail"
    req = urllib.request.Request(url, data=b"", method="POST")
    urllib.request.urlopen(req, timeout=TIMEOUT)
