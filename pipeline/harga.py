# harga.py - harga & volume harian dari Yahoo Finance (yfinance) untuk semua emiten.
# Setiap jalan menarik ulang ~2,2 tahun terakhir, jadi tidak perlu menyimpan riwayat di antara jalan.
import logging, time
from datetime import date, timedelta
import pandas as pd
import yfinance as yf

logging.getLogger("yfinance").setLevel(logging.CRITICAL)
HARI_RIWAYAT = 800      # hari kalender (~2,2 tahun: cukup untuk MA200, 52 minggu, dan chart 2 tahun)
UKURAN = 50
JEDA = 2
KOLOM = {"Open": "open", "High": "high", "Low": "low", "Close": "close",
         "Adj Close": "adj_close", "Volume": "volume"}


def pecah(df, kelompok):
    """Hasil yf.download (kolom bertingkat Price x Ticker) -> baris panjang."""
    if df is None or df.empty:
        return pd.DataFrame()
    potong = []
    if isinstance(df.columns, pd.MultiIndex):
        lv_h = 0 if "Close" in df.columns.get_level_values(0) else 1
        for t in df.columns.get_level_values(1 - lv_h).unique():
            sub = df.xs(t, axis=1, level=1 - lv_h).copy()
            sub["ticker"] = t
            potong.append(sub)
    else:
        sub = df.copy()
        sub["ticker"] = kelompok[0]
        potong.append(sub)
    h = pd.concat(potong).rename(columns=KOLOM)
    for k in KOLOM.values():
        if k not in h.columns:
            h[k] = None
    h.index.name = "tanggal"
    h = h.reset_index()
    h["tanggal"] = pd.to_datetime(h["tanggal"]).dt.tz_localize(None).dt.date
    h = h.dropna(subset=["close"])
    return h[["ticker", "tanggal", "open", "high", "low", "close", "adj_close", "volume"]]


def unduh(kelompok, mulai, akhir, paralel=True):
    return pecah(yf.download(kelompok, start=mulai, end=akhir, auto_adjust=False, group_by="column",
                             threads=paralel, progress=False), kelompok)


def tarik(con, tickers):
    """Isi ulang tabel harga. Mengembalikan daftar ticker yang tetap tanpa data."""
    mulai = (date.today() - timedelta(days=HARI_RIWAYAT)).isoformat()
    akhir = (date.today() + timedelta(days=1)).isoformat()
    con.execute("""CREATE OR REPLACE TABLE harga (ticker VARCHAR, tanggal DATE, open DOUBLE, high DOUBLE,
                   low DOUBLE, close DOUBLE, adj_close DOUBLE, volume BIGINT)""")
    kosong = []
    for n, i in enumerate(range(0, len(tickers), UKURAN), 1):
        kelompok = tickers[i:i + UKURAN]
        try:
            data = unduh(kelompok, mulai, akhir)
        except Exception as e:
            print(f"   kelompok {n} gagal ({e}), dicoba satu per satu")
            data = pd.DataFrame()
        ada = set(data["ticker"]) if not data.empty else set()
        sisa = [t for t in kelompok if t not in ada]
        if sisa:                       # coba ulang tanpa thread (mis. galat 'database is locked')
            time.sleep(JEDA)
            ulang = []
            for t in sisa:
                try:
                    u = unduh([t], mulai, akhir, paralel=False)
                    if not u.empty:
                        ulang.append(u)
                except Exception:
                    pass
            if ulang:
                data = pd.concat([data] + ulang, ignore_index=True)
            ada = set(data["ticker"]) if not data.empty else set()
            kosong += [t for t in kelompok if t not in ada]
        if not data.empty:
            con.register("df_harga", data)
            con.execute("""INSERT INTO harga SELECT ticker, tanggal, open, high, low, close, adj_close,
                           TRY_CAST(volume AS BIGINT) FROM df_harga""")
            con.unregister("df_harga")
        print(f"   harga kelompok {n}: {kelompok[0]}..{kelompok[-1]} {len(data):,} baris")
        time.sleep(JEDA)
    return kosong


def kurs_usd_idr():
    """Kurs penutupan USD/IDR terakhir dari Yahoo (IDR=X) -> (kurs, 'YYYY-MM-DD')."""
    akhir = (date.today() + timedelta(days=1)).isoformat()
    mulai = (date.today() - timedelta(days=14)).isoformat()
    d = unduh(["IDR=X"], mulai, akhir, paralel=False)
    if d.empty:
        raise RuntimeError("IDR=X kosong")
    b = d.sort_values("tanggal").iloc[-1]
    k = float(b["close"])
    if not 1000 < k < 100000:
        raise RuntimeError(f"kurs tidak masuk akal: {k}")
    return round(k, 2), b["tanggal"].isoformat()
