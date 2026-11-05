# tiktok-streak

Streak tracker buat ritual repost TikTok gua. Sistem ngecek tiap 6 jam via
GitHub Actions: kalau gua berhenti repost, bot Telegram ngomel ke gua; kalau
sudah berhari-hari sunyi, email kejadian dikirim ke temen accountability gua
biar ada yang nanya kabar.

```
Repost terakhir: Hari 0
Hari 1-2 → reminder Telegram ke diri sendiri (ulang tiap 12 jam)
Hari 2+  → email ke temen accountability (ulang tiap 24 jam)
Repost lagi → reset otomatis + notif "all clear"
```

Deteksinya via API mirror [tikwm.com](https://www.tikwm.com) (gratis, tanpa
key); fallback-nya Playwright headless yang buka profil publik. Semua logika
di `streak/check.py`, tanpa server, jalan di free tier GitHub Actions.

## Setup

### 1. TikTok: repost harus publik
Di app TikTok: **Settings and privacy → Privacy → Reposts → "Who can watch
your reposts"** → **Everyone**. Kalau bukan, repost nggak akan kebaca sistem.

Tes dari lokal:
```bash
pip install -r requirements.txt
python -m playwright install chromium
python streak/probe.py <username-tanpa-@>
```

### 2. Telegram bot
1. Chat [@BotFather](https://t.me/BotFather) → `/newbot` → simpan **token**.
2. Kirim `/start` ke bot yang baru dibuat (bot nggak bisa DM duluan).
3. Ambil **chat_id**: buka `https://api.telegram.org/bot<TOKEN>/getUpdates`,
   cari `"chat":{"id": ...`.

### 3. SMTP (email accountability)
Pakai SMTP apa pun. Umumnya butuh **app password** (bukan password login
biasa) — mis. Gmail: akun → Security → App passwords. Port `587` (STARTTLS)
atau `465` (SSL).

### 4. Repo + konfigurasi
Push repo ini, lalu di **Settings → Secrets and variables → Actions**:

**Variables:**
- `TIKTOK_USERNAME` — username TikTok tanpa `@`
- (opsional) `REMIND_AFTER_HOURS`, `ALERT_AFTER_HOURS` — kalau ritme repost
  lebih longgar dari default 24/48 jam, mis. `48` dan `72`

**Secrets:** `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID`, `SMTP_HOST`,
`SMTP_PORT`, `SMTP_USER`, `SMTP_APP_PASSWORD`, `EMAIL_FROM`,
`ALERT_EMAIL_TO`, dan (opsional) `HEALTHCHECK_URL` — URL healthchecks.io
buat monitor eksternal kalau workflow-nya sendiri mati.

### 5. Edit template email
`templates/alert_third_party.md` — pesan yang diterima temen accountability.
Sesuaikan isinya. Jangan taruh password/rahasia di email.

### 6. Urutan test (tab Actions → repost streak → Run workflow)
1. `probe` — deteksi jalan dari IP GitHub?
2. `dry_run` — keputusan di-log tanpa kirim apa pun
3. `force_remind` — notif Telegram harus masuk
4. `force_alert` — email harus masuk (tes pakai email sendiri dulu)
5. `normal` — jalan seperti jadwal; commit `state.json` muncul tiap run

## Catatan desain

- **Sinyal check-in = urutan repost, bukan timestamp.** Di list repost,
  urutan = waktu repost (paling baru di atas), sedangkan `create_time` =
  tanggal video asli dibuat. Sistem membandingkan urutan `video_id`
  antar-run: berubah = ada repost baru.
- **Scrape gagal ≠ user hilang.** Kalau deteksi gagal ≥ 8 run berturut
  (±2 hari), sistem kirim warning "monitor blind" via Telegram — email
  accountability tidak pernah terkirim cuma gara-gara scrape gagal.
- **Keepalive 60 hari**: GitHub menonaktifkan scheduled workflow kalau repo
  60 hari tanpa commit. Tiap run commit `state.json` = keepalive otomatis.
- **Cron telat beberapa menit** itu normal; semua threshold pakai jam.
- **Run pertama** bikin baseline (urutan repost saat itu), nggak ada alert
  instan.

## Troubleshooting

| Gejala | Penyebab umum |
|---|---|
| Probe: "tikwm gagal ... Cloudflare" | Tikwm kadang 403 sesaat — ada retry 3x; kalau terus, fallback browser otomatis jalan |
| Probe: "profil nggak ke-load (WAF)" | TikTok blok IP runner — jalur tikwm harusnya sudah nutup; kalau dua-duanya gagal berulang, cek Actions log |
| Probe: "nggak ada repost terlihat" | Belum pernah repost, atau privacy repost bukan Everyone |
| Reminder nggak masuk Telegram | Bot belum di-DM `/start`, atau chat_id salah |
| Email gagal | App password salah / port salah; cek log step "Cek streak" |
| Workflow nggak jalan sesuai jadwal | Pastikan di default branch & Actions di-enable |

## File penting

- `streak/check.py` — state machine (dibaca mulai dari sini)
- `streak/scraper_tiktok.py` — tikwm API (utama) + Playwright fallback
- `streak/probe.py` — tes deteksi standalone
- `templates/` — isi pesan reminder & email
- `state.json` — state (di-commit otomatis tiap run, jangan edit manual)
