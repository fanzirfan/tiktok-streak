"""Cek aktivitas repost TikTok.

Strategi berlapis:
1. UTAMA - tikwm.com (API mirror gratis, tanpa key, tanpa browser):
   GET /api/user/reposts?unique_id=... -> daftar repost terbaru.
   PENTING: urutan list = urutan waktu REPOST (paling baru di indeks 0),
   sedangkan field create_time = waktu video ASLI dibuat. Karena itu
   sinyal check-in = perubahan urutan video_id antar-run, bukan timestamp.
2. FALLBACK - Playwright headless: buka profil publik, intercept respons
   /api/repost/item_list/, pakai createTime terbesar.

Jalur browser dipakai kalau tikwm kena challenge Cloudflare / mati.
"""

import json
import re
import time
import urllib.error
import urllib.parse
import urllib.request


class ScrapeError(Exception):
    """Gagal scrape transien (WAF, timeout) — layak diulang."""


class PermanentScrapeError(ScrapeError):
    """Gagal yang nggak akan sembuh dengan retry (akun nggak ada)."""


# --- Jalur 1: tikwm (utama) --------------------------------------------------

TIKWM_REPOSTS_URL = "https://www.tikwm.com/api/user/reposts"
TIKWM_RETRIES = 3
TIKWM_BACKOFF_S = 3
TIKWM_COUNT = 10

_BROWSER_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
)


def fetch_reposts_tikwm(username: str, count: int = TIKWM_COUNT) -> dict:
    """Repost via mirror API tikwm.com. Return:

    {"ok": True, "source": "tikwm", "top_ids": [video_id, ...], "count": int}

    top_ids terurut dari repost paling baru. Kosong = nggak ada repost
    yang terlihat.
    """
    params = urllib.parse.urlencode(
        {"unique_id": username.lstrip("@"), "count": str(count)}
    )
    url = f"{TIKWM_REPOSTS_URL}?{params}"
    last_err = None
    for attempt in range(1, TIKWM_RETRIES + 1):
        try:
            req = urllib.request.Request(url, headers={
                "User-Agent": _BROWSER_UA,
                "Accept": "application/json, text/plain, */*",
                "Accept-Language": "en-US,en;q=0.9",
                "Referer": "https://www.tikwm.com/",
            })
            with urllib.request.urlopen(req, timeout=30) as resp:
                data = json.loads(resp.read().decode("utf-8", "replace"))
            if data.get("code") != 0:
                raise ScrapeError(
                    f"tikwm code={data.get('code')} ({data.get('msg')})"
                )
            vids = (data.get("data") or {}).get("videos") or []
            return {
                "ok": True,
                "source": "tikwm",
                "top_ids": [str(v.get("video_id")) for v in vids if v.get("video_id")],
                "count": len(vids),
            }
        except urllib.error.HTTPError as e:
            # 403/503 = challenge Cloudflare / rate limit -> backoff lalu ulang
            last_err = f"HTTP {e.code} (kemungkinan challenge Cloudflare)"
        except ScrapeError:
            raise
        except Exception as e:
            last_err = str(e)
        if attempt < TIKWM_RETRIES:
            time.sleep(TIKWM_BACKOFF_S)
    raise ScrapeError(f"tikwm gagal setelah {TIKWM_RETRIES}x: {last_err}")


# --- Jalur 2: browser headless (fallback) ------------------------------------

PROFILE_WAIT_S = 20
TAB_WAIT_S = 8
RETRY = 2
RETRY_BACKOFF_S = 5

# Tab repost: teksnya tergantung locale; ?lang=en bikin kebanyakan "Reposts".
_TAB_TEXTS = ("Reposts", "Repost", "リポスト", "转发")


def _parse_universal_data(html: str) -> dict:
    m = re.search(
        r'<script id="__UNIVERSAL_DATA_FOR_REHYDRATION__"[^>]*>(.*?)</script>',
        html,
        re.S,
    )
    if not m:
        return {}
    try:
        return json.loads(m.group(1), parse_constant=lambda s: None)
    except json.JSONDecodeError:
        return {}


def _extract_user(universal: dict) -> dict:
    return (universal.get("__DEFAULT_SCOPE__", {})
            .get("webapp.user-detail", {})
            .get("userInfo", {})
            .get("user", {}))


def _extract_page_status(universal: dict):
    """statusCode tingkat halaman dari SSR data (bukan statusCode API repost)."""
    return (universal.get("__DEFAULT_SCOPE__", {})
            .get("webapp.user-detail", {})
            .get("statusCode"))


def _find_reposts_tab(page):
    """Cari elemen tab Reposts via JS (tanpa actionability check — tab kadang
    'invisible' tapi tetap berfungsi diklik via JS). Return handle atau None."""
    handle = page.evaluate_handle(
        """(texts) => {
            const bySel = document.querySelector(
                '[data-e2e="repost-tab"], [data-e2e="tab-repost"]');
            if (bySel) return bySel;
            for (const el of document.querySelectorAll('div,span,p,button')) {
                if (el.children.length === 0 && texts.includes(el.textContent.trim()))
                    return el;
            }
            return null;
        }""",
        list(_TAB_TEXTS),
    )
    return handle.as_element()


def _parse_captured(captured: list) -> dict | None:
    """Parse respons API repost yang sudah tertangkap. None kalau belum ada."""
    for resp in reversed(captured):  # yang terbaru dulu
        try:
            text = resp.text()
        except Exception:
            continue
        if not text.strip():
            continue
        # JSON TikTok kadang mengandung NaN/Infinity unquoted -> parser strict
        # gagal. parse_constant menetralkannya jadi None.
        try:
            data = json.loads(text, parse_constant=lambda s: None)
        except json.JSONDecodeError:
            continue
        status_code = data.get("statusCode")
        if status_code not in (0, None):
            raise ScrapeError(
                f"API repost statusCode={status_code} "
                f"({data.get('statusMsg', '')})"
            )
        items = data.get("itemList") or []
        latest = None
        for item in items:
            try:
                ts = int(item.get("createTime", 0))
            except (TypeError, ValueError):
                continue
            if latest is None or ts > latest:
                latest = ts
        return {"count": len(items), "latest_ts": latest}
    return None


def _attempt(username: str) -> dict:
    from playwright.sync_api import sync_playwright

    result = {"ok": True, "latest_ts": None, "count": 0,
              "nickname": username, "tab_found": False}

    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True)
        context = browser.new_context(
            user_agent=_BROWSER_UA,
            viewport={"width": 1920, "height": 1080},
            locale="en-US",
            timezone_id="Asia/Jakarta",
        )
        page = context.new_page()

        # Listener respons dipasang sejak awal: TikTok PREFETCH data semua tab
        # (termasuk repost) sejak load halaman versi logged-out, jadi respons
        # bisa datang tanpa perlu klik tab.
        captured = []

        def on_response(resp):
            if "/api/repost/item_list/" in resp.url:
                captured.append(resp)

        page.on("response", on_response)
        try:
            page.goto(
                f"https://www.tiktok.com/@{username.lstrip('@')}?lang=en",
                wait_until="domcontentloaded",
                timeout=PROFILE_WAIT_S * 1000,
            )

            # WAF challenge jalan di background; tunggu SSR data muncul.
            # page.content() bisa melempar kalau halaman lagi navigasi
            # (redirect WAF) — di-toleransi, cukup ulang polling.
            deadline = time.monotonic() + PROFILE_WAIT_S
            user = {}
            while time.monotonic() < deadline:
                try:
                    universal = _parse_universal_data(page.content())
                except Exception:
                    page.wait_for_timeout(1500)
                    continue
                user = _extract_user(universal)
                if user.get("secUid"):
                    result["nickname"] = user.get("nickname") or username
                    break
                # 10202/10216 = "user not exist" versi TikTok; string "couldn't
                # find this account" juga ada di JS bundle, jadi jangan dipakai.
                if not user and _extract_page_status(universal) in (10202, 10216):
                    raise PermanentScrapeError(f"akun @{username} nggak ada (404)")
                page.wait_for_timeout(1500)
            else:
                raise ScrapeError(
                    "profil nggak ke-load (WAF/captcha nggak lolos) — coba lagi, "
                    "kalau berulang lihat README troubleshooting"
                )

            parsed = _parse_captured(captured)

            if parsed is None:
                # Beri kesempatan prefetch masuk (biasanya cepat)
                deadline = time.monotonic() + TAB_WAIT_S
                while time.monotonic() < deadline and parsed is None:
                    page.wait_for_timeout(500)
                    parsed = _parse_captured(captured)

            if parsed is None:
                # Prefetch nggak datang / kosong: klik tab Reposts via JS
                # (klik DOM biasa bisa timeout karena tab kadang dianggap
                # invisible oleh Playwright).
                tab = _find_reposts_tab(page)
                if tab is None:
                    # Tab nggak ada: bisa jadi belum pernah repost, atau
                    # privacy repost diset bukan Everyone.
                    return result
                result["tab_found"] = True
                try:
                    page.evaluate("el => el.click()", tab)
                except Exception:
                    pass  # klik gagal -> masih ada peluang prefetch masuk
                deadline = time.monotonic() + TAB_WAIT_S
                while time.monotonic() < deadline and parsed is None:
                    page.wait_for_timeout(500)
                    parsed = _parse_captured(captured)

            if parsed is None:
                if captured:
                    raise ScrapeError(
                        "respons API repost nggak bisa di-parse (body kosong/HTML)"
                    )
                raise ScrapeError(
                    "tab repost ada tapi respons API /api/repost/item_list/ "
                    "nggak muncul"
                )

            result["count"] = parsed["count"]
            result["latest_ts"] = parsed["latest_ts"]
            return result
        finally:
            context.close()
            browser.close()


def fetch_latest_repost(username: str) -> dict:
    """Return {"ok": True, "latest_ts": int|None, "count": int, "nickname": str}.

    latest_ts None kalau repost nggak ada / nggak publik.
    PermanentScrapeError kalau akun nggak ada; ScrapeError kalau gagal
    transien setelah RETRY percobaan.
    """
    last_err = None
    for attempt in range(1, RETRY + 1):
        try:
            return _attempt(username)
        except PermanentScrapeError:
            raise  # akun nggak ada, retry cuma buang waktu
        except Exception as e:  # WAF, timeout, network -> retry
            last_err = e
            if attempt < RETRY:
                time.sleep(RETRY_BACKOFF_S)
    raise ScrapeError(f"gagal setelah {RETRY} percobaan: {last_err}")


# --- Orkestrator --------------------------------------------------------------

def fetch_activity(username: str) -> dict:
    """Cek aktivitas repost: tikwm dulu (cepat), fallback browser headless.

    Return jalur tikwm:
        {"ok": True, "source": "tikwm", "top_ids": [...], "count": int}
    Return jalur browser:
        {"ok": True, "source": "playwright", "latest_ts": int|None,
         "count": int, "nickname": str, "tab_found": bool}

    ScrapeError kalau dua-duanya gagal.
    """
    try:
        return fetch_reposts_tikwm(username)
    except ScrapeError as tikwm_err:
        try:
            res = fetch_latest_repost(username)
        except PermanentScrapeError:
            raise  # akun beneran nggak ada
        except ScrapeError as pw_err:
            raise ScrapeError(
                f"tikwm: {tikwm_err} | browser: {pw_err}"
            ) from pw_err
        res["source"] = "playwright"
        return res
