# TITEN US

Screener & analisis saham AS (S&P 500, Nasdaq-100, Dow 30) — saudara [TITEN](https://github.com/mrd270181/TITEN) untuk bursa Amerika.

**Aplikasi:** https://mrd270181.github.io/TITEN-US/ (bisa dipasang ke layar utama HP dan dibuka offline)

## Cara kerja — tanpa laptop

GitHub Actions (`.github/workflows/perbarui.yml`) menjalankan `pipeline/jalankan.py` dan menerbitkan hasilnya ke cabang `gh-pages`:

| Jadwal (WIB) | Mode | Isi |
|---|---|---|
| Selasa–Sabtu 05.20 | harian | harga & volume ~2 tahun (Yahoo Finance) → rasio & indikator → data aplikasi |
| Minggu 06.00 | mingguan | + daftar anggota indeks + laporan keuangan SEC EDGAR |

Bisa juga dijalankan kapan saja: tab **Actions** → *Perbarui data TITEN US* → **Run workflow**.

- Sumber: laporan keuangan **SEC EDGAR** (resmi, gratis), harga **yfinance**, daftar indeks Wikipedia + nasdaq.com.
- Kalau Yahoo menolak (>20% emiten tanpa harga), jalan itu gagal dan situs lama dipertahankan; GitHub mengirim email pemberitahuan.
- Harga hari berjalan (bursa New York belum tutup) otomatis dibuang.
- Opsional: secret `SEC_USER_AGENT` berisi `Nama email@anda.com` (permintaan SEC untuk kontak).

## Isi folder
- `app/` — aplikasi (satu berkas `index.html`, service worker, ikon)
- `pipeline/` — `daftar.py`, `sec.py`, `harga.py`, `ringkasan.py`, `ekspor.py`, `jalankan.py`
- Cabang `gh-pages` — situs terbit: `data/` (ringkasan + satu berkas per emiten) dan `state/` (laporan keuangan mingguan, dibaca jalan harian)
