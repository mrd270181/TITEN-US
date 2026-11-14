# jalankan.py - pengatur pembaruan TITEN US (dipanggil GitHub Actions, bisa juga di laptop).
#   harian   : harga terbaru -> ringkasan -> ekspor data aplikasi   (memakai state laporan keuangan terakhir)
#   mingguan : daftar emiten + laporan keuangan SEC, lalu langkah harian
# Pakai: python pipeline/jalankan.py --mode harian --lama <folder situs lama> --keluar <folder situs baru>
import argparse, os, shutil, sys, time
from datetime import datetime
from zoneinfo import ZoneInfo
import duckdb

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import daftar, sec, harga, ringkasan, ekspor, kalender, rekomendasi   # noqa: E402

AKAR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TABEL_STATE = ["emiten", "lk", "fakta"]


def muat_state(con, folder):
    berkas = [os.path.join(folder, f"{t}.parquet") for t in TABEL_STATE]
    if not all(os.path.exists(b) for b in berkas):
        return False
    for t, b in zip(TABEL_STATE, berkas):
        con.execute(f"CREATE OR REPLACE TABLE {t} AS SELECT * FROM read_parquet('{b}')")
    n = con.execute("SELECT COUNT(*) FROM emiten").fetchone()[0]
    n_lk = con.execute("SELECT COUNT(*) FROM lk").fetchone()[0]
    print(f"state dimuat: {n} emiten, {n_lk:,} baris lk")
    return n > 0 and n_lk > 0          # lk kosong (SEC sempat menolak) -> coba mingguan lagi


def simpan_state(con, folder):
    os.makedirs(folder, exist_ok=True)
    for t in TABEL_STATE:
        con.execute(f"COPY {t} TO '{os.path.join(folder, t + '.parquet')}' (FORMAT parquet, COMPRESSION zstd)")


def mingguan(con, folder_cek, folder_state_lama):
    con.execute("""CREATE OR REPLACE TABLE emiten (ticker VARCHAR, nama VARCHAR, sektor VARCHAR,
                   sp500 BOOLEAN, ndx BOOLEAN, dow BOOLEAN)""")
    con.executemany("INSERT INTO emiten VALUES (?,?,?,?,?,?)", daftar.daftar_emiten(folder_cek))
    con.execute("""CREATE OR REPLACE TABLE lk (ticker VARCHAR, pos VARCHAR, tahun_buku INTEGER, periode VARCHAR,
                   mulai DATE, akhir DATE, nilai DOUBLE, satuan VARCHAR, tag VARCHAR, turunan BOOLEAN)""")
    con.execute("""CREATE OR REPLACE TABLE fakta (ticker VARCHAR, cik INTEGER, taksonomi VARCHAR, tag VARCHAR,
                   satuan VARCHAR, mulai DATE, akhir DATE, nilai DOUBLE, form VARCHAR, filed DATE)""")
    try:
        peta = sec.peta_cik()
    except Exception as e:
        # SEC menolak: pakai laporan keuangan dari state lama kalau ada, jangan gagalkan seluruh pembaruan
        print(f"PERINGATAN: SEC tidak bisa diakses ({e}). Laporan keuangan memakai data terakhir yang ada.")
        for t in ("lk", "fakta"):
            b = os.path.join(folder_state_lama, f"{t}.parquet")
            if os.path.exists(b):
                con.execute(f"INSERT INTO {t} SELECT * FROM read_parquet('{b}')")
        return {"tanpa_sec": [], "lk_kosong": [], "sec_gagal": str(e)}
    tickers = [r[0] for r in con.execute("SELECT ticker FROM emiten ORDER BY 1").fetchall()]
    tanpa, kosong = [], []
    for i, t in enumerate(tickers, 1):
        if t not in peta:
            tanpa.append(t)
            continue
        cik, _ = peta[t]
        for coba in range(3):
            try:
                time.sleep(0.12)
                baris, fakta = sec.tarik_satu(t, cik)
                break
            except Exception as e:
                if coba == 2:
                    print(f"   {t}: GAGAL ({e})")
                    baris, fakta = [], []
                time.sleep(3 * (coba + 1))
        if baris:
            con.executemany("INSERT INTO lk VALUES (?,?,?,?,?,?,?,?,?,?)", baris)
        else:
            kosong.append(t)
        if fakta:
            con.executemany("INSERT INTO fakta VALUES (?,?,?,?,?,?,?,?,?,?)", fakta)
        if i % 50 == 0:
            print(f"   SEC {i}/{len(tickers)}")
    print(f"laporan keuangan: {len(tickers) - len(tanpa) - len(kosong)} emiten terisi")
    if tanpa:
        print(f"   belum terdaftar di SEC: {', '.join(tanpa)}")
    if kosong:
        print(f"   tanpa laporan tahunan / gagal: {', '.join(kosong)}")
    return {"tanpa_sec": tanpa, "lk_kosong": kosong}


def tentukan_batas(con):
    """Tanggal harga terakhir yang FINAL. Kalau hari ini di New York belum lewat 16.20, buang hari berjalan."""
    akhir = con.execute("SELECT MAX(tanggal) FROM harga").fetchone()[0]
    ny = datetime.now(ZoneInfo("America/New_York"))
    if akhir == ny.date() and (ny.hour, ny.minute) < (16, 20):
        con.execute("DELETE FROM harga WHERE tanggal = ?", [akhir])
        print(f"   {akhir} belum final (New York {ny:%H:%M}) - dibuang")
        akhir = con.execute("SELECT MAX(tanggal) FROM harga").fetchone()[0]
    return akhir


def harian(con, keluar, info, lama=None):
    tickers = [r[0] for r in con.execute("SELECT ticker FROM emiten ORDER BY 1").fetchall()]
    kosong = harga.tarik(con, tickers)
    if len(kosong) > 0.2 * len(tickers):
        sys.exit(f"GAGAL: {len(kosong)} dari {len(tickers)} emiten tanpa harga (Yahoo menolak?). Situs lama dipertahankan.")
    if kosong:
        print(f"   tanpa harga: {', '.join(kosong)}")
    batas = tentukan_batas(con)
    ringkasan.hitung(con, batas)
    # kartu rekomendasi (LK, valuasi, analis, berita) - gagal tidak menggagalkan pembaruan
    for k, tipe in (("skor_lk", "DOUBLE"), ("label_lk", "VARCHAR"), ("label_val", "VARCHAR"), ("teks_val", "VARCHAR"),
                    ("label_analis", "VARCHAR"), ("upside_analis", "DOUBLE"), ("label_berita", "VARCHAR"), ("teks_berita", "VARCHAR")):
        con.execute(f"ALTER TABLE ringkasan ADD COLUMN IF NOT EXISTS {k} {tipe}")
    rek = None
    try:
        rek, kolom = rekomendasi.hitung(con, lama)
        for t, v in kolom.items():
            con.execute("""UPDATE ringkasan SET skor_lk = ?, label_lk = ?, label_val = ?, teks_val = ?, label_analis = ?,
                           upside_analis = ?, label_berita = ?, teks_berita = ? WHERE ticker = ?""",
                        [v["skor_lk"], v["label_lk"], v["label_val"], v["teks_val"], v["label_analis"],
                         v["upside_analis"], v["label_berita"], v["teks_berita"], t])
    except Exception as e:
        import traceback
        traceback.print_exc()
        print(f"PERINGATAN: rekomendasi gagal ({e})")
    info = {**info, "tanpa_harga": kosong}
    ekspor.tulis(con, keluar, batas, info, rek)


def sesi_final_terakhir():
    """Tanggal sesi bursa New York terakhir yang sudah tutup (Senin-Jumat; hari libur bursa tidak dihitung)."""
    from datetime import timedelta
    ny = datetime.now(ZoneInfo("America/New_York"))
    d = ny.date() if (ny.hour, ny.minute) >= (16, 20) else ny.date() - timedelta(days=1)
    while d.weekday() >= 5:
        d -= timedelta(days=1)
    return d


def main():
    import json
    ap = argparse.ArgumentParser()
    ap.add_argument("--mode", choices=["harian", "mingguan"], default="harian")
    ap.add_argument("--lama", default="lama", help="folder situs sebelumnya (berisi state/)")
    ap.add_argument("--keluar", default="situs", help="folder situs baru")
    ap.add_argument("--ulang", action="store_true",
                    help="jadwal cadangan: lewati kalau data sudah terbaru dan laporan keuangan tidak sedang tertunda")
    a = ap.parse_args()

    if os.path.exists(a.keluar):
        shutil.rmtree(a.keluar)
    os.makedirs(a.keluar)
    try:
        m_lama = json.load(open(os.path.join(a.lama, "data", "manifest.json"), encoding="utf-8"))
    except Exception:
        m_lama = {}

    mode = a.mode
    if m_lama.get("sec_gagal"):
        print(f"jalan sebelumnya SEC gagal ({m_lama['sec_gagal']}) - ulangi mode mingguan")
        mode = "mingguan"
    if a.ulang and mode == "harian" and m_lama.get("batas", "") >= sesi_final_terakhir().isoformat():
        print(f"data sudah terbaru (per {m_lama['batas']}) - jadwal cadangan tidak perlu jalan")
        open(os.path.join(a.keluar, "LEWATI"), "w").close()
        return

    con = duckdb.connect()
    info = {}
    if mode == "harian" and not muat_state(con, os.path.join(a.lama, "state")):
        print("state belum ada / laporan keuangan kosong - jalankan mode mingguan")
        mode = "mingguan"
    if mode == "mingguan":
        info = mingguan(con, a.keluar, os.path.join(a.lama, "state"))
    else:   # bawa catatan mingguan terakhir
        info = {k: m_lama.get(k, []) for k in ("tanpa_sec", "lk_kosong")}
    try:
        k, tg = harga.kurs_usd_idr()
        print(f"kurs USD/IDR {k:,.2f} ({tg})")
    except Exception as e:
        k, tg = m_lama.get("kurs_usd_idr"), m_lama.get("kurs_tanggal")
        print(f"PERINGATAN: kurs gagal diambil ({e}) - pakai kurs terakhir {k} ({tg})")
    simpan_state(con, os.path.join(a.keluar, "state"))
    harian(con, a.keluar, {**info, "mode": mode, "kurs_usd_idr": k, "kurs_tanggal": tg}, a.lama)

    # kalender earnings + makro (gagal tidak menggagalkan pembaruan)
    try:
        tickers = [r[0] for r in con.execute("SELECT ticker FROM emiten").fetchall()]
        kalender.tulis(a.keluar, tickers, os.path.join(a.lama, "data", "kalender.json"))
    except Exception as e:
        print(f"PERINGATAN: kalender gagal ({e})")
        lama_k = os.path.join(a.lama, "data", "kalender.json")
        if os.path.exists(lama_k):
            shutil.copy2(lama_k, os.path.join(a.keluar, "data", "kalender.json"))

    # salin aplikasi
    for f in os.listdir(os.path.join(AKAR, "app")):
        shutil.copy2(os.path.join(AKAR, "app", f), a.keluar)
    open(os.path.join(a.keluar, ".nojekyll"), "w").close()
    print(f"Selesai: situs di {a.keluar}")


if __name__ == "__main__":
    main()
