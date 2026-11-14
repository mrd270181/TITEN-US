# rekomendasi.py - kartu REKOMENDASI per emiten (seperti TITEN): Kesimpulan LK, Valuasi, Sentimen analis,
# Sentimen berita. Semua label Bagus / Sedang / Jelek, keterangan bahasa Indonesia. BUKAN saran beli/jual.
#
# Skor LK mengikuti aturan TITEN (index.html lkSkor, 10-27 Okt 2026):
#   Bukan bank: Profitabilitas 20, Pertumbuhan 20, Kualitas laba 20, Utang & likuiditas 15, Konsistensi 10,
#   Arah margin 10, Arus kas bebas 5. Lembaga keuangan: Profitabilitas 40, Pertumbuhan 20, Konsistensi 20,
#   Arah margin 20 (diubah ke skala 0-100). Tanda bahaya berat -> paling tinggi 59, ringan -> -10 per tanda.
#   Label: >= 75 Bagus, 60-74 Sedang, < 60 Jelek.
# Penyesuaian AS: ekuitas negatif karena pembelian kembali saham (laba & arus kas operasi positif, umum di AS:
#   HD, MCD, dll.) BUKAN tanda bahaya; profitabilitasnya dinilai dengan ROA.
import html, json, logging, os, re, time, urllib.parse, urllib.request
import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime

LABEL = lambda s: None if s is None else ("Bagus" if s >= 75 else "Sedang" if s >= 60 else "Jelek")
ACU = {"Profitabilitas": "bagus jika ROE ≥ 15%", "Pertumbuhan": "bagus jika pendapatan & laba naik ≥ 10%",
       "Kualitas laba": "bagus jika arus kas operasi ≥ laba bersih",
       "Utang & likuiditas": "penuh jika liabilitas/ekuitas ≤ 1× (sektor padat modal ≤ 2×); ⚠ Utang besar jika > 3×",
       "Konsistensi": "bagus jika untung 4 tahun berturut-turut", "Arah margin": "bagus jika margin naik atau tetap",
       "Arus kas bebas": "bagus jika positif, atau negatif karena belanja modal (ekspansi) sementara arus kas operasi positif"}
PADAT = {"Real Estate", "Utilities"}

# kata penanda sentimen di judul - Indonesia: sama persis dengan TITEN scripts/news.py
POSITIF_ID = ['naik', 'menguat', 'melonjak', 'melesat', 'meroket', 'terbang', 'rebound', 'reli', 'rally',
              'laba', 'untung', 'cuan', 'dividen', 'buyback', 'akuisisi', 'ekspansi', 'rekor', 'tertinggi',
              'positif', 'net buy', 'borong', 'diborong', 'kontrak baru', 'tumbuh', 'optimis', 'hijau',
              'upgrade', 'surplus', 'lampaui', 'penguatan', 'lonjakan',
              'menghijau', 'apresiasi', 'menanjak', 'bangkit', 'pulih']
NEGATIF_ID = ['turun', 'melemah', 'anjlok', 'ambruk', 'ambles', 'amblas', 'merosot', 'terkoreksi', 'koreksi',
              'rugi', 'merugi', 'gagal bayar', 'pailit', 'suspensi', 'disuspensi', 'suspend',
              'delisting', 'net sell', 'dilepas', 'negatif', 'tertekan', 'merah', 'longsor',
              'terjun', 'waspada', 'gugatan', 'sanksi', 'downgrade', 'defisit',
              'terendah', 'jatuh', 'kabur', 'outflow', 'boncos', 'pelemahan', 'penurunan',
              'memerah', 'depresiasi', 'tumbang', 'rontok', 'ambyar']
POSITIF_EN = ['beat', 'beats', 'tops', 'surge', 'surges', 'soar', 'soars', 'jump', 'jumps', 'rally', 'rallies',
              'record', 'upgrade', 'upgrades', 'upgraded', 'raises', 'raised', 'boost', 'boosts', 'strong',
              'outperform', 'bullish', 'gain', 'gains', 'rise', 'rises', 'climb', 'climbs', 'higher', 'growth',
              'wins', 'approval', 'approved', 'partnership', 'expands', 'dividend', 'buyback', 'rebound', 'top pick']
NEGATIF_EN = ['miss', 'misses', 'plunge', 'plunges', 'sink', 'sinks', 'tumble', 'tumbles', 'drop', 'drops',
              'fall', 'falls', 'slump', 'slumps', 'slide', 'slides', 'downgrade', 'downgrades', 'downgraded',
              'lowers', 'weak', 'loss', 'losses', 'lawsuit', 'sued', 'probe', 'investigation', 'recall', 'layoffs',
              'bearish', 'warning', 'warns', 'decline', 'declines', 'fraud', 'antitrust', 'fined', 'halt', 'selloff',
              'sell-off', 'underperform', 'cuts guidance', 'cuts outlook']
REKOM_ID = {"strong_buy": "Beli kuat", "buy": "Beli", "hold": "Tahan", "underperform": "Kurang menarik",
            "sell": "Jual", "strong_sell": "Jual kuat", "none": "–"}
UA_PERAMBAN = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
               "Chrome/126.0 Safari/537.36")


def nada(judul, bahasa):
    t = " " + judul.lower() + " "
    pos, neg = (POSITIF_ID, NEGATIF_ID) if bahasa == "id" else (POSITIF_EN, NEGATIF_EN)
    p = sum(1 for x in pos if re.search(r"\b" + re.escape(x) + r"\b", t))
    n = sum(1 for x in neg if re.search(r"\b" + re.escape(x) + r"\b", t))
    return "positif" if p > n else "negatif" if n > p else "netral"


# ------------------------------------------------------------------ Kesimpulan LK
def data_lk(con):
    """Angka mentah per emiten dari tabel lk (USD)."""
    q = {}
    for t, pos, akhir, nilai, urut in con.execute("""
        SELECT ticker, pos, akhir, nilai, ROW_NUMBER() OVER (PARTITION BY ticker, pos ORDER BY akhir DESC)
        FROM lk WHERE periode LIKE 'Q%' AND satuan = 'USD'""").fetchall():
        if urut <= 8:
            q.setdefault(t, {}).setdefault(pos, {})[urut] = (akhir, nilai)
    fy = {}
    for t, nilai, urut in con.execute("""
        SELECT ticker, nilai, ROW_NUMBER() OVER (PARTITION BY ticker ORDER BY akhir DESC)
        FROM lk WHERE periode = 'FY' AND pos = 'laba_bersih'""").fetchall():
        if urut <= 4:
            fy.setdefault(t, []).append(nilai)
    return q, fy


def ttm(seri, awal):
    """Jumlah 4 kuartal berurutan mulai urutan `awal` (1 = terbaru)."""
    if not seri:
        return None
    k = [seri.get(i) for i in range(awal, awal + 4)]
    if any(x is None for x in k) or (k[0][0] - k[3][0]).days > 300:
        return None
    return sum(x[1] for x in k)


def skor_lk(t, sektor, q, fy):
    s = q.get(t)
    if not s or "laba_bersih" not in s:
        return None
    g = lambda n, o: None if n is None or not o else (n - o) / abs(o) * 100
    saldo = lambda pos, i=1: (s.get(pos) or {}).get(i, (None, None))[1]
    pend, pend1 = ttm(s.get("pendapatan"), 1), ttm(s.get("pendapatan"), 5)
    li, li1 = ttm(s.get("laba_bersih"), 1), ttm(s.get("laba_bersih"), 5)
    cfo, capex = ttm(s.get("arus_kas_operasi"), 1), ttm(s.get("belanja_modal"), 1)
    if li is None:
        return None
    eq, eq1, liab, aset = saldo("ekuitas"), saldo("ekuitas", 5), saldo("total_liabilitas"), saldo("total_aset")
    lancar, liab_lancar = saldo("aset_lancar"), saldo("liabilitas_lancar")
    keu = sektor == "Financials" and lancar is None
    padat = sektor in PADAT
    eq_neg = eq is not None and eq <= 0
    buyback = eq_neg and li > 0 and cfo is not None and cfo > 0      # penyesuaian AS
    roe = (li / ((eq + eq1) / 2) * 100 if eq1 and eq1 > 0 else li / eq * 100) if eq and eq > 0 else None
    roa = li / aset * 100 if aset else None
    gp, gl = g(pend, pend1), g(li, li1)
    npm = li / pend * 100 if pend else None
    npm1 = li1 / pend1 * 100 if (li1 is not None and pend1) else None
    der = liab / eq if (liab is not None and eq and eq > 0) else None
    cr = lancar / liab_lancar if (lancar and liab_lancar) else None
    f1 = lambda x: "-" if x is None else f"{x:,.1f}".replace(",", "X").replace(".", ",").replace("X", ".").replace("-", "−")
    pc = lambda x: "-" if x is None else ("+" if x > 0 else "−" if x < 0 else "") + f"{abs(round(x))}%"
    cat, tot = [], [0]

    def tambah(n, p, m, teks, acu=None):
        if p is not None:
            tot[0] += p
        cat.append({"n": n, "p": p, "m": m, "t": teks, "acu": acu or ACU.get(n, "")})

    if roe is not None:
        pr = 20 if roe >= 15 else 12 if roe >= 8 else 5 if roe > 0 else 0
        tambah("Profitabilitas", pr * 2 if keu else pr, 40 if keu else 20, f"ROE {f1(roe)}%")
    elif buyback and roa is not None:
        pr = 20 if roa >= 10 else 12 if roa >= 5 else 5 if roa > 0 else 0
        tambah("Profitabilitas", pr, 20, f"ROA {f1(roa)}% — ekuitas negatif karena pembelian kembali saham, ROE tidak berlaku",
               "bagus jika ROA ≥ 10% (dipakai karena ekuitas negatif akibat buyback)")
    else:
        tambah("Profitabilitas", 0, 40 if keu else 20, "ROE tidak bisa dihitung (ekuitas negatif)")
    p_laba = 0 if li <= 0 else 5 if (li1 is not None and li1 <= 0) else 10 if (gl is not None and gl >= 10) else 5 if (gl is not None and gl >= 0) else 0
    t_laba = (f"laba {pc(gl)} tapi masih rugi" if li <= 0 else "laba berbalik untung" if (li1 is not None and li1 <= 0) else f"laba {pc(gl)}")
    if pend is not None:
        tambah("Pertumbuhan", (10 if gp is not None and gp >= 10 else 5 if gp is not None and gp >= 0 else 0) + p_laba, 20,
               f"pendapatan {pc(gp)}, {t_laba} (4 kuartal terakhir vs 4 kuartal sebelumnya)")
    else:
        tambah("Pertumbuhan", 5 + p_laba, 20, f"{t_laba}; pendapatan tidak tersedia, diberi nilai tengah 5")
    if keu:
        tambah("Kualitas laba", None, 20, "tidak dinilai untuk lembaga keuangan", "")
    else:
        c = cfo / li if (cfo is not None and li > 0) else None
        tambah("Kualitas laba", 20 if c is not None and c >= 1 else 12 if c is not None and c >= 0.7 else 4 if c is not None and c > 0 else 0, 20,
               f"arus kas operasi {f1(c)}× laba bersih" if c is not None else "laba atau arus kas operasi negatif")
    der_longgar = not keu and roe is not None and roe >= 30 and li > 0 and cfo is not None and cfo > 0 and der is not None and der <= 4
    b1, b2 = (2, 3.5) if padat else (1, 2)
    if keu:
        tambah("Utang & likuiditas", None, 15, "tidak dinilai: utang lembaga keuangan sebagian besar dana nasabah", "")
    else:
        if buyback:
            pd_, k = 5, "ekuitas negatif karena buyback — liabilitas/ekuitas tidak berlaku, diberi nilai tengah"
        else:
            pd_ = 10 if der_longgar else 10 if (der is not None and der <= b1) else 5 if (der is not None and der <= b2) else 0
            k = f"liabilitas/ekuitas {f1(der)}×" + (f" (ROE {f1(roe)}% — modal kecil karena laba dibagikan, tidak dihukum)" if der_longgar else
                                                     " (sektor padat modal)" if padat else "")
        if cr is not None:
            tambah("Utang & likuiditas", pd_ + (5 if cr >= 1.5 else 2 if cr >= 1 else 0), 15, f"{k}, rasio lancar {f1(cr)}×")
        else:
            tambah("Utang & likuiditas", pd_ + 2, 15, f"{k}; rasio lancar belum tersedia, diberi nilai tengah 2")
    kons = sum(1 for v in fy.get(t, []) if v is not None and v > 0)
    tambah("Konsistensi", [0, 3, 7, 12, 20][kons] if keu else [0, 2, 4, 7, 10][kons], 20 if keu else 10,
           f"untung {kons} dari {len(fy.get(t, []))} tahun buku terakhir")
    if pend is None:
        tambah("Arah margin", 10 if keu else 5, 20 if keu else 10, "pendapatan tidak tersedia, diberi nilai tengah")
    else:
        mg = 2 if (npm is not None and npm1 is not None and npm >= npm1 and npm > 0) else 1 if (npm is not None and npm > 0) else 0
        tambah("Arah margin", [0, 8, 20][mg] if keu else [0, 4, 10][mg], 20 if keu else 10,
               f"margin bersih {f1(npm)}% (setahun sebelumnya {f1(npm1)}%)")
    fcf = cfo - (capex or 0) if cfo is not None else None
    if keu:
        tambah("Arus kas bebas", None, 5, "tidak dinilai untuk lembaga keuangan", "")
    else:
        eksp = fcf is not None and fcf <= 0 and cfo > 0 and (capex or 0) > 0
        tambah("Arus kas bebas", 5 if fcf is not None and (fcf > 0 or eksp) else 0, 5,
               "-" if fcf is None else "positif" if fcf > 0 else "negatif karena belanja modal (ekspansi); arus kas operasi positif, tidak dihukum" if eksp else "negatif")
    juta = lambda x: "-" if x is None else ("minus " if x < 0 else "") + f"${abs(x) / 1e9:,.1f} M".replace(",", "X").replace(".", ",").replace("X", ".")
    F = []   # [judul, bukti, arti, berat?]
    if eq_neg and not buyback:
        F.append(["Ekuitas negatif", f"ekuitas {juta(eq)}", "Utang lebih besar dari seluruh aset dan perusahaan tidak sedang untung — modal pemegang saham habis secara buku.", True])
    if li < 0:
        F.append(["Rugi", f"laba bersih 4 kuartal {juta(li)}", "Perusahaan merugi dalam 4 kuartal terakhir, sehingga modal tergerus.", True])
    if not keu and cfo is not None and cfo < 0:
        F.append(["Arus kas operasi negatif", f"arus kas operasi 4 kuartal {juta(cfo)}", "Kegiatan usaha justru menghabiskan uang; untuk menutupnya perusahaan bisa perlu utang atau saham baru.", False])
    if not keu and not der_longgar and der is not None and der > 3:
        F.append(["Utang besar", f"liabilitas {f1(der)}× ekuitas", "Kewajiban lebih dari 3 kali modal sendiri; kalau usaha melemah perusahaan bisa kesulitan bayar.", False])
    maks = sum(c["m"] for c in cat if c["p"] is not None)
    asli = round(tot[0] * 100 / maks)
    ringan, berat = [f for f in F if not f[3]], [f for f in F if f[3]]
    lama = min(asli, 59) if F else asli
    baru = max(0, asli - 10 * len(ringan))
    baru = 59 if berat and baru > 59 else baru
    skor = max(lama, baru)
    catatan = ["Ekuitas negatif karena pembelian kembali saham (buyback) — umum di emiten AS yang untung besar, tidak dianggap tanda bahaya."] if buyback else []
    return {"skor": skor, "skor_asli": asli, "label": LABEL(skor), "keu": keu, "rinci": cat,
            "bahaya": [{"judul": f[0], "bukti": f[1], "arti": f[2], "berat": f[3]} for f in F], "catatan": catatan,
            "periode": max((x[0].isoformat() for x in (s.get("laba_bersih") or {}).values()), default=None)}


# ------------------------------------------------------------------ Valuasi (P/E & PBV vs median sektor)
def valuasi(rows):
    from statistics import median
    per_s, pbv_s = {}, {}
    for r in rows.values():
        if r["per"] and 0 < r["per"] < 300:
            per_s.setdefault(r["sektor"], []).append(r["per"])
        if r["pbv"] and 0 < r["pbv"] < 100:
            pbv_s.setdefault(r["sektor"], []).append(r["pbv"])
    hasil = {}
    for t, r in rows.items():
        mp = median(per_s[r["sektor"]]) if len(per_s.get(r["sektor"], [])) >= 5 else None
        mb = median(pbv_s[r["sektor"]]) if len(pbv_s.get(r["sektor"], [])) >= 5 else None
        if r["per"] and r["per"] > 0 and mp:
            rasio = r["per"] / mp
            label, teks = ("Bagus", "Murah") if rasio <= 0.8 else ("Sedang", "Wajar") if rasio <= 1.25 else ("Jelek", "Mahal")
            ket = f"P/E {r['per']:.1f} vs median sektor {mp:.1f} ({rasio:.2f}×)".replace(".", ",")
        elif r["laba_bersih_ttm"] is not None and r["laba_bersih_ttm"] < 0:
            label, teks, ket = "Jelek", "Rugi", "P/E tidak berlaku karena laba 4 kuartal terakhir negatif"
        else:
            label, teks, ket = None, "–", "data belum cukup"
        hasil[t] = {"label": label, "teks": teks, "ket": ket, "per": r["per"], "per_sektor": mp, "pbv": r["pbv"],
                    "pbv_sektor": mb, "fcf_yield": r["fcf_yield"]}
    return hasil


# ------------------------------------------------------------------ Yahoo: sentimen analis + berita Inggris
def yahoo_satu(t):
    import yfinance as yf
    tk = yf.Ticker(t)
    analis, berita = None, []
    try:
        i = tk.info or {}
        if i.get("numberOfAnalystOpinions"):
            m = i.get("recommendationMean")
            label = None if m is None else "Bagus" if m <= 2.0 else "Sedang" if m <= 3.0 else "Jelek"
            analis = {"label": label, "rata2": m, "jumlah": i.get("numberOfAnalystOpinions"),
                      "kunci": REKOM_ID.get(i.get("recommendationKey") or "none", i.get("recommendationKey")),
                      "target": i.get("targetMeanPrice"), "target_tinggi": i.get("targetHighPrice"),
                      "target_rendah": i.get("targetLowPrice")}
    except Exception:
        pass
    try:
        batas = datetime.now(timezone.utc) - timedelta(days=7)
        for n in (tk.news or [])[:15]:
            c = n.get("content") or n
            judul = c.get("title")
            waktu = c.get("pubDate") or c.get("displayTime")
            if waktu:
                w = datetime.fromisoformat(waktu.replace("Z", "+00:00"))
            elif c.get("providerPublishTime"):
                w = datetime.fromtimestamp(c["providerPublishTime"], timezone.utc)
            else:
                continue
            if not judul or w < batas:
                continue
            url = ((c.get("canonicalUrl") or {}).get("url") or (c.get("clickThroughUrl") or {}).get("url") or c.get("link"))
            sumber = (c.get("provider") or {}).get("displayName") or c.get("publisher") or "Yahoo Finance"
            berita.append({"judul": judul, "sumber": sumber, "waktu": w.isoformat(timespec="minutes"), "url": url,
                           "bahasa": "en", "nada": nada(judul, "en")})
    except Exception:
        pass
    return t, analis, berita


def nama_pendek(nama):
    n = re.sub(r"\(.*?\)", "", nama or "")
    n = re.sub(r"[,.]", " ", n)
    buang = {"inc", "corp", "corporation", "company", "co", "plc", "ltd", "holdings", "holding", "group", "the",
             "incorporated", "class", "a", "b", "c", "platforms", "technologies", "systems", "international", "n", "v", "sa", "se", "ag", "nv"}
    kata = [k for k in n.split() if k.lower() not in buang]
    return " ".join(kata[:2]) if kata else (nama or "")


def google_id(t, nama):
    """Google Berita berbahasa Indonesia, 7 hari terakhir (seperti TITEN news.py)."""
    q = f'"{nama_pendek(nama)}" saham when:7d'
    url = "https://news.google.com/rss/search?" + urllib.parse.urlencode({"q": q, "hl": "id", "gl": "ID", "ceid": "ID:id"})
    req = urllib.request.Request(url, headers={"User-Agent": UA_PERAMBAN})
    with urllib.request.urlopen(req, timeout=30) as r:
        akar = ET.fromstring(r.read())
    hasil = []
    for it in akar.iter("item"):
        judul = html.unescape(it.findtext("title") or "")
        sumber = it.findtext("source") or ""
        if sumber and judul.endswith(" - " + sumber):
            judul = judul[: -len(sumber) - 3]
        try:
            w = parsedate_to_datetime(it.findtext("pubDate")).astimezone(timezone.utc)
        except Exception:
            continue
        hasil.append({"judul": judul, "sumber": sumber or "Google Berita", "waktu": w.isoformat(timespec="minutes"),
                      "url": it.findtext("link"), "bahasa": "id", "nada": nada(judul, "id")})
    return hasil[:10]


def label_berita(item):
    pos = sum(1 for b in item if b["nada"] == "positif")
    neg = sum(1 for b in item if b["nada"] == "negatif")
    if not item:
        return {"label": None, "teks": "Tidak ada berita", "pos": 0, "neg": 0, "jumlah": 0}
    sk = (pos - neg) / max(1, pos + neg)
    label, teks = (("Bagus", "Positif") if sk >= 0.3 and pos >= 2 else ("Jelek", "Negatif") if sk <= -0.3 and neg >= 2 else ("Sedang", "Netral"))
    return {"label": label, "teks": teks, "pos": pos, "neg": neg, "jumlah": len(item)}


# ------------------------------------------------------------------ utama
def hitung(con, folder_lama=None):
    logging.getLogger("yfinance").setLevel(logging.CRITICAL)
    kol = ["ticker", "nama", "sektor", "per", "pbv", "fcf_yield", "laba_bersih_ttm", "kap_pasar", "close", "dow"]
    rows = {r[0]: dict(zip(kol, r)) for r in con.execute(f"SELECT {', '.join(kol)} FROM ringkasan").fetchall()}
    q, fy = data_lk(con)
    lk = {t: skor_lk(t, r["sektor"], q, fy) for t, r in rows.items()}
    val = valuasi(rows)
    print(f"rekomendasi: skor LK {sum(1 for v in lk.values() if v)} emiten")

    # Yahoo (analis + berita Inggris), paralel ringan
    yahoo = {}
    with ThreadPoolExecutor(max_workers=6) as ex:
        for t, a, b in ex.map(yahoo_satu, sorted(rows)):
            yahoo[t] = (a, b)
    ada_analis = sum(1 for a, _ in yahoo.values() if a)
    print(f"   Yahoo: analis {ada_analis} emiten, berita {sum(len(b) for _, b in yahoo.values())} judul")

    # Google Berita Indonesia: 150 kapitalisasi terbesar + Dow
    urut = sorted(rows.values(), key=lambda r: -(r["kap_pasar"] or 0))
    pilih = {r["ticker"] for r in urut[:150]} | {t for t, r in rows.items() if r["dow"]}
    gid, gagal = {}, 0
    for t in sorted(pilih, key=lambda x: -(rows[x]["kap_pasar"] or 0)):
        if t == "GOOG" or t == "FOX" or t == "NWS":      # kelas saham kedua: beritanya sama dengan kelas pertama
            continue
        try:
            gid[t] = google_id(t, rows[t]["nama"])
        except Exception:
            gagal += 1
        time.sleep(0.6)
    gid["GOOG"], gid["FOX"], gid["NWS"] = gid.get("GOOGL", []), gid.get("FOXA", []), gid.get("NWSA", [])
    print(f"   Google Berita (id): {sum(len(v) for v in gid.values())} judul untuk {len(gid)} emiten, gagal {gagal}")

    # cadangan: kalau Yahoo kosong untuk emiten, pakai data kemarin
    def lama(t):
        try:
            return json.load(open(os.path.join(folder_lama, "data", "emiten", f"{t}.json"), encoding="utf-8")).get("rek") or {}
        except Exception:
            return {}

    rek, kolom = {}, {}
    for t in rows:
        a, b_en = yahoo.get(t, (None, []))
        if a is None or not b_en:
            l = lama(t)
            a = a or l.get("analis")
            if not b_en:
                b_en = [x for x in (l.get("berita") or {}).get("item", []) if x.get("bahasa") == "en"]
        item = sorted(gid.get(t, []) + b_en, key=lambda x: x["waktu"], reverse=True)[:20]
        br = {**label_berita(item), "item": item}
        if a and a.get("target") and rows[t]["close"]:
            a["upside"] = round((a["target"] / rows[t]["close"] - 1) * 100, 1)
        rek[t] = {"lk": lk[t], "valuasi": val[t], "analis": a, "berita": br}
        kolom[t] = {"skor_lk": lk[t]["skor"] if lk[t] else None, "label_lk": lk[t]["label"] if lk[t] else None,
                    "label_val": val[t]["label"], "teks_val": val[t]["teks"],
                    "label_analis": a["label"] if a else None, "upside_analis": a.get("upside") if a else None,
                    "label_berita": br["label"], "teks_berita": br["teks"]}
    return rek, kolom
