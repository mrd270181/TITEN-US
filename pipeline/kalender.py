# kalender.py - kalender earnings (Nasdaq, otomatis tiap hari) + kalender makro AS (jadwal resmi Fed & BLS)
# Semua keluaran dalam bahasa Indonesia, jam dikonversi ke WIB (otomatis mengikuti jam musim panas/dingin AS).
import json, os, time, urllib.request
from datetime import date, datetime, time as jam, timedelta
from zoneinfo import ZoneInfo

NY, WIB = ZoneInfo("America/New_York"), ZoneInfo("Asia/Jakarta")
BULAN = ["Januari", "Februari", "Maret", "April", "Mei", "Juni", "Juli", "Agustus",
         "September", "Oktober", "November", "Desember"]

# Jadwal resmi (federalreserve.gov/monetarypolicy/fomccalendars.htm, bls.gov/schedule/news_release/cpi.htm,
# bls.gov/schedule/news_release/empSit.htm). Perbarui daftar ini sekali setahun saat jadwal baru terbit.
FOMC = [  # (hari terakhir rapat = hari pengumuman, ada proyeksi ekonomi/dot plot?)
    ("2026-01-28", False), ("2026-03-18", True), ("2026-04-29", False), ("2026-06-17", True),
    ("2026-07-29", False), ("2026-09-16", True), ("2026-10-28", False), ("2026-12-09", True),
    ("2027-01-27", False), ("2027-03-17", None), ("2027-04-28", False), ("2027-06-09", None),
    ("2027-07-28", False), ("2027-09-15", None), ("2027-10-27", False), ("2027-11-17", None),
]
CPI = [  # (tanggal rilis, bulan data)
    ("2026-11-10", "2026-10"), ("2026-12-10", "2026-11"), ("2027-01-13", "2026-12"), ("2027-02-11", "2027-01"),
    ("2027-03-10", "2027-02"), ("2027-04-13", "2027-03"), ("2027-05-12", "2027-04"), ("2027-06-10", "2027-05"),
    ("2027-07-14", "2027-06"), ("2027-08-11", "2027-07"), ("2027-09-14", "2027-08"), ("2027-10-14", "2027-09"),
    ("2027-11-10", "2027-10"), ("2027-12-10", "2027-11"),
]
NFP = [
    ("2026-11-06", "2026-10"), ("2026-12-04", "2026-11"), ("2027-01-08", "2026-12"), ("2027-02-05", "2027-01"),
    ("2027-03-05", "2027-02"), ("2027-04-02", "2027-03"), ("2027-05-07", "2027-04"), ("2027-06-04", "2027-05"),
    ("2027-07-02", "2027-06"), ("2027-08-06", "2027-07"), ("2027-09-03", "2027-08"), ("2027-10-08", "2027-09"),
    ("2027-11-05", "2027-10"), ("2027-12-03", "2027-11"),
]

KET_FOMC = ("Pengumuman suku bunga pukul 14.00 waktu New York, disusul konferensi pers Ketua The Fed. "
            "Penggerak terbesar seluruh pasar: bunga naik atau ditahan lebih lama biasanya menekan saham "
            "(terutama teknologi), sinyal penurunan bunga biasanya mengangkat.")
KET_CPI = ("Laju inflasi harga konsumen. Inflasi di atas perkiraan membuat peluang bunga turun mengecil "
           "sehingga saham biasanya tertekan; di bawah perkiraan biasanya positif.")
KET_NFP = ("Jumlah lapangan kerja baru dan tingkat pengangguran. Angka yang terlalu kuat atau terlalu lemah "
           "sama-sama bisa mengguncang pasar karena memengaruhi arah suku bunga The Fed.")


def ke_wib(tgl, jam_ny):
    t = datetime.combine(date.fromisoformat(tgl), jam_ny, NY).astimezone(WIB)
    return t.date().isoformat(), t.strftime("%H.%M")


def nama_bulan(ym):
    y, m = ym.split("-")
    return f"{BULAN[int(m) - 1]} {y}"


def makro(dari, sampai):
    hasil = []
    for tgl, sep in FOMC:
        tw, jw = ke_wib(tgl, jam(14, 0))
        hasil.append({"tanggal": tgl, "tanggal_wib": tw, "jam_wib": jw, "jenis": "FOMC",
                      "judul": "Keputusan suku bunga The Fed (FOMC)" + (" + proyeksi ekonomi (dot plot)" if sep else ""),
                      "keterangan": KET_FOMC, "dampak": "tinggi"})
    for tgl, ym in CPI:
        tw, jw = ke_wib(tgl, jam(8, 30))
        hasil.append({"tanggal": tgl, "tanggal_wib": tw, "jam_wib": jw, "jenis": "CPI",
                      "judul": f"Inflasi AS (CPI) bulan {nama_bulan(ym)}", "keterangan": KET_CPI, "dampak": "tinggi"})
    for tgl, ym in NFP:
        tw, jw = ke_wib(tgl, jam(8, 30))
        hasil.append({"tanggal": tgl, "tanggal_wib": tw, "jam_wib": jw, "jenis": "NFP",
                      "judul": f"Data tenaga kerja AS (NFP) bulan {nama_bulan(ym)}", "keterangan": KET_NFP, "dampak": "tinggi"})
    return sorted((h for h in hasil if dari <= h["tanggal"] <= sampai), key=lambda h: (h["tanggal_wib"], h["jam_wib"]))


def ambil_nasdaq(tgl):
    req = urllib.request.Request(f"https://api.nasdaq.com/api/calendar/earnings?date={tgl}", headers={
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36",
        "Accept": "application/json, text/plain, */*", "Accept-Language": "en-US,en;q=0.9",
        "Origin": "https://www.nasdaq.com", "Referer": "https://www.nasdaq.com/"})
    with urllib.request.urlopen(req, timeout=60) as r:
        data = json.load(r)
    baris = ((data.get("data") or {}).get("rows")) or []
    return [b for b in baris if isinstance(b, dict) and b.get("symbol")]


def angka(x):
    """'$1.23' / '($0.45)' / 'N/A' -> float atau None"""
    if x is None:
        return None
    s = str(x).strip().replace("$", "").replace(",", "")
    neg = s.startswith("(") and s.endswith(")")
    s = s.strip("()")
    try:
        v = float(s)
    except ValueError:
        return None
    return -v if neg else v


def kuartal_id(x):
    """'Oct/2026' -> 'kuartal berakhir Okt 2026'"""
    if not x:
        return None
    eng = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]
    ind = ["Jan", "Feb", "Mar", "Apr", "Mei", "Jun", "Jul", "Agu", "Sep", "Okt", "Nov", "Des"]
    b, _, y = str(x).partition("/")
    return f"kuartal berakhir {ind[eng.index(b)]} {y}" if b in eng else str(x)


def waktu_lapor(kode, tgl):
    """Kode waktu Nasdaq -> (kunci, label Indonesia dengan perkiraan jam WIB)."""
    if kode == "time-pre-market":
        tw, jw = ke_wib(tgl, jam(7, 0))
        return "pra", f"Sebelum bursa buka (±{jw} WIB)"
    if kode == "time-after-hours":
        tw, jw = ke_wib(tgl, jam(16, 5))
        hari = "besoknya" if tw != tgl else ""
        return "pasca", f"Setelah bursa tutup (±{jw} WIB {hari})".replace(" )", ")")
    return "?", "Jam belum diumumkan"


def earnings(tickers, dari, sampai):
    hasil, gagal, d = [], 0, date.fromisoformat(dari)
    while d <= date.fromisoformat(sampai):
        if d.weekday() < 5:
            tgl = d.isoformat()
            try:
                for b in ambil_nasdaq(tgl):
                    t = str(b["symbol"]).strip().upper().replace(".", "-").replace("/", "-")
                    if t not in tickers:
                        continue
                    kunci, label = waktu_lapor(b.get("time"), tgl)
                    eps_akt, eps_prk = angka(b.get("eps")), angka(b.get("epsForecast"))
                    hasil.append({
                        "ticker": t, "tanggal": tgl, "waktu": kunci, "waktu_label": label,
                        "kuartal": kuartal_id(b.get("fiscalQuarterEnding")),
                        "eps_perkiraan": eps_prk, "jumlah_analis": angka(b.get("noOfEsts")),
                        "eps_tahun_lalu": angka(b.get("lastYearEPS")), "eps_aktual": eps_akt,
                        "kejutan_persen": angka(str(b.get("surprise", "")).replace("%", "")) if b.get("surprise") not in (None, "", "N/A") else None,
                    })
            except Exception as e:
                gagal += 1
                print(f"   earnings {tgl} gagal ({e})")
            time.sleep(0.6)
        d += timedelta(days=1)
    return hasil, gagal


def label_waktu(kunci, tgl):
    if kunci == "pra":
        return f"Sebelum bursa buka (±{ke_wib(tgl, jam(7, 0))[1]} WIB)"
    if kunci == "pasca":
        tw, jw = ke_wib(tgl, jam(16, 5))
        return f"Setelah bursa tutup (±{jw} WIB{' besoknya' if tw != tgl else ''})"
    return "Jam belum diumumkan"


def earnings_yahoo(tickers, dari, sampai):
    """Kalender earnings Yahoo (yfinance.Calendars): 100 jadwal per permintaan, urut kapitalisasi terbesar."""
    import logging
    import pandas as pd
    import yfinance as yf
    logging.getLogger("yfinance").setLevel(logging.CRITICAL)
    kal = yf.Calendars(start=dari, end=sampai)
    potong, off = [], 0
    while off < 4000:
        df = kal.get_earnings_calendar(market_cap=2e9, filter_most_active=False, start=dari, end=sampai,
                                       limit=100, offset=off, force=True)
        if df is None or df.empty:
            break
        potong.append(df.reset_index())
        if len(df) < 100:
            break
        off += 100
        time.sleep(1)
    if not potong:
        raise RuntimeError("kalender Yahoo kosong")
    df = pd.concat(potong, ignore_index=True)
    hasil = {}
    for _, b in df.iterrows():
        t = str(b.get("Symbol", "")).strip().upper().replace(".", "-")
        if t not in tickers or pd.isna(b.get("Event Start Date")):
            continue
        waktu = pd.Timestamp(b["Event Start Date"])
        waktu = (waktu.tz_localize("UTC") if waktu.tzinfo is None else waktu).tz_convert(NY)
        tgl = waktu.date().isoformat()
        kode = str(b.get("Timing") or "").upper()
        kunci = ("pra" if kode == "BMO" else "pasca" if kode == "AMC" else
                 "pra" if (waktu.hour, waktu.minute) < (9, 30) and kode in ("TAS", "") and waktu.hour > 0 else
                 "pasca" if waktu.hour >= 16 and kode in ("TAS", "") else "?")
        nilai = lambda k: None if pd.isna(b.get(k)) else round(float(b.get(k)), 4)
        baris = {"ticker": t, "tanggal": tgl, "waktu": kunci, "waktu_label": label_waktu(kunci, tgl),
                 "kuartal": None, "eps_perkiraan": nilai("EPS Estimate"), "jumlah_analis": None,
                 "eps_tahun_lalu": None, "eps_aktual": nilai("Reported EPS"), "kejutan_persen": nilai("Surprise(%)")}
        lama = hasil.get((t, tgl))
        if lama is None or (lama["eps_aktual"] is None and baris["eps_aktual"] is not None):
            hasil[(t, tgl)] = baris
    return list(hasil.values())


def tulis(keluar, tickers, lama=None):
    hari_ini = date.today()
    dari, sampai = (hari_ini - timedelta(days=14)).isoformat(), (hari_ini + timedelta(days=35)).isoformat()
    sumber = "Yahoo Finance"
    try:
        er, gagal = earnings_yahoo(set(tickers), dari, sampai), 0
    except Exception as e:
        print(f"   kalender Yahoo gagal ({e}) - coba Nasdaq")
        sumber = "Nasdaq"
        er, gagal = earnings(set(tickers), dari, sampai)
    hari_kerja = sum(1 for i in range(50) if (date.fromisoformat(dari) + timedelta(days=i)).weekday() < 5
                     and (date.fromisoformat(dari) + timedelta(days=i)).isoformat() <= sampai)
    if (gagal > hari_kerja / 2 or not er) and lama and os.path.exists(lama):
        sumber = "sebelumnya"
        print(f"   PERINGATAN: kalender earnings {gagal} hari gagal - pakai kalender earnings sebelumnya")
        er = json.load(open(lama, encoding="utf-8")).get("earnings", [])
    obj = {"dibuat": datetime.now(WIB).isoformat(timespec="minutes"), "dari": dari, "sampai": sampai, "sumber": sumber,
           "earnings": sorted(er, key=lambda x: (x["tanggal"], x["ticker"])),
           "makro": makro((hari_ini - timedelta(days=14)).isoformat(), (hari_ini + timedelta(days=120)).isoformat())}
    with open(os.path.join(keluar, "data", "kalender.json"), "w", encoding="utf-8") as fh:
        json.dump(obj, fh, ensure_ascii=False, separators=(",", ":"))
    print(f"kalender: {len(obj['earnings'])} jadwal earnings ({sumber}), {len(obj['makro'])} acara makro")
    return obj
