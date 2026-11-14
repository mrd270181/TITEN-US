# ekspor.py - tulis data untuk aplikasi: data/ringkasan.json, data/emiten/<TICKER>.json, data/manifest.json
import hashlib, json, math, os
from datetime import datetime, timezone

KOL_RINGKAS = ["ticker", "nama", "sektor", "sp500", "ndx", "dow", "tanggal", "open", "high", "low", "close", "volume",
               "nilai_transaksi", "chg_1h", "gap_open", "chg_1mg", "chg_1bl", "chg_3bl", "chg_ytd", "chg_1th",
               "vol_ma5", "vol_ma20", "vol_x_ma5", "vol_x_ma20", "ma20", "ma50", "ma200", "tinggi_52m", "rendah_52m",
               "dari_tinggi_52m", "saham", "sumber_saham", "kap_pasar", "pendapatan_ttm", "laba_bersih_ttm",
               "lk_terakhir", "per", "psr", "pbv", "roe", "npm", "gpm", "opm", "liab_ekuitas",
               "tumbuh_pendapatan", "tumbuh_laba", "fcf_ttm", "fcf_yield",
               "skor_lk", "label_lk", "label_val", "teks_val", "label_analis", "upside_analis", "label_berita", "teks_berita"]
POS = ["pendapatan", "laba_kotor", "laba_usaha", "laba_sebelum_pajak", "laba_bersih", "eps_dilusi",
       "arus_kas_operasi", "belanja_modal", "total_aset", "total_liabilitas", "ekuitas", "kas", "utang_jangka_panjang"]


def bersih(x, d=4):
    if x is None:
        return None
    if isinstance(x, bool) or isinstance(x, str):
        return x
    if hasattr(x, "isoformat"):
        return x.isoformat()
    x = float(x)
    if math.isnan(x) or math.isinf(x):
        return None
    if abs(x) >= 1e5:
        return round(x)
    return round(x, d)


def tulis_json(jalur, obj):
    teks = json.dumps(obj, separators=(",", ":"), ensure_ascii=False)
    with open(jalur, "w", encoding="utf-8") as fh:
        fh.write(teks)
    return hashlib.sha1(teks.encode()).hexdigest()[:12]


def tulis(con, keluar, batas, info_jalan, rek=None):
    data_dir = os.path.join(keluar, "data")
    os.makedirs(os.path.join(data_dir, "emiten"), exist_ok=True)
    baris = con.execute(f"SELECT {', '.join(KOL_RINGKAS)} FROM ringkasan ORDER BY kap_pasar DESC NULLS LAST").fetchall()
    sidik = {"ringkasan.json": tulis_json(os.path.join(data_dir, "ringkasan.json"),
                                          {"kolom": KOL_RINGKAS, "baris": [[bersih(v) for v in b] for b in baris]})}
    tickers = [b[0] for b in baris]

    # harga per emiten (seluruh isi tabel harga s.d. batas, ~2 tahun)
    harga = {}
    for t, tg, o, h, l, c, v in con.execute(f"""SELECT ticker, tanggal, open, high, low, close, volume FROM harga
                                               WHERE tanggal <= DATE '{batas}' ORDER BY ticker, tanggal""").fetchall():
        a = harga.setdefault(t, {"t": [], "o": [], "h": [], "l": [], "c": [], "v": []})
        a["t"].append(tg.isoformat())
        for k, x in (("o", o), ("h", h), ("l", l), ("c", c)):
            a[k].append(None if x is None else round(float(x), 4 if float(x) < 1 else 2))
        a["v"].append(int(v or 0))

    # laporan keuangan per emiten: 12 kuartal + 10 tahun terakhir, dalam juta USD (EPS dalam USD)
    lk = {}
    for t, pos, tb, per, akhir, nilai, tr in con.execute("""
        WITH p AS (SELECT ticker, tahun_buku, periode, MAX(akhir) akhir FROM lk GROUP BY ALL),
        pq AS (SELECT *, ROW_NUMBER() OVER (PARTITION BY ticker, periode = 'FY' ORDER BY akhir DESC) urut FROM p)
        SELECT l.ticker, l.pos, l.tahun_buku, l.periode, pq.akhir, l.nilai, l.turunan
        FROM lk l JOIN pq USING (ticker, tahun_buku, periode)
        WHERE (pq.periode = 'FY' AND urut <= 10) OR (pq.periode <> 'FY' AND urut <= 12)""").fetchall():
        if pos not in POS:
            continue
        kel = "FY" if per == "FY" else "Q"
        lab = f"FY{tb}" if per == "FY" else f"{per} {tb}"
        p = lk.setdefault(t, {"Q": {}, "FY": {}})[kel].setdefault(lab, {"akhir": akhir.isoformat(), "tr": []})
        p[pos] = nilai
        if tr:
            p["tr"].append(pos)
    for t, e in lk.items():
        for kel in ("Q", "FY"):
            per = sorted(({"label": k, **v} for k, v in e[kel].items()), key=lambda x: x["akhir"])
            e[kel] = {"label": [p["label"] for p in per], "akhir": [p["akhir"] for p in per],
                      "turunan": [p["tr"] for p in per],
                      "pos": {pos: [(None if p.get(pos) is None else
                                     (round(p[pos], 2) if pos == "eps_dilusi" else round(p[pos] / 1e6, 1)))
                                    for p in per] for pos in POS}}
    cik = dict(con.execute("SELECT ticker, MAX(cik) FROM fakta GROUP BY 1").fetchall())
    akhir_fy = dict(con.execute("""SELECT ticker, strftime(MAX(akhir), '%d-%m') FROM lk
                                   WHERE periode = 'FY' GROUP BY 1""").fetchall())
    for t in tickers:
        tulis_json(os.path.join(data_dir, "emiten", f"{t}.json"),
                   {"harga": harga.get(t), "lk": lk.get(t), "cik": cik.get(t), "akhir_fy": akhir_fy.get(t),
                    "rek": (rek or {}).get(t)})

    # harga gabungan semua emiten untuk menu ANALISA: kalender bersama, [close[], volume/1000[]] per ticker
    tanggal = sorted({d for a in harga.values() for d in a["t"]})
    posisi = {d: i for i, d in enumerate(tanggal)}
    semua = {}
    for t, a in harga.items():
        c, v = [None] * len(tanggal), [None] * len(tanggal)
        for i, d in enumerate(a["t"]):
            c[posisi[d]] = a["c"][i]
            v[posisi[d]] = round((a["v"][i] or 0) / 1000)
        semua[t] = [c, v]
    sidik["harga-semua.json"] = tulis_json(os.path.join(data_dir, "harga-semua.json"), {"t": tanggal, "d": semua})

    versi = datetime.now(timezone.utc).strftime("%Y%m%d%H%M")
    tulis_json(os.path.join(data_dir, "manifest.json"), {
        "versi": versi, "dibuat": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "batas": str(batas), "jumlah_emiten": len(tickers), "sidik": sidik, **info_jalan})
    print(f"ekspor: {len(tickers)} emiten, data per {batas}, versi {versi}")
