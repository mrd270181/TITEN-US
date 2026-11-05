# ringkasan.py - data pasar, rasio, indikator teknikal -> tabel ringkasan. Dari hitung_ringkasan_us.py
# Hitung data pasar, rasio fundamental, dan indikator teknikal untuk screener TITEN US.
# Membaca tabel emiten, harga, lk, fakta di us.duckdb -> menulis tabel saham_beredar dan ringkasan.
# Pakai: python hitung_ringkasan_us.py            (sampai tanggal harga terakhir)
#        python hitung_ringkasan_us.py 2026-11-03 (sampai tanggal tertentu, mis. kalau hari terakhir belum final)
import os, sys
import duckdb

FOLDER = os.path.dirname(os.path.abspath(__file__))
DB = os.path.join(FOLDER, "us.duckdb")
# Saham kelas khusus: jumlah saham di laporan dalam satuan kelas lain.
# BRK-B: Berkshire melapor dalam "setara saham kelas A"; 1 saham A = 1.500 saham B.
PENGALI_SAHAM = {"BRK-B": 1500}


def hitung(con, batas):
    print(f"Menghitung ringkasan sampai {batas}")

    # 1. Jumlah saham beredar ---------------------------------------------------------
    con.execute("CREATE OR REPLACE TEMP TABLE pengali (ticker VARCHAR, kali DOUBLE)")
    for t, k in PENGALI_SAHAM.items():
        con.execute("INSERT INTO pengali VALUES (?, ?)", [t, k])
    con.execute(f"""
    CREATE OR REPLACE TABLE saham_beredar AS
    WITH sampul AS (      -- angka di sampul laporan (per tanggal tertentu)
      SELECT ticker, akhir, nilai FROM fakta
      WHERE tag = 'EntityCommonStockSharesOutstanding' AND nilai >= 1e6
      QUALIFY ROW_NUMBER() OVER (PARTITION BY ticker ORDER BY akhir DESC, filed DESC) = 1
    ),
    dilusi AS (           -- rata-rata tertimbang saham terdilusi, kuartal terakhir (us-gaap & IFRS)
      SELECT ticker, akhir, nilai FROM fakta
      WHERE tag IN ('WeightedAverageNumberOfDilutedSharesOutstanding',
                    'WeightedAverageNumberOfSharesOutstandingBasicAndDiluted',
                    'WeightedAverageNumberOfSharesOutstandingBasic',
                    'AdjustedWeightedAverageShares', 'WeightedAverageShares')
        AND nilai >= 1e6 AND mulai IS NOT NULL
      QUALIFY ROW_NUMBER() OVER (PARTITION BY ticker
              ORDER BY akhir DESC,
                       date_diff('day', mulai, akhir),            -- kuartal dulu, baru kumulatif
                       CASE tag WHEN 'WeightedAverageNumberOfDilutedSharesOutstanding' THEN 0
                                WHEN 'AdjustedWeightedAverageShares' THEN 0
                                WHEN 'WeightedAverageNumberOfSharesOutstandingBasicAndDiluted' THEN 1
                                ELSE 2 END,
                       filed DESC) = 1
    ),
    pilih AS (
      -- Sampul dipakai kalau masih segar (<= 400 hari) KECUALI angka dilusi 1,25-20x lebih besar
      -- (tanda sampul hanya memuat satu kelas saham). Rasio >= 20x = salah satuan di laporan
      -- emiten (mis. WAT menulis 90 miliar lembar), jadi sampul tetap dipakai.
      -- Sampul yang sudah basi diabaikan sama sekali (mis. GOOGL: sampul terakhir tahun 2010).
      SELECT e.ticker, s.akhir AS s_akhir, s.nilai AS s_nilai, d.akhir AS d_akhir, d.nilai AS d_nilai,
        COALESCE(p.kali, 1) AS kali,
        CASE WHEN s.akhir >= DATE '{batas}' - INTERVAL 400 DAY
                  AND (d.nilai IS NULL OR d.nilai / s.nilai < 1.25 OR d.nilai / s.nilai >= 20)
             THEN 'sampul'
             WHEN d.nilai IS NOT NULL THEN 'dilusi'
        END AS sumber
      FROM emiten e
      LEFT JOIN sampul s USING (ticker)
      LEFT JOIN dilusi d USING (ticker)
      LEFT JOIN pengali p USING (ticker)
    )
    SELECT ticker,
      CASE sumber WHEN 'sampul' THEN s_nilai WHEN 'dilusi' THEN d_nilai END * kali AS saham,
      sumber,
      CASE sumber WHEN 'sampul' THEN s_akhir WHEN 'dilusi' THEN d_akhir END AS per_tanggal
    FROM pilih
    """)

    # 1b. Cadangan yfinance untuk yang tidak punya angka saham di SEC (mis. V, STZ, TRI)
    kosong = [r[0] for r in con.execute(
        "SELECT ticker FROM saham_beredar WHERE saham IS NULL ORDER BY 1").fetchall()]
    if kosong:
        try:
            import logging
            import yfinance as yf
            logging.getLogger("yfinance").setLevel(logging.CRITICAL)
            for t in kosong:
                try:
                    n = yf.Ticker(t).fast_info["shares"]
                except Exception:
                    n = None
                if n and n > 0:
                    con.execute("""UPDATE saham_beredar SET saham = ?, sumber = 'yfinance',
                                   per_tanggal = DATE '{0}' WHERE ticker = ?""".format(batas), [float(n), t])
                    print(f"   saham {t} dari yfinance: {n:,.0f}")
        except ImportError:
            print("   (yfinance tidak terpasang - lewati cadangan saham)")

    # 2. Fundamental TTM & posisi neraca terakhir -------------------------------------
    con.execute("""
    CREATE OR REPLACE TEMP TABLE kuartal AS
    SELECT ticker, pos, akhir, nilai,
           ROW_NUMBER() OVER (PARTITION BY ticker, pos ORDER BY akhir DESC) AS urut
    FROM lk WHERE periode LIKE 'Q%' AND satuan = 'USD'
    """)
    con.execute("""
    CREATE OR REPLACE TEMP TABLE ttm AS
    SELECT ticker, pos,
      -- 4 kuartal terakhir, hanya kalau berurutan (akhir kuartal pertama s.d. terakhir <= 300 hari)
      CASE WHEN COUNT(*) FILTER (WHERE urut <= 4) = 4
            AND date_diff('day', MIN(akhir) FILTER (WHERE urut <= 4), MAX(akhir) FILTER (WHERE urut <= 4)) <= 300
           THEN SUM(nilai) FILTER (WHERE urut <= 4) END AS ttm,
      CASE WHEN COUNT(*) FILTER (WHERE urut BETWEEN 5 AND 8) = 4
            AND date_diff('day', MIN(akhir) FILTER (WHERE urut BETWEEN 5 AND 8), MAX(akhir) FILTER (WHERE urut BETWEEN 5 AND 8)) <= 300
           THEN SUM(nilai) FILTER (WHERE urut BETWEEN 5 AND 8) END AS ttm_lalu,
      MAX(nilai) FILTER (WHERE urut = 1) AS q_terakhir,
      MAX(akhir) FILTER (WHERE urut = 1) AS akhir_terakhir
    FROM kuartal
    WHERE pos IN ('pendapatan','laba_kotor','laba_usaha','laba_bersih','arus_kas_operasi','belanja_modal')
    GROUP BY 1, 2
    """)
    con.execute("""
    CREATE OR REPLACE TEMP TABLE neraca AS
    SELECT ticker, pos, nilai FROM lk
    WHERE pos IN ('total_aset','total_liabilitas','ekuitas','kas','utang_jangka_panjang') AND periode LIKE 'Q%'
    QUALIFY ROW_NUMBER() OVER (PARTITION BY ticker, pos ORDER BY akhir DESC) = 1
    """)
    con.execute("""
    CREATE OR REPLACE TEMP TABLE funda AS
    SELECT t.ticker,
      MAX(ttm) FILTER (WHERE pos='pendapatan') AS pendapatan_ttm,
      MAX(ttm_lalu) FILTER (WHERE pos='pendapatan') AS pendapatan_ttm_lalu,
      MAX(ttm) FILTER (WHERE pos='laba_kotor') AS laba_kotor_ttm,
      MAX(ttm) FILTER (WHERE pos='laba_usaha') AS laba_usaha_ttm,
      MAX(ttm) FILTER (WHERE pos='laba_bersih') AS laba_bersih_ttm,
      MAX(ttm_lalu) FILTER (WHERE pos='laba_bersih') AS laba_bersih_ttm_lalu,
      MAX(ttm) FILTER (WHERE pos='arus_kas_operasi') AS ako_ttm,
      MAX(ttm) FILTER (WHERE pos='belanja_modal') AS capex_ttm,
      MAX(akhir_terakhir) FILTER (WHERE pos IN ('pendapatan','laba_bersih')) AS lk_terakhir
    FROM ttm t GROUP BY 1
    """)

    # 3. Teknikal dari harga harian ---------------------------------------------------
    con.execute(f"""
    CREATE OR REPLACE TEMP TABLE tek AS
    WITH h AS (
      SELECT ticker, tanggal, open, high, low, close, volume,
        LAG(close) OVER w AS close_kemarin,
        LAG(close, 5) OVER w AS close_5, LAG(close, 21) OVER w AS close_21,
        LAG(close, 63) OVER w AS close_63, LAG(close, 252) OVER w AS close_252,
        AVG(volume) OVER (w ROWS BETWEEN 5 PRECEDING AND 1 PRECEDING) AS vol_ma5,
        AVG(volume) OVER (w ROWS BETWEEN 20 PRECEDING AND 1 PRECEDING) AS vol_ma20,
        AVG(close) OVER (w ROWS BETWEEN 19 PRECEDING AND CURRENT ROW) AS ma20,
        AVG(close) OVER (w ROWS BETWEEN 49 PRECEDING AND CURRENT ROW) AS ma50,
        AVG(close) OVER (w ROWS BETWEEN 199 PRECEDING AND CURRENT ROW) AS ma200,
        MAX(high) OVER (w ROWS BETWEEN 251 PRECEDING AND CURRENT ROW) AS tinggi_52m,
        MIN(low) OVER (w ROWS BETWEEN 251 PRECEDING AND CURRENT ROW) AS rendah_52m,
        COUNT(*) OVER (w ROWS BETWEEN 199 PRECEDING AND CURRENT ROW) AS n200
      FROM harga WHERE tanggal <= DATE '{batas}'
      WINDOW w AS (PARTITION BY ticker ORDER BY tanggal)
    ),
    awal_tahun AS (
      SELECT ticker, close AS close_akhir_tahun_lalu FROM harga
      WHERE tanggal < DATE_TRUNC('year', DATE '{batas}')
      QUALIFY ROW_NUMBER() OVER (PARTITION BY ticker ORDER BY tanggal DESC) = 1
    )
    SELECT h.*, a.close_akhir_tahun_lalu FROM h LEFT JOIN awal_tahun a USING (ticker)
    QUALIFY ROW_NUMBER() OVER (PARTITION BY h.ticker ORDER BY tanggal DESC) = 1
    """)

    # 4. Gabung jadi ringkasan --------------------------------------------------------
    con.execute("""
    CREATE OR REPLACE TABLE ringkasan AS
    SELECT e.ticker, e.nama, e.sektor, e.sp500, e.ndx, e.dow,
      k.tanggal, k.open, k.high, k.low, k.close, k.volume,
      k.close * k.volume AS nilai_transaksi,
      100 * (k.close / k.close_kemarin - 1) AS chg_1h,
      100 * (k.open / k.close_kemarin - 1) AS gap_open,
      100 * (k.close / k.close_5 - 1) AS chg_1mg,
      100 * (k.close / k.close_21 - 1) AS chg_1bl,
      100 * (k.close / k.close_63 - 1) AS chg_3bl,
      100 * (k.close / k.close_akhir_tahun_lalu - 1) AS chg_ytd,
      100 * (k.close / k.close_252 - 1) AS chg_1th,
      k.vol_ma5, k.vol_ma20,
      k.volume / NULLIF(k.vol_ma5, 0) AS vol_x_ma5,
      k.volume / NULLIF(k.vol_ma20, 0) AS vol_x_ma20,
      k.ma20, k.ma50, CASE WHEN k.n200 = 200 THEN k.ma200 END AS ma200,
      k.tinggi_52m, k.rendah_52m,
      100 * (k.close / k.tinggi_52m - 1) AS dari_tinggi_52m,
      s.saham, s.sumber AS sumber_saham,
      k.close * s.saham AS kap_pasar,
      f.pendapatan_ttm, f.laba_bersih_ttm, f.lk_terakhir,
      CASE WHEN f.laba_bersih_ttm > 0 THEN k.close * s.saham / f.laba_bersih_ttm END AS per,
      CASE WHEN f.pendapatan_ttm > 0 THEN k.close * s.saham / f.pendapatan_ttm END AS psr,
      CASE WHEN eq.nilai > 0 THEN k.close * s.saham / eq.nilai END AS pbv,
      CASE WHEN eq.nilai > 0 THEN 100 * f.laba_bersih_ttm / eq.nilai END AS roe,
      CASE WHEN f.pendapatan_ttm > 0 THEN 100 * f.laba_bersih_ttm / f.pendapatan_ttm END AS npm,
      CASE WHEN f.pendapatan_ttm > 0 THEN 100 * f.laba_kotor_ttm / f.pendapatan_ttm END AS gpm,
      CASE WHEN f.pendapatan_ttm > 0 THEN 100 * f.laba_usaha_ttm / f.pendapatan_ttm END AS opm,
      CASE WHEN eq.nilai > 0 THEN li.nilai / eq.nilai END AS liab_ekuitas,
      CASE WHEN f.pendapatan_ttm_lalu > 0 THEN 100 * (f.pendapatan_ttm / f.pendapatan_ttm_lalu - 1) END AS tumbuh_pendapatan,
      CASE WHEN f.laba_bersih_ttm_lalu > 0 THEN 100 * (f.laba_bersih_ttm / f.laba_bersih_ttm_lalu - 1) END AS tumbuh_laba,
      f.ako_ttm - COALESCE(f.capex_ttm, 0) AS fcf_ttm,
      CASE WHEN k.close * s.saham > 0 THEN 100 * (f.ako_ttm - COALESCE(f.capex_ttm, 0)) / (k.close * s.saham) END AS fcf_yield
    FROM emiten e
    LEFT JOIN tek k USING (ticker)
    LEFT JOIN saham_beredar s USING (ticker)
    LEFT JOIN funda f USING (ticker)
    LEFT JOIN neraca eq ON eq.ticker = e.ticker AND eq.pos = 'ekuitas'
    LEFT JOIN neraca li ON li.ticker = e.ticker AND li.pos = 'total_liabilitas'
    ORDER BY kap_pasar DESC NULLS LAST
    """)

    n, kap, per = con.execute("SELECT COUNT(*), COUNT(kap_pasar), COUNT(per) FROM ringkasan").fetchone()
    print(f"ringkasan: {n} emiten | kapitalisasi {kap} | PER {per}")
