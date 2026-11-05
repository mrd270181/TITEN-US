# sec.py - laporan keuangan dari SEC EDGAR (companyfacts) -> baris tabel lk. Diadaptasi dari tarik_lk_us4.py
# Tarik laporan keuangan emiten AS dari SEC EDGAR ke DuckDB, lalu rapikan jadi tabel lk
import csv, json, os, sys, tempfile, time, urllib.request
from datetime import date, timedelta
import duckdb

UA = os.environ.get("SEC_USER_AGENT") or "TITEN-US titen-us@users.noreply.github.com"
FOLDER = os.path.dirname(os.path.abspath(__file__))
DB = os.path.join(FOLDER, "us.duckdb")
FORM_OK = {"10-K", "10-K/A", "10-Q", "10-Q/A", "20-F", "20-F/A", "40-F", "40-F/A"}
FORM_TAHUNAN = {"10-K", "10-K/A", "20-F", "20-F/A", "40-F", "40-F/A"}

# Emiten yang ganti nomor CIK (induk baru / reorganisasi): riwayat lama ada di CIK lama.
# Angka dari CIK baru tetap menang kalau periodenya sama.
CIK_LAMA = {
    "XOM": [34088],      # Exxon Mobil Corp (CIK lama sebelum reorganisasi)
    "GOOGL": [1288776],  # Google Inc. (sebelum induk Alphabet, 2015)
    "GOOG": [1288776],
    "DIS": [1001039],    # Walt Disney lama / TWDC Enterprises 18 (sebelum akuisisi Fox, 2019)
}

# pos: (jenis, bisa_dijumlah, tag menurut urutan prioritas)
#  durasi = angka selama periode (laba rugi, arus kas) ; posisi = angka per tanggal (neraca)
#  bisa_dijumlah False = Q4 tidak boleh dihitung dengan pengurangan (mis. EPS)
#  pos berawalan "_" = bahan hitungan saja, tidak disimpan ke tabel lk
PETA = {
    "pendapatan": ("durasi", True, [
        "us-gaap:Revenues",
        "us-gaap:RevenueFromContractWithCustomerExcludingAssessedTax",
        "us-gaap:RevenueFromContractWithCustomerIncludingAssessedTax",
        "us-gaap:RevenuesNetOfInterestExpense",          # bank & kartu kredit (GS, JPM, AXP)
        "us-gaap:SalesRevenueNet",
        "us-gaap:SalesRevenueGoodsNet",
        "ifrs-full:Revenue"]),
    "_beban_pokok": ("durasi", True, [
        "us-gaap:CostOfRevenue",
        "us-gaap:CostOfGoodsAndServicesSold",
        "us-gaap:CostOfGoodsSold",
        "ifrs-full:CostOfSales"]),
    "laba_sebelum_pajak": ("durasi", True, [
        "us-gaap:IncomeLossFromContinuingOperationsBeforeIncomeTaxesExtraordinaryItemsNoncontrollingInterest",
        "us-gaap:IncomeLossFromContinuingOperationsBeforeIncomeTaxesMinorityInterestAndIncomeLossFromEquityMethodInvestments",
        "us-gaap:IncomeLossFromContinuingOperationsBeforeIncomeTaxesDomestic",
        "ifrs-full:ProfitLossBeforeTax"]),
    "laba_kotor": ("durasi", True, ["us-gaap:GrossProfit", "ifrs-full:GrossProfit"]),
    "laba_usaha": ("durasi", True, [
        "us-gaap:OperatingIncomeLoss", "ifrs-full:ProfitLossFromOperatingActivities"]),
    "laba_bersih": ("durasi", True, [
        "us-gaap:NetIncomeLoss",
        "us-gaap:NetIncomeLossAvailableToCommonStockholdersBasic",
        "us-gaap:ProfitLoss",
        "ifrs-full:ProfitLossAttributableToOwnersOfParent",
        "ifrs-full:ProfitLoss"]),
    "eps_dilusi": ("durasi", False, [
        "us-gaap:EarningsPerShareDiluted", "us-gaap:EarningsPerShareBasic",
        "ifrs-full:DilutedEarningsPerShare", "ifrs-full:BasicEarningsPerShare"]),
    "arus_kas_operasi": ("durasi", True, [
        "us-gaap:NetCashProvidedByUsedInOperatingActivities",
        "us-gaap:NetCashProvidedByUsedInOperatingActivitiesContinuingOperations",
        "ifrs-full:CashFlowsFromUsedInOperatingActivities"]),
    "belanja_modal": ("durasi", True, [
        "us-gaap:PaymentsToAcquirePropertyPlantAndEquipment",
        "us-gaap:PaymentsToAcquireProductiveAssets",
        "ifrs-full:PurchaseOfPropertyPlantAndEquipmentClassifiedAsInvestingActivities"]),
    "total_aset": ("posisi", False, ["us-gaap:Assets", "ifrs-full:Assets"]),
    "total_liabilitas": ("posisi", False, ["us-gaap:Liabilities", "ifrs-full:Liabilities"]),
    "_liabilitas_dan_ekuitas": ("posisi", False, [
        "us-gaap:LiabilitiesAndStockholdersEquity", "ifrs-full:EquityAndLiabilities"]),
    "_ekuitas_total": ("posisi", False, [
        "us-gaap:StockholdersEquityIncludingPortionAttributableToNoncontrollingInterest",
        "ifrs-full:Equity"]),
    "ekuitas": ("posisi", False, [
        "us-gaap:StockholdersEquity",
        "us-gaap:StockholdersEquityIncludingPortionAttributableToNoncontrollingInterest",
        "ifrs-full:EquityAttributableToOwnersOfParent", "ifrs-full:Equity"]),
    "kas": ("posisi", False, [
        "us-gaap:CashAndCashEquivalentsAtCarryingValue",
        "us-gaap:CashCashEquivalentsRestrictedCashAndRestrictedCashEquivalents",
        "us-gaap:Cash",
        "us-gaap:CashAndDueFromBanks",                    # bank
        "ifrs-full:CashAndCashEquivalents"]),
    "utang_jangka_panjang": ("posisi", False, [
        "us-gaap:LongTermDebtNoncurrent", "us-gaap:LongTermDebt",
        "us-gaap:LongTermDebtAndCapitalLeaseObligations"]),
}


def ambil(url):
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=60) as r:
        return json.load(r)


def gabung_fakta(utama, tambahan):
    """Gabungkan companyfacts CIK lama ke CIK utama (daftar angka per tag/satuan disambung)."""
    for taks, tags in tambahan.get("facts", {}).items():
        tujuan = utama.setdefault("facts", {}).setdefault(taks, {})
        for tag, isi in tags.items():
            t = tujuan.setdefault(tag, {"units": {}})
            for satuan, daftar in isi.get("units", {}).items():
                t.setdefault("units", {}).setdefault(satuan, []).extend(daftar)
    return utama


def hari(a, b):
    return (b - a).days


def kumpulkan(data, taks_tag):
    """Angka satu tag: {(mulai, akhir): nilai}, diambil dari laporan yang PALING BARU diajukan."""
    taks, tag = taks_tag.split(":")
    isi = data.get("facts", {}).get(taks, {}).get(tag)
    if not isi:
        return None, {}, set()
    units = isi.get("units", {})
    satuan = next((u for u in units if u in ("USD", "USD/shares")), next(iter(units), None))
    terbaru, tahunan = {}, set()
    for f in units.get(satuan, []):
        if f.get("form") not in FORM_OK or not f.get("end") or f.get("val") is None:
            continue
        m = date.fromisoformat(f["start"]) if f.get("start") else None
        a = date.fromisoformat(f["end"])
        if m and f.get("form") in FORM_TAHUNAN:
            tahunan.add((m, a))
        filed = f.get("filed", "")
        if (m, a) not in terbaru or filed > terbaru[(m, a)][0]:
            terbaru[(m, a)] = (filed, float(f["val"]))
    return satuan, {k: v[1] for k, v in terbaru.items()}, tahunan


def kalender(semua):
    """Tanggal awal tiap tahun buku, HANYA dari angka 12 bulan di laporan tahunan (10-K/20-F).
    Angka 12 bulan terakhir (TTM) di laporan kuartalan diabaikan agar tidak jadi tahun buku palsu."""
    awal = set()
    for periode in semua:
        for (m, a) in periode:
            if 350 <= hari(m, a) <= 380:
                awal.add(m)
                awal.add(a + timedelta(days=1))   # awal tahun buku berikutnya
    hasil = []
    for s in sorted(awal):
        if not hasil or hari(hasil[-1], s) > 10:
            hasil.append(s)
    return hasil


def susun_durasi(fakta, awal_list, bisa_jumlah):
    """Q1-Q4 + FY per tahun buku. Kuartal yang tidak dilaporkan dihitung dari angka kumulatif."""
    hasil = []
    for S in awal_list:
        batas = S + timedelta(days=380)
        kum = {a: v for (m, a), v in fakta.items()
               if m and abs(hari(S, m)) <= 3 and 80 <= hari(m, a) <= 380}
        disk = {a: (m, v) for (m, a), v in fakta.items()
                if m and 80 <= hari(m, a) <= 100 and hari(S, m) >= -3 and a <= batas}
        # nomor kuartal ditentukan dari jarak ke awal tahun buku (±91 hari per kuartal)
        ujung = {}
        for a in sorted(set(kum) | set(disk)):
            k = round((hari(S, a) + 1) / 91.3)
            if 1 <= k <= 4 and k not in ujung:
                ujung[k] = a
        tb = (S + timedelta(days=360)).year
        kum_k = {0: 0.0}            # angka kumulatif sampai akhir kuartal ke-k
        for k in range(1, 5):
            if k not in ujung:
                continue
            a = ujung[k]
            m_awal = ujung[k - 1] + timedelta(days=1) if k - 1 in ujung else (S if k == 1 else None)
            sebelum = kum_k.get(k - 1)
            if a in disk:
                m, q = disk[a]
                turunan = False
            elif bisa_jumlah and a in kum and sebelum is not None and m_awal is not None:
                m, q, turunan = m_awal, kum[a] - sebelum, True
            else:
                q = None
            if a in kum:
                kum_k[k] = kum[a]
            elif q is not None and sebelum is not None:
                kum_k[k] = sebelum + q
            if q is not None:
                hasil.append((tb, f"Q{k}", m, a, q, turunan))
        fy = [(a, v) for a, v in kum.items() if hari(S, a) >= 350]
        if fy:
            a, v = max(fy)
            hasil.append((tb, "FY", S, a, v, False))
    return hasil


def proses(ticker, cik, data):
    # 1. semua tag yang dipetakan
    per_tag = {}
    for pos, (jenis, _, tags) in PETA.items():
        for t in tags:
            per_tag[t] = kumpulkan(data, t)
    tahunan = [per_tag[t][2] for pos, (j, _, tags) in PETA.items() if j == "durasi" for t in tags]
    awal_list = kalender(tahunan)

    hasil, label = {}, {}   # hasil[pos][(tahun_buku, periode)] = (mulai, akhir, nilai, satuan, tag, turunan)
    # 2. laba rugi & arus kas
    for pos, (jenis, bisa_jumlah, tags) in PETA.items():
        if jenis != "durasi":
            continue
        terpilih = hasil.setdefault(pos, {})
        for t in tags:   # tag prioritas lebih tinggi menang per periode
            satuan, fakta, _ = per_tag[t]
            for tb, per, m, a, v, tr in susun_durasi(fakta, awal_list, bisa_jumlah):
                if (tb, per) not in terpilih:
                    terpilih[(tb, per)] = (m, a, v, satuan, t, tr)
                    label.setdefault(a, (tb, "Q4" if per == "FY" else per))

    # 3. neraca: cocokkan tanggal dengan akhir kuartal yang sudah dikenal
    tanggal_label = sorted(label)
    for pos, (jenis, _, tags) in PETA.items():
        if jenis != "posisi":
            continue
        terpilih = hasil.setdefault(pos, {})
        for t in tags:
            satuan, fakta, _ = per_tag[t]
            for (m, a), v in fakta.items():
                if m is not None:
                    continue
                cocok = next((d for d in tanggal_label if abs(hari(d, a)) <= 3), None)
                if cocok is None:
                    continue
                tb, per = label[cocok]
                for p in ([per, "FY"] if per == "Q4" else [per]):
                    if (tb, p) not in terpilih:
                        terpilih[(tb, p)] = (None, a, v, satuan, t, False)

    # 4. isi yang kosong dengan hitungan (ditandai turunan = true)
    def turunkan(target, kiri, kanan, nama):
        for kunci, (m, a, v1, satuan, _, tr1) in hasil[kiri].items():
            if kunci in hasil[target] or kunci not in hasil[kanan]:
                continue
            v2 = hasil[kanan][kunci][2]
            hasil[target][kunci] = (m, a, v1 - v2, satuan, nama, True)

    turunkan("laba_kotor", "pendapatan", "_beban_pokok", "hitung:pendapatan-beban_pokok")
    turunkan("total_liabilitas", "_liabilitas_dan_ekuitas", "_ekuitas_total",
             "hitung:liabilitas_dan_ekuitas-ekuitas_total")
    turunkan("total_liabilitas", "_liabilitas_dan_ekuitas", "ekuitas",
             "hitung:liabilitas_dan_ekuitas-ekuitas")

    baris = []
    for pos, isi in hasil.items():
        if pos.startswith("_"):
            continue
        for (tb, per), (m, a, v, satuan, t, tr) in isi.items():
            baris.append((ticker, pos, tb, per, m, a, v, satuan, t, tr))
    return baris



TAG_SAHAM = {"EntityCommonStockSharesOutstanding", "WeightedAverageNumberOfDilutedSharesOutstanding",
             "WeightedAverageNumberOfSharesOutstandingBasicAndDiluted", "WeightedAverageNumberOfSharesOutstandingBasic",
             "AdjustedWeightedAverageShares", "WeightedAverageShares"}


def peta_cik():
    peta = ambil("https://www.sec.gov/files/company_tickers.json")
    return {v["ticker"].upper(): (int(v["cik_str"]), v["title"]) for v in peta.values()}


def tarik_satu(ticker, cik):
    """Ambil companyfacts (+ CIK lama bila ada) -> (baris_lk, baris_fakta_saham)."""
    data = ambil(f"https://data.sec.gov/api/xbrl/companyfacts/CIK{cik:010d}.json")
    for lama in CIK_LAMA.get(ticker, []):
        if lama != cik:
            try:
                time.sleep(0.15)
                gabung_fakta(data, ambil(f"https://data.sec.gov/api/xbrl/companyfacts/CIK{lama:010d}.json"))
            except Exception as e:
                print(f"   {ticker}: CIK lama {lama} gagal ({e})")
    fakta = []
    for taks, tags in data.get("facts", {}).items():
        for tag, isi in tags.items():
            if tag not in TAG_SAHAM:
                continue
            for satuan, daftar in isi.get("units", {}).items():
                for f in daftar:
                    if f.get("val") is None or not f.get("end"):
                        continue
                    fakta.append((ticker, cik, taks, tag, satuan, f.get("start"), f.get("end"),
                                  float(f["val"]), f.get("form"), f.get("filed")))
    return proses(ticker, cik, data), fakta
