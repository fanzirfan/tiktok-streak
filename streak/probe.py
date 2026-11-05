"""Probe: cek apakah deteksi repost TikTok jalan buat satu akun.

Ngetes dua jalur: tikwm (utama) lalu browser headless (fallback).
Jalankan dari lokal ATAU dari GitHub Actions (workflow_dispatch `probe`).

Pemakaian:
    python streak/probe.py <username>
"""

import sys

from scraper_tiktok import ScrapeError, fetch_activity


def main() -> int:
    if len(sys.argv) < 2:
        print("pemakaian: python streak/probe.py <username>")
        return 2
    username = sys.argv[1].lstrip("@")

    print(f"[probe] ngecek @{username} (tikwm dulu, lalu browser fallback) ...")
    try:
        res = fetch_activity(username)
    except ScrapeError as e:
        print(f"[probe] GAGAL total: {e}")
        print("[probe] Coba lagi nanti; kalau berulang lihat README troubleshooting.")
        return 1

    source = res["source"]
    print(f"[probe] OK — jalur: {source}")

    if source == "tikwm":
        ids = res["top_ids"]
        if not ids:
            print("[probe] tikwm bilang nggak ada repost terlihat. Cek:")
            print("[probe]   1. Apakah lo udah pernah repost? Repost 1 video lalu ulangi.")
            print("[probe]   2. Settings -> Privacy -> Reposts harus 'Everyone'.")
            return 1
        print(f"[probe] repost terlihat: {res['count']} (diambil {len(ids)} teratas)")
        print(f"[probe] id repost teratas (paling baru): {ids[0]}")
        print("[probe] catatan: urutan list = repost terbaru duluan; sistem streak")
        print("[probe] mendeteksi check-in dari perubahan urutan ini antar-run.")
        return 0

    # jalur browser
    print(f"[probe] repost terlihat: {res['count']} (tab_found={res.get('tab_found')})")
    latest = res["latest_ts"]
    if latest is None:
        print("[probe] nggak ada repost terbaca via browser (biasanya anti-bot")
        print("[probe] ngasih shell kosong — tikwm di atas sudah jadi jalur utama).")
        return 1
    from datetime import datetime
    print(f"[probe] repost terakhir: {datetime.fromtimestamp(latest).isoformat()}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
