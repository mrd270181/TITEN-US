# daftar.py - anggota S&P 500 (Wikipedia), Nasdaq-100 (nasdaq.com, cadangan Wikipedia), Dow 30. Dari ambil_daftar_us3.py
# Ambil daftar anggota S&P 500 dan Nasdaq-100 dari Wikipedia, gabung dengan Dow 30,
# simpan ke tabel emiten di us.duckdb dan ke daftar.txt (daftar lama diarsipkan dulu).
import json, os, re, shutil, sys, urllib.request
from datetime import datetime
from html.parser import HTMLParser
import duckdb

FOLDER = os.path.dirname(os.path.abspath(__file__))
DB = os.path.join(FOLDER, "us.duckdb")
UA = "TITEN-US/1.0 (penelitian pribadi)"
SUMBER = {
    "sp500": "https://en.wikipedia.org/wiki/List_of_S%26P_500_companies",
    "ndx": "https://en.wikipedia.org/wiki/Nasdaq-100",
}
NASDAQ_API = "https://api.nasdaq.com/api/quote/list-type/nasdaq100"
UA_PERAMBAN = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
               "(KHTML, like Gecko) Chrome/126.0 Safari/537.36")
# Dow 30 per 29 Juni 2026 (Alphabet masuk, Verizon keluar). Ubah di sini kalau ada pergantian.
DOW = ["AAPL", "AMGN", "AMZN", "AXP", "BA", "CAT", "CRM", "CSCO", "CVX", "DIS",
       "GOOGL", "GS", "HD", "HON", "IBM", "JNJ", "JPM", "KO", "MCD", "MMM",
       "MRK", "MSFT", "NKE", "NVDA", "PG", "SHW", "TRV", "UNH", "V", "WMT"]


class PembacaTabel(HTMLParser):
    """Kumpulkan semua tabel HTML sebagai daftar baris (daftar teks sel)."""
    def __init__(self):
        super().__init__()
        self.tabel, self._stack, self._baris, self._sel, self._abaikan = [], [], None, None, 0

    def handle_starttag(self, tag, attrs):
        if tag == "table":
            self._stack.append([])
        elif tag == "tr" and self._stack:
            self._baris = []
        elif tag in ("td", "th") and self._baris is not None:
            self._sel = []
        elif tag in ("sup", "style", "script"):
            self._abaikan += 1

    def handle_endtag(self, tag):
        if tag in ("sup", "style", "script") and self._abaikan:
            self._abaikan -= 1
        elif tag in ("td", "th") and self._sel is not None:
            self._baris.append(" ".join("".join(self._sel).split()))
            self._sel = None
        elif tag == "tr" and self._baris is not None and self._stack:
            if self._baris:
                self._stack[-1].append(self._baris)
            self._baris = None
        elif tag == "table" and self._stack:
            self.tabel.append(self._stack.pop())

    def handle_data(self, data):
        if self._sel is not None and not self._abaikan:
            self._sel.append(data)


POLA_TICKER = re.compile(r"^[A-Z][A-Z0-9]{0,4}([.-][A-Z])?$")


def jadi_ticker(teks):
    """'AAPL' / 'NASDAQ: AAPL' / 'BRK.B' -> 'AAPL' / 'AAPL' / 'BRK-B'; selain itu None."""
    if not teks:
        return None
    t = teks.replace("\u00a0", " ").strip().split(":")[-1].strip().split(" ")[0]   # harus huruf besar asli
    return t.replace(".", "-") if POLA_TICKER.match(t) else None


def cari_kolom(header, *kata):
    for i, h in enumerate(header):
        if any(k in h.lower() for k in kata):
            return i
    return None


def baca_tabel(html):
    p = PembacaTabel()
    p.feed(html)
    return p.tabel


def anggota_dari_tabel(tabel, minimal):
    """Kenali kolom ticker dari ISINYA (>= 80% sel berbentuk kode saham), bukan dari judul kolom."""
    if len(tabel) < minimal:
        return None
    lebar = max(len(b) for b in tabel)
    data = [b for b in tabel[1:] if len(b) >= 2]
    terbaik, skor = None, 0
    for k in range(lebar):
        n = sum(1 for b in data if len(b) > k and jadi_ticker(b[k]))
        if n > skor:
            terbaik, skor = k, n
    if terbaik is None or skor < max(minimal, 0.8 * len(data)):
        return None
    header = tabel[0]
    k_s = cari_kolom(header, "gics sector", "sector", "industry")
    # kolom nama = kolom bukan-ticker & bukan-sektor dengan teks rata-rata terpanjang
    k_n, panjang = None, 0
    for k in range(lebar):
        if k in (terbaik, k_s):
            continue
        isi = [b[k] for b in data if len(b) > k]
        rata = sum(len(x) for x in isi) / max(len(isi), 1)
        if rata > panjang and sum(1 for x in isi if jadi_ticker(x)) < 0.5 * len(isi):
            k_n, panjang = k, rata
    k_nh = cari_kolom(header, "security", "company")
    if k_nh is not None and k_nh != terbaik:
        k_n = k_nh
    hasil = {}
    for b in data:
        t = jadi_ticker(b[terbaik]) if len(b) > terbaik else None
        if not t:
            continue
        nama = b[k_n] if k_n is not None and len(b) > k_n else ""
        sektor = b[k_s] if k_s is not None and len(b) > k_s else ""
        hasil[t] = (nama, sektor)
    return hasil if len(hasil) >= minimal else None


def ambil_anggota(url, minimal, berkas_cek):
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=60) as r:
        html = r.read().decode("utf-8", "replace")
    semua = baca_tabel(html)
    for tabel in sorted(semua, key=len, reverse=True):
        hasil = anggota_dari_tabel(tabel, minimal)
        if hasil:
            return hasil
    # gagal: simpan contoh isi tabel besar supaya bisa diperiksa
    with open(berkas_cek, "w", encoding="utf-8") as fh:
        fh.write(f"{url}\n{len(semua)} tabel ditemukan\n")
        for i, tabel in enumerate(semua):
            if len(tabel) >= 20:
                fh.write(f"\n--- tabel {i}: {len(tabel)} baris ---\n")
                for b in tabel[:6]:
                    fh.write(" | ".join(b) + "\n")
    return None


def cari_baris_symbol(obj):
    """Cari daftar dict yang punya kunci 'symbol' di mana pun dalam JSON."""
    if isinstance(obj, list) and obj and all(isinstance(x, dict) and "symbol" in x for x in obj):
        return obj
    if isinstance(obj, dict):
        obj = list(obj.values())
    if isinstance(obj, list):
        for x in obj:
            hasil = cari_baris_symbol(x)
            if hasil:
                return hasil
    return None


def ambil_ndx_nasdaq():
    req = urllib.request.Request(NASDAQ_API, headers={
        "User-Agent": UA_PERAMBAN, "Accept": "application/json, text/plain, */*",
        "Accept-Language": "en-US,en;q=0.9", "Origin": "https://www.nasdaq.com",
        "Referer": "https://www.nasdaq.com/"})
    with urllib.request.urlopen(req, timeout=60) as r:
        data = json.load(r)
    hasil = {}
    for b in cari_baris_symbol(data) or []:
        t = jadi_ticker(str(b.get("symbol", "")).strip())
        if t:
            nama = str(b.get("companyName") or b.get("name") or "")
            for buang in (" Common Stock", " Class A", " Class C", " American Depositary Shares",
                          " Ordinary Shares"):
                nama = nama.split(buang)[0]
            hasil[t] = (nama.strip(), "")
    return hasil if len(hasil) >= 90 else None



def daftar_emiten(folder_cek="."):
    """-> daftar (ticker, nama, sektor, sp500, ndx, dow). Gagal keras kalau S&P 500 tak terbaca."""
    sp = ambil_anggota(SUMBER["sp500"], 450, os.path.join(folder_cek, "cek_sp500.txt"))
    if not sp:
        raise RuntimeError("Tabel S&P 500 tidak terbaca")
    try:
        ndx = ambil_ndx_nasdaq()
    except Exception as e:
        print(f"Nasdaq-100: nasdaq.com gagal ({e}), coba Wikipedia")
        ndx = None
    if not ndx:
        ndx = ambil_anggota(SUMBER["ndx"], 90, os.path.join(folder_cek, "cek_ndx.txt")) or {}
    print(f"S&P 500 {len(sp)} | Nasdaq-100 {len(ndx) or 'TIDAK TERBACA'} | Dow {len(DOW)}")
    semua = sorted(set(sp) | set(ndx) | set(DOW))
    return [(t, *(sp.get(t) or ndx.get(t) or ("", "")), t in sp, t in ndx, t in DOW) for t in semua]
