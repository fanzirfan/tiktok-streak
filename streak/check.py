"""Orchestrator streak tracker.

Alur tiap run (dipanggil GitHub Actions tiap 6 jam):
1. Scrape timestamp repost terakhir di profil TikTok publik.
2. Bandingkan dengan state.json:
   - check-in baru terdeteksi -> reset eskalasi + kirim "all clear"
     (kalau sebelumnya lagi reminder/alert)
   - >= REMIND_AFTER (24h)    -> reminder Telegram ke diri sendiri
   - >= ALERT_AFTER (48h)     -> email SMTP ke orang ketiga
   - scrape gagal berkali-kali-> warning "monitor blind" via Telegram
3. Simpan state + ping healthcheck (opsional).

Catatan desain penting: kegagalan scrape TIDAK dianggap "tidak check-in".
Monitor yang buta hanya men-trigger warning ke diri sendiri, tidak pernah
mengirim email ke pihak ketiga tanpa bukti staleness nyata.
"""

import json
import os
import sys
import time
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import config as cfg  # noqa: E402
import notify  # noqa: E402
from scraper_tiktok import ScrapeError, fetch_activity  # noqa: E402

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def log(msg: str) -> None:
    stamp = datetime.now(timezone.utc).isoformat(timespec="seconds")
    print(f"[streak {stamp}] {msg}", flush=True)


# --- State -----------------------------------------------------------------

def state_path() -> str:
    if os.path.isabs(cfg.STATE_FILE):
        return cfg.STATE_FILE
    return os.path.join(REPO_ROOT, cfg.STATE_FILE)


def load_state(path: str) -> dict:
    if os.path.exists(path):
        try:
            with open(path, encoding="utf-8") as f:
                return json.load(f)
        except (json.JSONDecodeError, OSError) as e:
            log(f"state.json nggak bisa dibaca ({e}) — mulai baseline baru")
    now = int(time.time())
    # Baseline first-run: repost dianggap "baru saja" supaya nggak ada
    # alert instan sebelum sistem pernah lihat kondisi asli sekali.
    return {
        "last_seen_ts": now,
        "last_top_ids": None,       # baseline diisi run pertama jalur tikwm
        "consecutive_failures": 0,
        "last_remind_ts": None,
        "last_alert_ts": None,
        "last_blind_warn_ts": None,
        "alerting": False,
        "last_update_ts": now,
    }


def save_state(path: str, state: dict) -> None:
    state["last_update_ts"] = int(time.time())
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(state, f, indent=2, ensure_ascii=False)
        f.write("\n")
    os.replace(tmp, path)


# --- Templates -------------------------------------------------------------

def _subs(state: dict, stale_hours: float) -> dict:
    return {
        "username": cfg.TIKTOK_USERNAME,
        "hours": f"{stale_hours:.0f}",
        "alert_hours": str(cfg.ALERT_AFTER_HOURS),
        "date_iso": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "profile_url": f"https://www.tiktok.com/@{cfg.TIKTOK_USERNAME}",
        "last_seen_iso": datetime.fromtimestamp(
            state["last_seen_ts"], tz=timezone.utc
        ).isoformat(timespec="seconds"),
    }


def render_template(filename: str, subs: dict) -> tuple[str, str]:
    """Return (subject, body). Subject = baris pertama 'Subject: ...' (opsional)."""
    path = os.path.join(cfg._TEMPLATES_DIR, filename)
    with open(path, encoding="utf-8") as f:
        text = f.read()
    subject = ""
    if text.startswith("Subject:"):
        first_line, _, rest = text.partition("\n")
        subject = first_line[len("Subject:"):].strip()
        text = rest.lstrip("\n")
    for key, val in subs.items():
        text = text.replace("{" + key + "}", val)
        subject = subject.replace("{" + key + "}", val)
    return subject, text.strip()


# --- Kirim dengan try/except per channel -----------------------------------

def try_telegram(text: str, tag: str) -> bool:
    if cfg.DRY_RUN:
        log(f"[dry-run] WOULD kirim Telegram ({tag}):\n---\n{text}\n---")
        return True
    try:
        notify.send_telegram(cfg.TELEGRAM_BOT_TOKEN, cfg.TELEGRAM_CHAT_ID, text)
        log(f"Telegram terkirim ({tag})")
        return True
    except Exception as e:
        log(f"GAGAL kirim Telegram ({tag}): {e}")
        return False


def try_email(state: dict, stale_hours: float, tag: str) -> bool:
    subject, body = render_template("alert_third_party.md", _subs(state, stale_hours))
    if cfg.DRY_RUN:
        # Log Actions itu publik di repo public — alamat penerima nggak dicetak.
        log(f"[dry-run] WOULD kirim email alert ke penerima terkonfigurasi ({tag}):\n"
            f"Subject: {subject}\n---\n{body}\n---")
        return True
    try:
        notify.send_email(
            cfg.SMTP_HOST, cfg.SMTP_PORT, cfg.SMTP_USER, cfg.SMTP_APP_PASSWORD,
            cfg.EMAIL_FROM, cfg.ALERT_EMAIL_TO, subject, body,
        )
        log(f"Email alert terkirim ({tag})")
        return True
    except Exception as e:
        log(f"GAGAL kirim email ({tag}): {e}")
        return False


# --- Main -------------------------------------------------------------------

def main() -> int:
    if not cfg.TIKTOK_USERNAME:
        log("TIKTOK_USERNAME belum diset")
        return 2
    if cfg.DRY_RUN:
        log("MODE DRY RUN — nggak ada yang dikirim / disimpan")

    path = state_path()
    state = load_state(path)
    now = int(time.time())

    # --- Mode test notifikasi (nggak nyentuh state) -------------------------
    if cfg.FORCE_REMIND:
        _, body = render_template("reminder_self.txt", _subs(state, 0))
        ok = try_telegram(body, "force_remind")
        return 0 if ok else 1
    if cfg.FORCE_ALERT:
        ok = try_email(state, cfg.ALERT_AFTER_HOURS, "force_alert")
        return 0 if ok else 1

    # --- 1. Scrape ----------------------------------------------------------
    try:
        res = fetch_activity(cfg.TIKTOK_USERNAME)
    except ScrapeError as e:
        state["consecutive_failures"] += 1
        fails = state["consecutive_failures"]
        log(f"scrape GAGAL: {e} (beruntun: {fails}/{cfg.SCRAPE_FAIL_LIMIT})")

        if fails >= cfg.SCRAPE_FAIL_LIMIT:
            last_warn = state.get("last_blind_warn_ts") or 0
            if now - last_warn >= cfg.BLIND_WARN_EVERY_HOURS * 3600:
                log("monitor blind — kirim warning ke diri sendiri")
                ok = try_telegram(
                    f"👁️ Streak monitor BUTA: scrape gagal {fails}x "
                    f"berturut-turut ({e}). Check-in lo mungkin tetap aman, "
                    "tapi cek Actions log / akun TikTok publik ya.",
                    "blind_warn",
                )
                if ok:
                    state["last_blind_warn_ts"] = now
        if not cfg.DRY_RUN:
            save_state(path, state)
        _ping()
        return 0

    state["consecutive_failures"] = 0
    source = res.get("source")
    log(f"scrape OK via {source}")

    # --- Deteksi check-in (per jalur) ----------------------------------------
    if source == "tikwm":
        # Sinyal = perubahan urutan video_id repost (paling baru di indeks 0).
        # create_time tidak dipakai karena itu tanggal video asli dibuat.
        top_ids = res.get("top_ids") or []
        prev = state.get("last_top_ids")
        if prev is None:
            state["last_top_ids"] = top_ids
            new_seen = False
            log(f"baseline disimpan: {len(top_ids)} repost "
                f"(top-1={top_ids[0] if top_ids else '-'}) — run pertama jalur tikwm")
        else:
            # Bandingin prefix sepanjang data yang sama (kalau salah satu
            # list lebih pendek, jangan sampai beda panjang dianggap activity)
            k = min(len(top_ids), len(prev), 5)
            new_seen = bool(top_ids) and (
                not prev
                or top_ids[0] != prev[0]
                or set(top_ids[:k]) != set(prev[:k])
            )
            if new_seen:
                state["last_top_ids"] = top_ids
                state["last_seen_ts"] = now
                log(f"check-in terdeteksi: urutan repost berubah "
                    f"(top-1 sekarang {top_ids[0]}, sebelumnya "
                    f"{prev[0] if prev else '-'})")
            else:
                log(f"urutan repost nggak berubah ({len(top_ids)} repost "
                    f"terlihat, top-1={top_ids[0] if top_ids else '-'})")
    else:  # playwright
        log(f"repost terlihat: {res.get('count')} (tab_found={res.get('tab_found')})")
        new_seen = bool(res.get("latest_ts")) and res["latest_ts"] > state["last_seen_ts"]
        if new_seen:
            state["last_seen_ts"] = res["latest_ts"]
            log(
                "check-in terdeteksi: repost "
                + datetime.fromtimestamp(res["latest_ts"], tz=timezone.utc).isoformat(
                    timespec="seconds"
                )
            )

    stale_hours = max(0.0, (now - state["last_seen_ts"]) / 3600)
    log(f"umur check-in terakhir: {stale_hours:.1f} jam "
        f"(remind >={cfg.REMIND_AFTER_HOURS}h, alert >={cfg.ALERT_AFTER_HOURS}h)")

    # --- 2. All clear -------------------------------------------------------
    had_activity = bool(
        state.get("alerting") or state.get("last_remind_ts") or state.get("last_alert_ts")
    )
    if new_seen and had_activity:
        log("check-in kembali + sebelumnya lagi eskalasi -> kirim all clear")
        if try_telegram(
            f"✅ Streak tenang lagi: repost @{cfg.TIKTOK_USERNAME} terdeteksi. "
            "Semua alert dibatalkan.",
            "all_clear",
        ):
            state["alerting"] = False
            state["last_remind_ts"] = None
            state["last_alert_ts"] = None

    # --- 3. Eskalasi ----------------------------------------------------------
    elif not new_seen:
        alert_due = state.get("last_alert_ts") is None or (
            now - state["last_alert_ts"] >= cfg.ALERT_EVERY_HOURS * 3600
        )
        remind_due = state.get("last_remind_ts") is None or (
            now - state["last_remind_ts"] >= cfg.REMIND_EVERY_HOURS * 3600
        )

        if stale_hours >= cfg.ALERT_AFTER_HOURS:
            if alert_due:
                log(f"STALE {stale_hours:.0f}h >= {cfg.ALERT_AFTER_HOURS}h "
                    "-> ALERT email ke pihak ketiga")
                if try_email(state, stale_hours, "alert"):
                    state["last_alert_ts"] = now
                    state["alerting"] = True
        elif stale_hours >= cfg.REMIND_AFTER_HOURS:
            if remind_due:
                log(f"STALE {stale_hours:.0f}h >= {cfg.REMIND_AFTER_HOURS}h "
                    "-> reminder Telegram ke diri sendiri")
                _, body = render_template("reminder_self.txt", _subs(state, stale_hours))
                if try_telegram(body, "remind"):
                    state["last_remind_ts"] = now

    # --- 4. Simpan + healthcheck -------------------------------------------
    if not cfg.DRY_RUN:
        save_state(path, state)
        log("state disimpan")
    _ping()
    return 0


def _ping() -> None:
    if not cfg.HEALTHCHECK_URL or cfg.DRY_RUN:
        return
    try:
        notify.ping_healthcheck(cfg.HEALTHCHECK_URL, ok=True)
        log("healthcheck ping OK")
    except Exception as e:
        log(f"healthcheck ping gagal: {e}")


if __name__ == "__main__":
    sys.exit(main())
