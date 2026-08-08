#!/usr/bin/env python3
"""
pve-gdrive-backup test paketi.

  python3 tests/run_tests.py            # hepsi
  python3 tests/run_tests.py retention  # ada gore filtrele
  python3 tests/run_tests.py -v         # ayrintili

Gercek Drive'a veya gercek bir Proxmox'a dokunmaz: rclone ve pgrep sahte
surumlerle degistirilir (tests/mock/). Her test kendi gecici dizininde calisir.
"""
import os, re, sys, json, time, shutil, tempfile, subprocess, importlib.util, base64, urllib.request, urllib.error

os.environ.setdefault("PVE_GDRIVE_QUIET", "1")   # log satirlari test ciktisini bogmasin
KOK = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MOCK = os.path.join(KOK, "tests", "mock")
BETIK = os.path.join(KOK, "pve_gdrive.py")
AYRINTILI = "-v" in sys.argv
SUZGEC = [x for x in sys.argv[1:] if not x.startswith("-")]

TESTLER = []
def test(ad, grup="genel"):
    def sar(fn):
        TESTLER.append((grup, ad, fn)); return fn
    return sar

class Ortam:
    """Izole calisma dizini + sahte rclone + taze modul kopyasi."""
    def __init__(self, **cfg_ek):
        self.dizin = tempfile.mkdtemp(prefix="pgd-test-")
        self.dump = os.path.join(self.dizin, "dump"); os.makedirs(self.dump)
        self.db = os.path.join(self.dizin, "drive.json")
        self.cfg_yolu = os.path.join(self.dizin, "test.conf")
        cfg = {
            "log_file": os.path.join(self.dizin, "t.log"),
            "state_file": os.path.join(self.dizin, "state.json"),
            "ui_bind": "127.0.0.1", "ui_port": 0, "ui_user": "admin", "ui_pass": "sifre123",
            "browse_roots": [self.dizin], "captcha_enabled": False,
            "plans": [],
        }
        cfg.update(cfg_ek)
        self.yaz_cfg(cfg)
    def yaz_cfg(self, cfg):
        with open(self.cfg_yolu, "w") as f: json.dump(cfg, f, indent=1)
    def oku_cfg(self):
        with open(self.cfg_yolu) as f: return json.load(f)
    def plan(self, **ek):
        p = {"id": "p1", "name": "Test", "src_dir": self.dump, "remote": "gdrive:hedef",
             "keep_days": 3, "keep_count": 2, "drive_trash_days": 1, "min_age_min": 0,
             "wait_for_vzdump": False, "notify_success": False, "notify_failure": False,
             "weekly_report": False, "bwlimit": "off"}
        p.update(ek)
        c = self.oku_cfg(); c["plans"] = [p]; self.yaz_cfg(c)
        return p
    def dosya(self, ad, boyut=1024, yas_sn=7200):
        yol = os.path.join(self.dump, ad)
        with open(yol, "wb") as f: f.write(b"x" * boyut)
        t = time.time() - yas_sn
        os.utime(yol, (t, t))
        return yol
    def vzdump_seti(self, misafir, gun_once, uzanti="vma.zst"):
        """gun_once gun ONCESINE ait bir set uretir.

        Sabit tarih kullanmak testi kirilgan yapiyordu: gun sinirinin tam saatine
        denk gelen bir set, testin kostugu saate gore icerde veya disarida
        kaliyordu. Yarim gun kaydirarak sinira denk gelme ihtimali kaldirilir."""
        from datetime import datetime, timedelta
        t = datetime.now() - timedelta(days=gun_once, hours=12)
        return self.dosya(f"vzdump-{misafir}-{t:%Y_%m_%d-%H_%M_%S}.{uzanti}")
    def sunucu(self):
        """Gercek HTTP sunucusunu rastgele portta baslatir.

        Oturum/captcha yerine api_token kullanilir: testin konusu tasima
        katmani, giris akisi degil. temizle() sunucuyu kapatir."""
        import threading as _t
        G = self.modul()
        c = self.oku_cfg()
        c["api_token"] = "test-token-123"
        c["allow_networks"] = []            # 127.0.0.1 gecebilsin
        self.yaz_cfg(c); G.cfg(force=True)
        srv = G.ThreadingHTTPServer(("127.0.0.1", 0), G.H)
        srv.daemon_threads = True
        _t.Thread(target=srv.serve_forever, daemon=True).start()
        self._srv = srv
        taban = f"http://127.0.0.1:{srv.server_address[1]}"

        class Istemci:
            G = None                 # sunucunun kullandigi modul ornegi (bkz. modul())
            def istek(_s, yol, akis=False, zaman=5):
                r = urllib.request.Request(taban + yol,
                                           headers={"Authorization": "Bearer test-token-123"})
                return urllib.request.urlopen(r, timeout=zaman)
        # modul() her cagrida taze kopya uretiyor; sunucu ile test ayni ornegi
        # kullanmazsa OLAY nesneleri farkli olur ve olaylar bulusmaz.
        Istemci.G = G
        return Istemci()

    def modul(self):
        os.environ["PVE_GDRIVE_CONF"] = self.cfg_yolu
        os.environ["MOCK_DB"] = self.db
        os.environ["PATH"] = MOCK + os.pathsep + os.environ.get("PATH", "")
        sp = importlib.util.spec_from_file_location("pgd_" + str(id(self)), BETIK)
        m = importlib.util.module_from_spec(sp); sp.loader.exec_module(m)
        return m
    def drive_ekle(self, ad, boyut=1000, remote="gdrive:hedef"):
        """Sahte Drive'a hazir dosya koyar: silme/saklama testleri bir sey bulsun."""
        try:
            with open(self.db) as f: d = json.load(f)
        except Exception: d = {}
        d.setdefault("files", {}); d.setdefault("remotes", {"gdrive": "drive"})
        d["files"][f"{remote}/{ad}"] = {"remote": remote, "size": boyut,
                                        "mtime": int(time.time()), "trashed": False}
        with open(self.db, "w") as f: json.dump(d, f)

    def drive(self, copte=False):
        try:
            with open(self.db) as f: d = json.load(f)
        except Exception: return []
        return sorted(k.split("/")[-1] for k, v in d.get("files", {}).items()
                      if bool(v.get("trashed")) == copte)
    def temizle(self):
        srv = getattr(self, "_srv", None)
        if srv:
            srv.shutdown(); srv.server_close(); self._srv = None
        shutil.rmtree(self.dizin, ignore_errors=True)

def esit(a, b, mesaj=""):
    if a != b: raise AssertionError(f"{mesaj}\n    beklenen: {b!r}\n    gelen   : {a!r}")
def dogru(k, mesaj=""):
    if not k: raise AssertionError(mesaj or "kosul saglanmadi")

# ============================ RETENTION ============================
@test("gun siniri ve adet tabani birlikte dogru set secer", "retention")
def t_retention():
    o = Ortam()
    try:
        for g in range(0, 7):     # bugunden 6 gun oncesine
            o.vzdump_seti("qemu-100", g); o.vzdump_seti("lxc-201", g, "tar.zst")
        p = o.plan(keep_days=3, keep_count=2)
        G = o.modul()
        ok, n = G.do_copy(G.get_plan("p1"))
        dogru(ok, "kopyalama basarisiz")
        esit(len(o.drive()), 14, "yuklenen dosya sayisi")
        G.do_prune(G.get_plan("p1"))
        kalan = o.drive()
        esit(len(kalan), 6, "3 gun + 2 set tabani sonrasi kalan")
        from datetime import datetime, timedelta
        for g in range(0, 3):
            t = datetime.now() - timedelta(days=g, hours=12)
            dogru(any(f"{t:%Y_%m_%d}" in k for k in kalan), f"{g} gun oncesi kalmali")
        esit(len(o.drive(copte=True)), 8, "cope giden")
    finally: o.temizle()

@test("keep_days=0 olsa bile adet tabani korur", "retention")
def t_taban():
    o = Ortam()
    try:
        for g in range(0, 5): o.vzdump_seti("qemu-100", g)
        o.plan(keep_days=0, keep_count=2)
        G = o.modul()
        G.do_copy(G.get_plan("p1")); G.do_prune(G.get_plan("p1"))
        esit(len(o.drive()), 2, "misafir basina 2 set korunmali")
    finally: o.temizle()

# ============================ GUVENLIK ============================
@test("yukleme basarisizsa hicbir sey silinmez", "guvenlik")
def t_hatada_silme_yok():
    o = Ortam()
    try:
        for g in range(0, 7): o.vzdump_seti("qemu-100", g)
        o.plan(keep_days=3, keep_count=1)
        G = o.modul()
        G.do_run("p1", "test")
        once = o.drive()
        os.environ["MOCK_FAIL"] = "copy"
        try: G.do_run("p1", "test")
        finally: os.environ.pop("MOCK_FAIL", None)
        esit(o.drive(), once, "hatali kosuda Drive icerigi degismemeli")
    finally: o.temizle()

@test("retention izin kurali tum kombinasyonlarda dogru", "guvenlik")
def t_retention_kurali():
    """Projenin en onemli guvenlik kurali dogrudan sinaniyor:
    yukleme basarisiz VEYA Drive listelenemiyorsa hicbir sey silinmez."""
    o = Ortam()
    try:
        o.plan(); G = o.modul(); p = G.get_plan("p1")
        # (kopyalama_ok, listeleme_ok, prune_on_failure) -> retention calissin mi
        beklenen = {
            (True,  True,  False): True,    # normal durum
            (True,  True,  True):  True,
            (False, True,  False): False,   # yukleme hatasi -> ASLA silme
            (False, True,  True):  True,    # kullanici bilerek acmis
            (True,  False, False): False,   # Drive listelenemedi -> silme
            (True,  False, True):  False,   # zorlansa bile liste yoksa silme
            (False, False, False): False,
            (False, False, True):  False,
        }
        for (ok, listed, zorla), bekle in beklenen.items():
            p["prune_on_failure"] = zorla
            sonuc, _ = G._retention_calissin_mi(p, ok, listed, "p1")
            esit(sonuc, bekle, f"kopya={ok} liste={listed} zorla={zorla}")
    finally: o.temizle()

@test("cop listelenemezse takip kaydi dusurulmez", "guvenlik")
def t_cop_listeleme_hatasi():
    o = Ortam()
    try:
        for g in range(0, 4): o.vzdump_seti("qemu-100", g)
        o.plan(keep_days=1, keep_count=1, drive_trash_days=0)
        G = o.modul()
        G.do_copy(G.get_plan("p1")); G.do_prune(G.get_plan("p1"))
        izlenen = len(G.pstate(G.read_state(), "p1").get("drive_trash", []))
        dogru(izlenen > 0, "once cope tasinmis olmali")
        os.environ["MOCK_FAIL"] = "lstrash"
        try: n = G.do_purge_trash(G.get_plan("p1"))
        finally: os.environ.pop("MOCK_FAIL", None)
        esit(n, 0, "listeleme hatasinda silme raporlanmamali")
        esit(len(G.pstate(G.read_state(), "p1").get("drive_trash", [])), izlenen,
             "takip kayitlari korunmali")
    finally: o.temizle()

@test("cop suresi dolmadan kalici silinmez, dolunca silinir", "guvenlik")
def t_cop_suresi():
    o = Ortam()
    try:
        for g in range(0, 4): o.vzdump_seti("qemu-100", g)
        o.plan(keep_days=1, keep_count=1, drive_trash_days=2)
        G = o.modul()
        G.do_copy(G.get_plan("p1")); G.do_prune(G.get_plan("p1"))
        esit(G.do_purge_trash(G.get_plan("p1")), 0, "sure dolmadan silinmemeli")
        st = G.read_state()
        for e in st["plans"]["p1"]["drive_trash"]: e["trashed_at"] = int(time.time()) - 3 * 86400
        G.write_state(st)
        dogru(G.do_purge_trash(G.get_plan("p1")) > 0, "sure dolunca silinmeli")
        esit(o.drive(copte=True), [], "cop bosalmali")
    finally: o.temizle()

# ============================ CAKISMA ============================
@test("vzdump calisirken tur atlanir", "cakisma")
def t_vzdump_bekleme():
    o = Ortam()
    try:
        o.vzdump_seti("qemu-100", 0)
        o.plan(wait_for_vzdump=True, vzdump_wait_min=0)
        G = o.modul()
        os.environ["MOCK_VZDUMP"] = "1"
        try: G.do_run("p1", "test")
        finally: os.environ.pop("MOCK_VZDUMP", None)
        esit(G.pstate(G.read_state(), "p1")["status"], "atlandi", "durum atlandi olmali")
        esit(o.drive(), [], "hicbir sey yuklenmemeli")
    finally: o.temizle()

@test("bayat gecici dosya plani bloklamaz", "cakisma")
def t_bayat_dat():
    o = Ortam()
    try:
        o.vzdump_seti("qemu-100", 0)
        o.dosya("vzdump-qemu-100-2026_08_02-03_00_00.vma.zst.dat", yas_sn=7200)
        o.plan(wait_for_vzdump=True, vzdump_wait_min=0, min_age_min=10)
        G = o.modul()
        p = G.get_plan("p1")
        esit(G.active_writes(p), [], "2 saat once dokunulan .dat aktif sayilmamali")
        dogru(G.wait_for_vzdump(p), "plan bloklanmamali")
    finally: o.temizle()

@test("taze gecici dosya varken beklenir", "cakisma")
def t_taze_dat():
    o = Ortam()
    try:
        o.dosya("vzdump-qemu-100-2026_08_02-03_00_00.vma.zst.dat", yas_sn=5)
        o.plan(wait_for_vzdump=True, vzdump_wait_min=0, min_age_min=10)
        G = o.modul()
        p = G.get_plan("p1")
        esit(len(G.active_writes(p)), 1, "taze .dat aktif yazim sayilmali")
        dogru(not G.wait_for_vzdump(p), "tur atlanmali")
    finally: o.temizle()

# ============================ ZAMANLAMA ============================
@test("vakti gelmeyen calismaz, gelen bir kez calisir", "zamanlama")
def t_zamanlama():
    from datetime import datetime, timedelta
    o = Ortam()
    try:
        o.plan(); G = o.modul()
        p = G.get_plan("p1"); simdi = datetime(2026, 8, 8, 10, 0, 0)
        p["run_at"] = "12:00"
        dogru(not G.is_due(p, {}, simdi), "saat gelmeden calismamali")
        p["run_at"] = "09:00"
        dogru(G.is_due(p, {}, simdi), "saat gectiginde calismali")
        dogru(not G.is_due(p, {"last_run": "2026-08-08 09:30:00"}, simdi),
              "ayni slot icin ikinci kez calismamali")
        p["enabled"] = False
        dogru(not G.is_due(p, {}, simdi), "kapali plan calismamali")
        p["enabled"] = True; p["weekdays"] = [(simdi.isoweekday() % 7) + 1]
        dogru(not G.is_due(p, {}, simdi), "yanlis gunde calismamali")
    finally: o.temizle()

@test("haftalik rapor dogru gun ve saatte tetiklenir", "zamanlama")
def t_rapor_zamani():
    from datetime import datetime
    o = Ortam()
    try:
        o.plan(weekly_report=True, report_day=6, report_at="09:00")
        G = o.modul(); p = G.get_plan("p1")
        cmt = datetime(2026, 8, 8, 10, 0, 0)   # 2026-08-08 Cumartesi
        esit(cmt.isoweekday(), 6, "test tarihi cumartesi olmali")
        dogru(G.report_due(p, {}, cmt), "dogru gun ve saatte rapor gitmeli")
        dogru(not G.report_due(p, {"last_report": "2026-08-08 09:30:00"}, cmt),
              "ayni gun ikinci kez gitmemeli")
        dogru(not G.report_due(p, {}, datetime(2026, 8, 7, 10, 0, 0)), "yanlis gunde gitmemeli")
    finally: o.temizle()

# ============================ GOC ve AYAR ============================
@test("eski tek planli config plan listesine gocer", "config")
def t_goc():
    o = Ortam()
    try:
        o.yaz_cfg({"src_dir": "/var/lib/vz/dump", "remote": "gdrive:eski",
                   "keep_count": 7, "trash_grace_days": 2,
                   "log_file": os.path.join(o.dizin, "t.log"),
                   "state_file": os.path.join(o.dizin, "state.json")})
        G = o.modul(); planlar = G.cfg()["plans"]
        esit(len(planlar), 1, "tek plana donusmeli")
        esit(planlar[0]["remote"], "gdrive:eski")
        esit(planlar[0]["keep_count"], 7)
        esit(planlar[0]["drive_trash_days"], 2, "trash_grace_days tasinmali")
    finally: o.temizle()

@test("gecersiz degerler varsayilana duser", "config")
def t_normalize():
    o = Ortam()
    try:
        o.plan(run_at="99:99", keep_days="abc", drive_trash_days=-5, weekdays=[0, 3, 99])
        G = o.modul(); p = G.get_plan("p1")
        esit(p["run_at"], "03:00"); esit(p["keep_days"], 14)
        esit(p["drive_trash_days"], 0.0); esit(p["weekdays"], [3])
    finally: o.temizle()

# ============================ KAPASITE ============================
@test("kaynak analizi gunluk uretimi dogru olcer", "kapasite")
def t_analiz():
    o = Ortam()
    try:
        # 3 gunluk set, her gun ayni iki misafir
        for g in (1, 2, 3):
            o.dosya(f"vzdump-qemu-100-2026_08_{g:02d}-03_00_00.vma.zst", boyut=300_000)
            o.dosya(f"vzdump-lxc-201-2026_08_{g:02d}-03_20_00.tar.zst", boyut=100_000)
        o.dosya("alakasiz.txt", boyut=999)
        o.plan(); G = o.modul()
        a = G.kaynak_analiz(o.dump)
        dogru(a["ok"], "analiz basarili olmali")
        esit(a["set_sayisi"], 3, "3 farkli gun")
        esit(a["dosya"], 6, "alakasiz dosya sayilmamali")
        esit(a["toplam"], 1_200_000, "toplam boyut")
        esit(int(a["gunluk"]), 400_000, "gunluk ortalama = toplam / gun sayisi")
        esit(len(a["misafirler"]), 2, "iki misafir")
        esit(a["misafirler"][0]["ad"], "qemu-100", "en buyuk once")
        esit(a["misafirler"][0]["pay"], 75.0, "pay yuzdesi")
    finally: o.temizle()

@test("bos klasor ve gecersiz yol duzgun raporlanir", "kapasite")
def t_analiz_hata():
    o = Ortam()
    try:
        G = o.modul()
        dogru(not G.kaynak_analiz(o.dump)["ok"], "bos klasor ok=False dondurmeli")
        r = G.kaynak_analiz("/kesinlikle/olmayan/yol")
        dogru(not r["ok"] and "okunamadi" in r["hata"], "olmayan yol icin anlamli hata")
    finally: o.temizle()

@test("kota bilinmiyorsa 'sigmaz' denmez", "kapasite")
def t_kota_bilinmiyor():
    """Kota sorgusu basarisiz oldugunda 'sigmaz' raporlamak yanlis alarmdir;
    dogru cevap 'bilinmiyor'. (Gercek sunucuda 34 sn suren sorgu zaman asimina
    ugrayinca planlayici 215 GB'in 1.8 TB'ye sigmadigini soylemisti.)"""
    o = Ortam()
    try:
        G = o.modul()
        GB = 1024 ** 3
        analiz = {"ok": True, "gunluk": 50 * GB, "toplam": 200 * GB}
        # kota alinamadi
        for bos_kota in ({}, {"ok": False, "error": "zaman asimi"}, {"ok": None, "bekliyor": True}):
            pr = G.saklama_projeksiyon(analiz, bos_kota, 3, 1)
            esit(pr["sigar"], None, f"kota yokken sigar None olmali: {bos_kota}")
            esit(pr["kota_var"], False, "kota_var False olmali")
            dogru(pr["gereken"] > 0, "gereken alan yine de hesaplanmali")
        # kota varken normal calisir
        kota = {"ok": True, "total": 2000 * GB, "used": 200 * GB, "free": 1800 * GB}
        pr = G.saklama_projeksiyon(analiz, kota, 3, 1)
        esit(pr["sigar"], True, "200 GB, 1800 GB bosa sigar")
        esit(pr["kota_var"], True)
    finally: o.temizle()

@test("saklama projeksiyonu ve onerisi tutarli", "kapasite")
def t_projeksiyon():
    o = Ortam()
    try:
        G = o.modul()
        GB = 1024 ** 3
        analiz = {"ok": True, "gunluk": 50 * GB, "toplam": 200 * GB}
        kota = {"ok": True, "total": 2000 * GB, "used": 200 * GB, "free": 1800 * GB}
        pr = G.saklama_projeksiyon(analiz, kota, 10, 1)
        esit(pr["gereken"], 11 * 50 * GB, "10 gun + 1 gun cop = 11 gunluk yer")
        dogru(pr["sigar"], "550 GB, 1800 GB bosa sigar")
        dogru(not G.saklama_projeksiyon(analiz, kota, 60, 1)["sigar"], "60 gun sigmamali")
        # oneri: bos alanin %60'i = 1080 GB -> 1080/50 - 1 = 20.6 -> 20 gun
        esit(G.saklama_oneri(analiz, kota, 1, 60), 20, "oneri hesabi")
        dogru(G.saklama_oneri(analiz, kota, 1, 30) < G.saklama_oneri(analiz, kota, 1, 60),
              "daha dusuk pay daha kisa sure onerir")
    finally: o.temizle()

# ============================ AYRISTIRICILAR ============================
@test("rclone istatistik satiri dogru ayristirilir", "ayristirma")
def t_stats():
    o = Ortam()
    try:
        G = o.modul()
        r = G.parse_stats("2026/08/08 INFO  :   45.1 MiB / 114.4 MiB, 39%, 2.9 MiB/s, ETA 23s")
        dogru(r is not None, "gercek tek satirlik format eslesmeli (Transferred: oneki YOK)")
        esit(r["pct"], 39); esit(r["eta"], "23s")
        dogru(G.parse_stats("Transferred:  1.2 GiB / 10 GiB, 11%, 25 MiB/s, ETA 6m") is not None,
              "onekli format da eslesmeli")
        esit(G.parse_stats("INFO : Starting bandwidth limiter at 1Mi Byte/s"), None,
             "istatistik olmayan satir eslesmemeli")
    finally: o.temizle()

@test("bant genisligi degerleri ve cizelgesi dogrulanir", "ayristirma")
def t_bwlimit():
    o = Ortam()
    try:
        G = o.modul()
        for gecerli in ["30M", "off", "2.5M", "08:00,2M 19:00,off"]:
            dogru(G.bwlimit_gecerli(gecerli)[0], f"{gecerli} gecerli olmali")
        for hatali in ["abc", "08:00 2M", "25:00,1M"]:
            dogru(not G.bwlimit_gecerli(hatali)[0], f"{hatali} reddedilmeli")
        esit(G.bw_bytes("1M"), 1048576)
        esit(G.bwlimit_arg({"bwlimit": "30M", "bwlimit_schedule": "", "bwlimit_upload_only": True}),
             "30M:off", "yalniz-yukleme bicimi")
    finally: o.temizle()

@test("otomatik bant genisligi hedefi dogru hesaplanir", "ayristirma")
def t_bw_hedef():
    o = Ortam()
    try:
        G = o.modul()
        link, taban, tavan, pay = G.bw_bytes("100M"), G.bw_bytes("1M"), G.bw_bytes("30M"), 0.7
        def hedef(diger): return int(max(taban, min(tavan, link * pay - G.bw_bytes(diger))))
        esit(hedef("0"), tavan, "hat bosken tavan")
        esit(hedef("100M"), taban, "hat doluyken taban")
        dogru(taban < hedef("50M") < tavan, "ara degerde orantili")
    finally: o.temizle()

# ============================ BELLEK ============================
@test("rclone ciktisi bellekte sinirli tutulur", "bellek")
def t_akis():
    o = Ortam()
    try:
        G = o.modul()
        loud = os.path.join(o.dizin, "loud")
        with open(loud, "w") as f:
            f.write("#!/usr/bin/env python3\nfor i in range(50000): print('satir', i)\n")
        os.chmod(loud, 0o755)
        import subprocess as sp
        asil = sp.Popen
        sp.Popen = lambda args, **kw: asil([loud], **kw)
        try: rc, tail = G.rclone_stream(["copy", "a", "b"])
        finally: sp.Popen = asil
        esit(len(tail), 40, "yalnizca son 40 satir tutulmali")
        dogru("49999" in tail[-1], "son satir korunmali")
    finally: o.temizle()

@test("log dosyasi esikte dondurulur", "bellek")
def t_log_dondurme():
    o = Ortam()
    try:
        c = o.oku_cfg(); c["log_max_mb"] = 0.02; c["log_keep"] = 2; o.yaz_cfg(c)
        G = o.modul()
        for i in range(2000): G.log("dolgu " + str(i) + " " * 30)
        import glob
        dosyalar = glob.glob(c["log_file"] + "*")
        dogru(len(dosyalar) <= 3, f"en fazla log_keep+1 dosya olmali, {len(dosyalar)} var")
        for d in dosyalar:
            dogru(os.path.getsize(d) < 0.05 * 1024 * 1024, f"{d} esigi asmis")
    finally: o.temizle()

@test("durum dosyasi satir siniri uygulanir", "bellek")
def t_state_siniri():
    o = Ortam()
    try:
        c = o.oku_cfg(); c["snapshot_max_rows"] = 5; o.yaz_cfg(c)
        for g in range(0, 10): o.vzdump_seti("qemu-100", g)
        o.plan(keep_days=999, keep_count=99)
        G = o.modul()
        G.do_copy(G.get_plan("p1"))
        snap = G.update_snapshot(G.get_plan("p1"))
        esit(len(snap["backups"]), 5, "satirlar kirpilmali")
        esit(snap["totals"]["count"], 10, "toplam tam kalmali")
    finally: o.temizle()

# ============================ AG KISITLAMASI ============================
@test("izinli ag listesi dogru filtreler", "erisim")
def t_ag():
    o = Ortam()
    try:
        c = o.oku_cfg(); c["allow_networks"] = ["10.212.134.0/24", "127.0.0.1/32"]; o.yaz_cfg(c)
        G = o.modul()
        for ip in ["10.212.134.200", "10.212.134.1", "127.0.0.1"]:
            dogru(G.ip_izinli(ip), f"{ip} izinli olmali")
        for ip in ["192.168.1.5", "10.212.135.1", "8.8.8.8"]:
            dogru(not G.ip_izinli(ip), f"{ip} reddedilmeli")
        c["allow_networks"] = []; o.yaz_cfg(c); G.cfg(force=True)
        dogru(G.ip_izinli("8.8.8.8"), "bos liste = kisitlama yok")
    finally: o.temizle()

@test("sunucunun yerel agi her zaman izinlidir", "erisim")
def t_lan_acik():
    """Kurulumu VPN'den yapip sonra yerel agdan girmek isteyen kullanici
    kendini disarida birakmasin diye yerel ag kurala dahildir."""
    o = Ortam()
    try:
        c = o.oku_cfg(); c["allow_networks"] = ["10.212.134.0/24"]; c["lan_hep_acik"] = True
        o.yaz_cfg(c)
        G = o.modul()
        import ipaddress
        G._LAN_ONBELLEK.update(zaman=time.time(),
                               aglar=[ipaddress.ip_network("192.168.2.0/24")])
        dogru(G.ip_izinli("10.212.134.5"), "VPN agi izinli")
        dogru(G.ip_izinli("192.168.2.77"), "yerel ag da izinli olmali")
        dogru(not G.ip_izinli("8.8.8.8"), "yabanci adres reddedilmeli")
        # loopback gercek host_lan_aglari() ciktisinda hep bulunur
        G._LAN_ONBELLEK.update(zaman=0, aglar=[])
        dogru(G.ip_izinli("127.0.0.1"), "loopback her zaman izinli olmali")
        c["lan_hep_acik"] = False; o.yaz_cfg(c); G.cfg(force=True)
        G._LAN_ONBELLEK.update(zaman=time.time(),
                               aglar=[ipaddress.ip_network("192.168.2.0/24")])
        dogru(not G.ip_izinli("192.168.2.77"), "kapatilinca yerel ag da reddedilmeli")
    finally: o.temizle()

@test("ag kurtarma komutu listeyi dogru yonetir", "erisim")
def t_ag_yonet():
    """Yanlis kisitlama yuzunden arayuze girilemez duruma dusuldugunde kurtarma yolu."""
    o = Ortam()
    try:
        c = o.oku_cfg(); c["allow_networks"] = ["10.0.0.0/24"]; o.yaz_cfg(c)
        G = o.modul()
        esit(G.aglari_yonet()["aglar"], ["10.0.0.0/24"], "mevcut liste")
        esit(G.aglari_yonet("ekle", "192.168.1.0/24")["aglar"],
             ["10.0.0.0/24", "192.168.1.0/24"], "ekleme")
        dogru(not G.aglari_yonet("ekle", "sacma")["ok"], "gecersiz ag reddedilmeli")
        esit(G.aglari_yonet("cikar", "10.0.0.0/24")["aglar"], ["192.168.1.0/24"], "cikarma")
        esit(G.aglari_yonet("ac")["aglar"], [], "kisitlamayi kaldirma")
        dogru(G.ip_izinli("8.8.8.8"), "kisitlama kalkinca herkes gecmeli")
    finally: o.temizle()

@test("sifre hash'lenir ve dogrulanir", "erisim")
def t_sifre():
    o = Ortam()
    try:
        G = o.modul()
        h = G.hash_pw("gizli")
        dogru(h.startswith("pbkdf2_sha256$"), "pbkdf2 bicimi")
        dogru(G.verify_pw("gizli", h), "dogru sifre gecmeli")
        dogru(not G.verify_pw("yanlis", h), "yanlis sifre gecmemeli")
        dogru(G.verify_pw("duz", "duz"), "eski duz metin sifre de calismali")
    finally: o.temizle()

@test("kaba kuvvet kilidi devreye girer ve suresi dolunca acilir", "erisim")
def t_kilit():
    o = Ortam()
    try:
        c = o.oku_cfg(); c["login_max_attempts"] = 3; c["login_lockout_min"] = 0.02; o.yaz_cfg(c)
        G = o.modul(); ip = "1.2.3.4"
        for i in range(2):
            G.note_fail(ip); esit(G.locked_out(ip), 0, f"{i+1}. denemede kilit olmamali")
        G.note_fail(ip)
        dogru(G.locked_out(ip) > 0, "3. denemeden sonra kilitlenmeli")
        time.sleep(1.5)
        esit(G.locked_out(ip), 0, "sure dolunca acilmali")
    finally: o.temizle()

@test("beni hatirla oturum omrunu uzatir", "erisim")
def t_hatirla():
    o = Ortam()
    try:
        c = o.oku_cfg(); c["remember_enabled"] = True; c["remember_days"] = 30
        c["session_absolute_h"] = 24; o.yaz_cfg(c)
        G = o.modul()
        t1 = G.new_session("admin", "1.2.3.4", kalici=False)
        t2 = G.new_session("admin", "1.2.3.4", kalici=True)
        kisa = G.SESSIONS[t1]["bitis"] - time.time()
        uzun = G.SESSIONS[t2]["bitis"] - time.time()
        dogru(23 * 3600 < kisa < 25 * 3600, f"normal oturum ~24 saat olmali, {kisa/3600:.1f}")
        dogru(29 * 86400 < uzun < 31 * 86400, f"hatirlanan oturum ~30 gun olmali, {uzun/86400:.1f}")
        # hatirlanan oturum hareketsizlik yuzunden dusmemeli
        G.SESSIONS[t2]["last"] = time.time() - 10 * 86400
        G.SESSIONS[t1]["last"] = time.time() - 10 * 86400
        G.gc_sessions()
        dogru(t2 in G.SESSIONS, "hatirlanan oturum hareketsizlikten dusmemeli")
        dogru(t1 not in G.SESSIONS, "normal oturum hareketsizlikten dusmeli")
    finally: o.temizle()

@test("captcha uretilir, tek kullanimliktir", "erisim")
def t_captcha():
    o = Ortam()
    try:
        G = o.modul()
        cid = G.new_captcha()
        svg = G.captcha_svg(cid)
        dogru(svg.startswith("<svg"), "SVG uretilmeli")
        kod = "".join(re.findall(r">([A-Z0-9])</text>", svg))
        esit(len(kod), 5, "5 karakter")
        dogru(G.check_captcha(cid, kod.lower()), "buyuk/kucuk harf duyarsiz olmali")
        dogru(not G.check_captcha(cid, kod), "ayni captcha ikinci kez kullanilamamali")
    finally: o.temizle()

# ============================ RAPOR ve MAIL ============================
@test("haftalik rapor beklenen bolumleri icerir", "rapor")
def t_rapor():
    o = Ortam()
    try:
        for g in range(0, 3): o.vzdump_seti("qemu-100", g); o.vzdump_seti("lxc-201", g, "tar.zst")
        o.plan(keep_days=99, keep_count=9, weekly_report=True)
        G = o.modul()
        G.do_copy(G.get_plan("p1"))
        G.put_pstate("p1", G.update_snapshot(G.get_plan("p1")))
        govde, uyari = G.build_report(G.get_plan("p1"))
        for bolum in ["HAFTALIK YEDEK RAPORU", "CALISMA", "DRIVE", "VM/CT BAZINDA", "UYARILAR"]:
            dogru(bolum in govde, f"'{bolum}' bolumu olmali")
        dogru("qemu-100" in govde and "lxc-201" in govde, "misafirler listelenmeli")
    finally: o.temizle()

@test("form acilisi tek basina 'degisti' saymaz", "arayuz")
def t_temiz_acilis():
    """Plan acilip hicbir sey degistirmeden kapatilinca uyari cikmamaliydi;
    bwAutoToggle() kosulsuz markDirty() cagirdigi ve openEditor onu formu
    kurarken kullandigi icin her acilis kirli sayiliyordu."""
    o = Ortam()
    try:
        G = o.modul()
        h = G.HTML
        # Gorunum uygulama ile damga birbirinden ayrilmis olmali
        dogru("function bwAutoUygula()" in h, "gorunum icin ayri fonksiyon olmali")
        dogru("bwAutoUygula();" in h, "openEditor damgasiz surumu cagirmali")
        # openEditor kurulum sirasinda damga birakmamali.
        # Olay isleyicisi ATAMALARI (oninput/onchange) mesru: onlar ancak
        # kullanici alana dokununca calisir. Onlari ayikla, kalanina bak.
        i = h.index("function openEditor(")
        govde = h[i:h.index("function validatePlan(", i)]
        kurulum = [ln for ln in govde.split("\n")
                   if "oninput" not in ln and "onchange" not in ln]
        kalan = [ln.strip() for ln in kurulum if "markDirty" in ln or "bwAutoToggle(" in ln]
        dogru(not kalan, f"openEditor kurulumunda damga birakan cagri: {kalan}")
        dogru("dirty = false" in govde, "acilista damga sifirlanmali")
        # Isleyiciler yine de bagli olmali, yoksa gercek degisiklik fark edilmez
        dogru("oninput = markDirty" in govde, "alanlara isleyici baglanmali")
    finally: o.temizle()

@test("cikis dugmesi arayuzde var", "arayuz")
def t_cikis_dugmesi():
    """logout() fonksiyonu vardi ama hicbir yerden cagrilmiyordu: kullanici
    oturumu arayuzden kapatamiyordu."""
    o = Ortam()
    try:
        G = o.modul()
        h = G.HTML
        dogru('onclick="logout()"' in h, "cikis dugmesi olmali")
        dogru('id="kullanici"' in h, "oturum sahibi gosterilmeli")
        dogru("function kullaniciCiz()" in h, "kullanici adini yazan fonksiyon olmali")
        dogru("kullaniciCiz();" in h, "render sirasinda cagrilmali")
        # Kaydedilmemis degisiklik varken cikarken sormali
        i = h.index("async function logout(")
        dogru("dirty" in h[i:i + 400], "kirli formda cikis onay istemeli")
        # Canli akis paketi oturum alanlarini icermez; arayuz mevcudunu korumali
        dogru("user" not in G.public_status(),
              "public_status oturuma bagli alan icermemeli")
        # tsc tek satirlik if'i iki satira aciyor; bicime degil icerige bak
        for alan in ("user", "csrf"):
            dogru(f"y.{alan} = S.{alan}" in h,
                  f"akis guncellemesinde {alan} korunmali")
    finally: o.temizle()

@test("login sayfasi surum ve sunucu bilgisi gosterir", "arayuz")
def t_login_damga():
    """Sifreyi yazmadan once hangi surume ve hangi sunucuya girdigini,
    baglantinin sifreli olup olmadigini gormek gerekir."""
    o = Ortam()
    try:
        G = o.modul()
        dogru("{{SURUM}}" in G.LOGIN_HTML, "sablonda surum yeri olmali")
        for yer in ("{{SUNUCU}}", "{{TLS}}", "{{TLSSINIF}}", "{{TLSIPUCU}}"):
            dogru(yer in G.LOGIN_HTML, f"{yer} sablonda olmali")

        class SahteIsleyici:
            headers = {}
            path = "/"
            def _send(_s, kod, ctype, govde, extra=None): _s.govde = govde
        h = SahteIsleyici()
        G.H._login_page(h)
        g = h.govde
        dogru(G.SURUM in g, f"surum sayfada gorunmeli: {G.SURUM}")
        dogru(os.uname().nodename in g, "sunucu adi gorunmeli")
        dogru("HTTP" in g, "baglanti durumu gorunmeli")
        for kalan in ("{{SURUM}}", "{{SUNUCU}}", "{{TLS}}", "{{TLSSINIF}}", "{{TLSIPUCU}}"):
            dogru(kalan not in g, f"{kalan} degistirilmeden kalmis")
        # TLS kapaliyken uyari, acikken guvenli rozet
        dogru("kapali" in g and "⚠" in g, "TLS kapaliyken uyari gostermeli")
        G.TLS_AKTIF = True
        h2 = SahteIsleyici(); G.H._login_page(h2)
        dogru("acik" in h2.govde and "🔒" in h2.govde, "TLS acikken guvenli rozet")
    finally: o.temizle()

@test("hesap eklenmediyse 'eklendi' denmez", "izleme")
def t_remote_dogrulama():
    """rclone rc=0 dondugu halde hesabin yazilmadigi bir durum yasandi:
    log 'eklendi' dedi, hesap yoktu ve bu ancak saatler sonra fark edildi.
    Cikis kodu kanit degil; dosyadan geri okunmali."""
    o = Ortam()
    try:
        G = o.modul()
        G.RCLONE_CONF = os.path.join(o.dizin, "rclone.conf")
        G.remote_quota = lambda n: {"ok": False, "error": "test"}
        # rclone hep basarili donsun; asil soru dosyaya yazilip yazilmadigi
        G.rclone = lambda a, timeout=None: (0, "", "")
        yazildi = []
        # remote_create once "zaten var mi" diye bakar, sonra dogrulama icin
        # tekrar okur: ilk cagri bos, sonrakiler yazilanlari donsun
        G.rclone_remotes = lambda force=False: list(yazildi)

        r = G.remote_create("HAYALET", '{"access_token":"x"}')
        dogru(not r["ok"], f"yazilmayan hesap basarili sayilmamali: {r}")
        dogru("gorunmuyor" in r["msg"], f"sebep net olmali: {r['msg']}")

        # Simdi rclone gercekten yazsin
        def yazan(a, timeout=None):
            if a[:2] == ["config", "create"]: yazildi.append({"name": a[2], "type": "drive"})
            return (0, "", "")
        G.rclone = yazan
        r = G.remote_create("GERCEK", '{"access_token":"x"}')
        dogru(r["ok"], f"yazilan hesap basarili olmali: {r}")
        # Ayni adi tekrar eklemeye calisirsa reddedilmeli
        dogru(not G.remote_create("GERCEK", '{"access_token":"x"}')["ok"],
              "ayni ad iki kez eklenememeli")
    finally: o.temizle()

@test("rclone.conf degisiklikten once yedeklenir", "izleme")
def t_rclone_conf_yedek():
    o = Ortam()
    try:
        G = o.modul()
        import os as _o
        G.RCLONE_CONF = _o.path.join(o.dizin, "rclone.conf")   # /var/lib altina yazma
        open(G.RCLONE_CONF, "w").write("[gdrive]\ntype = drive\n")
        y = G.rclone_conf_yedekle("deneme")
        dogru(y and _o.path.exists(y), f"yedek olusmali: {y}")
        dogru("[gdrive]" in open(y).read(), "yedek icerigi dogru olmali")
        esit(oct(_o.stat(y).st_mode)[-3:], "600", "yedek 600 olmali")
        d = _o.path.dirname(y)
        esit(oct(_o.stat(d).st_mode)[-3:], "700", "yedek dizini 700 olmali")
        # dosya yoksa sessizce gecmeli, patlamasin
        _o.remove(G.RCLONE_CONF)
        esit(G.rclone_conf_yedekle("yok"), "", "config yoksa yedek de yok")
    finally: o.temizle()

@test("zamanlayici gecikmesi fark edilir", "izleme")
def t_tick_sagligi():
    """Timer durursa hicbir yedek alinmaz ve tek belirtisi 'sonraki calisma'nin
    gecmiste kalmasidir. Damga eskirse arayuz ve rapor uyarmali."""
    o = Ortam()
    try:
        G = o.modul()
        d, m2 = G.tick_sagligi()
        esit(d, "bilinmiyor", "hic tick yokken bilinmiyor olmali")
        dogru(m2, "sebep yazilmali")
        G.tick_damgasi_yaz()
        esit(G.tick_sagligi()[0], "iyi", "taze damga iyi olmali")
        dogru(G.tick_yasi_dk() < 1, "yas kucuk olmali")
        # damgayi geriye al
        G.put_state_root({"last_tick_epoch": int(time.time()) - 3600})
        d, m2 = G.tick_sagligi()
        esit(d, "gecikmis", "bir saatlik damga gecikmis sayilmali")
        dogru("60" in m2, f"kac dakika gectigi yazmali: {m2}")
        c = G.cfg(); c["tick_uyari_dk"] = 120; G.save_cfg(c)
        esit(G.tick_sagligi()[0], "iyi", "esik yukselince iyi olmali")
    finally: o.temizle()

@test("tick her calismada damga birakir", "izleme")
def t_tick_damga():
    o = Ortam()
    try:
        o.plan(run_at="23:59")          # bugun calismasin, sadece damga birakilsin
        G = o.modul()
        dogru(G.tick_yasi_dk() is None, "baslangicta damga yok")
        G.do_tick()
        dogru(G.tick_yasi_dk() is not None, "tick damga birakmali")
        dogru(G.read_state().get("last_tick"), "okunabilir zaman damgasi da olmali")
        esit(G.public_status()["saglik"]["tick"], "iyi", "durum API'si sagligi bildirmeli")
    finally: o.temizle()

@test("birim hatasi maili gunlukle birlikte gider", "izleme")
def t_birim_bildir():
    o = Ortam()
    try:
        o.plan(mail_to="yonetici@ornek.com")
        G = o.modul()
        yakalanan = {}
        class SahteSMTP:
            def __init__(self, *a, **k): pass
            def starttls(self): pass
            def login(self, *a): pass
            def send_message(self, m): yakalanan["m"] = m
            def quit(self): pass
        G.smtplib.SMTP = SahteSMTP
        c = G.cfg()
        c["smtp_profiles"] = [{"id": "s1", "name": "t", "host": "h", "port": 25,
                               "security": "none", "user": "", "pass": "", "from": "a@b.c"}]
        c["plans"][0]["smtp_profile"] = "s1"
        G.save_cfg(c)
        esit(G.birim_bildir("pve-gdrive-ui.service"), 0, "bildirim basarili olmali")
        m2 = yakalanan["m"]
        esit(m2["To"], "yonetici@ornek.com", "alici plandan alinmali")
        dogru("pve-gdrive-ui.service" in m2["Subject"], f"konu birimi icermeli: {m2['Subject']}")
        govde = m2.get_body(("plain",)).get_content()
        for parca in ("SYSTEMD DURUMU", "SON GUNLUK", "UYARILAR"):
            dogru(parca in govde, f"'{parca}' bolumu olmali")
        # kapatilinca susmali
        c = G.cfg(); c["failure_mail"] = False; G.save_cfg(c)
        yakalanan.clear()
        esit(G.birim_bildir("pve-gdrive-ui.service"), 1, "kapaliyken basarisiz donmeli")
        dogru("m" not in yakalanan, "kapaliyken mail gitmemeli")
    finally: o.temizle()

@test("systemd birimleri hata bildirimi tanimlar", "izleme")
def t_systemd_onfailure():
    kok = os.path.join(KOK, "systemd")
    sablon = os.path.join(kok, "pve-gdrive-bildir@.service")
    dogru(os.path.exists(sablon), "bildirim sablon birimi olmali")
    ic = open(sablon).read()
    dogru("pve_gdrive.py bildir %i" in ic, "sablon coken birimin adini gecmeli")
    for birim in ("pve-gdrive-ui.service", "pve-gdrive-tick.service"):
        s2 = open(os.path.join(kok, birim)).read()
        dogru("OnFailure=pve-gdrive-bildir@%n.service" in s2, f"{birim} OnFailure tanimlamali")
    tick = open(os.path.join(kok, "pve-gdrive-tick.service")).read()
    dogru("TimeoutStartSec" in tick, "tick sonsuza kadar surmemeli")

@test("koprunun altindaki uplink secilir", "bantgenisligi")
def t_wan_iface():
    """Proxmox'ta varsayilan rota vmbr0 gibi bir kopruden gecer. Koprunun
    sayaclari VM<->VM yerel trafigi de sayar; o trafik internete cikmaz ve
    yukleme hizimizla yarismaz. Olcum koprunun uplink uyesinden yapilmali."""
    o = Ortam()
    try:
        G = o.modul()
        sahte = os.path.join(o.dizin, "sys")
        def ag(ad, kopru=False, uyeler=(), bond=False, fiziksel=False):
            d = os.path.join(sahte, ad); os.makedirs(d, exist_ok=True)
            if kopru:
                os.makedirs(os.path.join(d, "bridge"), exist_ok=True)
                for u in uyeler: os.makedirs(os.path.join(d, "brif", u), exist_ok=True)
            if bond: os.makedirs(os.path.join(d, "bonding"), exist_ok=True)
            if fiziksel: os.makedirs(os.path.join(d, "device"), exist_ok=True)
        ag("vmbr0", kopru=True, uyeler=["bond0", "tap105i0", "veth100i0", "fwpr102p0"])
        ag("bond0", bond=True); ag("eno1", fiziksel=True)
        G.kopru_mu = lambda i: os.path.isdir(os.path.join(sahte, i, "bridge"))
        gercek_kopru_uplink = G.kopru_uplink
        def sahte_uplink(i):
            try: uyeler = sorted(os.listdir(os.path.join(sahte, i, "brif")))
            except Exception: return ""
            aday = [u for u in uyeler if not u.startswith(G.SANAL_ONEK)]
            for u in aday:
                if os.path.isdir(os.path.join(sahte, u, "bonding")): return u
            for u in aday:
                if os.path.exists(os.path.join(sahte, u, "device")): return u
            return aday[0] if aday else ""
        G.kopru_uplink = sahte_uplink
        G.default_iface = lambda: "vmbr0"
        i, neden = G.wan_iface()
        esit(i, "bond0", "bond uplink secilmeliydi")
        dogru("uplink" in neden, f"sebep aciklanmali: {neden}")
        esit(G.wan_iface("eno3")[0], "eno3", "elle secim her zaman kazanir")
        # Kopru degilse dokunulmaz
        G.default_iface = lambda: "eno1"
        esit(G.wan_iface()[0], "eno1", "kopru olmayan arayuz oldugu gibi kalir")
        # Sanal uyeler asla secilmez
        G.kopru_uplink = gercek_kopru_uplink
        for sanal in ("tap105i0", "veth100i0", "fwpr102p0", "fwbr1i0", "docker0"):
            dogru(sanal.startswith(G.SANAL_ONEK), f"{sanal} sanal sayilmali")
        dogru(not "bond0".startswith(G.SANAL_ONEK), "bond0 sanal sayilmamali")
    finally: o.temizle()

@test("hat kapasitesi olculerek ogrenilir ve kalici yazilir", "bantgenisligi")
def t_bw_ogrenme():
    """Arayuzun bag hizi internet yukleme hizini gostermez. Olculen deger
    saklanir; gerileme icin kolayca dusmemeli."""
    o = Ortam()
    try:
        o.plan()
        G = o.modul()
        esit(G.bw_ogrenilen_oku("p1"), 0.0, "baslangicta olcum yok")
        G.bw_ogrenilen_yaz("p1", 8_086_657)              # 7.7 MB/sn
        esit(int(G.bw_ogrenilen_oku("p1")), 8_086_657, "olcum kalici olmali")
        s = G.pstate(G.read_state(), "p1")
        dogru(s.get("bw_olculen_zaman"), "olcum zamani da yazilmali")
        # varsayilan kip ogrenme
        esit(G.get_plan("p1")["bw_auto_link_mode"], "ogren", "varsayilan kip ogrenme")
        o.plan(bw_auto_link_mode="sacma")
        esit(G.get_plan("p1")["bw_auto_link_mode"], "ogren", "gecersiz kip varsayilana doner")
        o.plan(bw_auto_link_mode="manuel")
        esit(G.get_plan("p1")["bw_auto_link_mode"], "manuel", "manuel kip korunur")
    finally: o.temizle()

@test("host yapilandirma arsivi ozel anahtarlari almaz", "yapilandirma")
def t_hostconf_gizli():
    """Kume CA anahtari ve authkey.key sifresiz Drive'a cikmamali.
    authorized_keys ise ACIK anahtar dosyasi: dahil olmali, yoksa geri
    yuklemede SSH erisimi kaybolur."""
    o = Ortam()
    try:
        import tarfile
        sahte = os.path.join(o.dizin, "etc", "pve")
        os.makedirs(os.path.join(sahte, "priv", "acme"))
        os.makedirs(os.path.join(sahte, "lxc"))
        yaz = lambda yol, ic: open(yol, "w").write(ic)
        yaz(os.path.join(sahte, "storage.cfg"), "dir: local\n")
        yaz(os.path.join(sahte, "lxc", "100.conf"), "arch: amd64\n")
        yaz(os.path.join(sahte, "priv", "pve-root-ca.key"), "GIZLI-CA-ANAHTARI")
        yaz(os.path.join(sahte, "priv", "authkey.key"), "GIZLI-TICKET-ANAHTARI")
        yaz(os.path.join(sahte, "priv", "authorized_keys"), "ssh-ed25519 AAAA acik")
        yaz(os.path.join(sahte, "priv", "shadow.cfg"), "kullanici:$5$hash")
        yaz(os.path.join(sahte, "priv", "acme", "account.json"), "{}")
        p = o.plan(host_config_paths=[sahte], host_config_json=False)
        G = o.modul()
        hedef = os.path.join(o.dizin, "cikti"); os.makedirs(hedef)
        uretilen, n, atlanan = G.hostconf_uret(G.get_plan("p1"), hedef)
        dogru(len(uretilen) == 1 and uretilen[0].endswith(".tar.gz"), f"arsiv: {uretilen}")
        with tarfile.open(uretilen[0]) as t:
            adlar = t.getnames()
            govde = t.extractfile("OKUBENI.txt").read().decode()
        var = lambda parca: any(parca in a for a in adlar)
        dogru(var("storage.cfg"), "storage.cfg alinmali")
        dogru(var("lxc/100.conf"), "CT tanimi alinmali")
        dogru(var("authorized_keys"), "acik anahtar listesi alinmali")
        for gizli in ("pve-root-ca.key", "authkey.key", "shadow.cfg", "account.json"):
            dogru(not var(gizli), f"{gizli} arsive GIRMEMELI")
        # Icerik duzeyinde de dogrula: ad eslesmesi yetmez
        with tarfile.open(uretilen[0]) as t:
            hepsi = b"".join(t.extractfile(a).read() for a in adlar
                             if t.getmember(a).isfile())
        dogru(b"GIZLI-CA-ANAHTARI" not in hepsi, "CA anahtarinin icerigi sizmamali")
        dogru(b"GIZLI-TICKET-ANAHTARI" not in hepsi, "ticket anahtari sizmamali")
        dogru("pve-root-ca.key" in govde, "atlananlar OKUBENI'de listelenmeli")
        dogru(oct(os.stat(uretilen[0]).st_mode)[-3:] == "600", "arsiv 600 olmali")
        dogru(len(atlanan) >= 4, f"atlanan sayisi: {atlanan}")
    finally: o.temizle()

@test("yapilandirma arsivleri kendi saklama kuralina uyar", "yapilandirma")
def t_hostconf_saklama():
    o = Ortam()
    try:
        o.plan(keep_days=3, host_config_keep_count=2)
        G = o.modul()
        from datetime import datetime, timedelta
        adlar = []
        for gun in (0, 1, 2, 5, 9, 20):        # 5,9,20 gun onceki uc dosya eski
            t = datetime.now() - timedelta(days=gun, hours=1)
            adlar.append(f"pve-config-pve-{t:%Y_%m_%d-%H_%M_%S}.tar.gz")
        for a in adlar: o.drive_ekle(a)
        files = [{"Name": a, "Size": 1000, "IsDir": False} for a in adlar]
        files.append({"Name": "vzdump-qemu-100-2020_01_01-00_00_00.vma.zst",
                      "Size": 5, "IsDir": False})
        o.drive_ekle("vzdump-qemu-100-2020_01_01-00_00_00.vma.zst", 5)
        n = G.hostconf_prune(G.get_plan("p1"), files)
        dogru(n == 3, f"3 eski arsiv silinmeliydi, {n} silindi")
        kalan = [x for x in o.drive() if x.startswith("pve-config-")]
        dogru(len(kalan) == 3, f"3 arsiv kalmaliydi, kalan: {kalan}")
        dogru("vzdump-qemu-100-2020_01_01-00_00_00.vma.zst" in o.drive(),
              "vzdump dosyasina dokunulmamali")
        dogru(G.RE_HOSTCONF.match(adlar[0]), "kalip kendi adiyla eslesmeli")
        dogru(not G.RE_HOSTCONF.match("vzdump-qemu-100-2020_01_01-00_00_00.vma.zst"),
              "vzdump dosyasi yapilandirma sanilmamali")
    finally: o.temizle()

@test("yapilandirma secenekleri plana kaydedilip geri okunur", "yapilandirma")
def t_hostconf_form():
    """Arayuzdeki uc alan (e-hc / e-hcj / e-hck) plana yaziliyor mu?
    Alan tabloya eklenip sunucuda normalize edilmezse kutu isaretlenir ama
    kaydedilmez - sessizce calismayan bir ayar en kotusu."""
    o = Ortam()
    try:
        G = o.modul()
        r = G.save_plan({"name": "Deneme", "remote": "gdrive:x",
                         "host_config_enabled": False, "host_config_json": False,
                         "host_config_keep_count": 7})
        dogru(r["ok"], r.get("msg"))
        p = G.get_plan(r["id"])
        esit(p["host_config_enabled"], False, "kapali secim korunmali")
        esit(p["host_config_json"], False, "json secimi korunmali")
        esit(p["host_config_keep_count"], 7, "adet korunmali")
        G.save_plan({"id": r["id"], "name": "Deneme", "remote": "gdrive:x",
                     "host_config_enabled": True, "host_config_keep_count": 45})
        p = G.get_plan(r["id"])
        esit(p["host_config_enabled"], True, "acik secim korunmali")
        esit(p["host_config_keep_count"], 45, "yeni adet korunmali")
        # sacma deger varsayilana donsun, plan bozulmasin
        G.save_plan({"id": r["id"], "name": "Deneme", "remote": "gdrive:x",
                     "host_config_keep_count": "abc"})
        esit(G.get_plan(r["id"])["host_config_keep_count"],
             G.GLOBAL_DEFAULTS["host_config_keep_count"], "gecersiz deger varsayilana doner")
        # Arayuz alanlari gomulu pakette gercekten var mi
        for parca in ('id="e-hc"', 'id="e-hcj"', 'id="e-hck"', "host_config_enabled"):
            dogru(parca in G.HTML, f"'{parca}' arayuzde bulunmali")
    finally: o.temizle()

@test("yapilandirma yedegi kapaliyken hicbir sey uretmez", "yapilandirma")
def t_hostconf_kapali():
    o = Ortam()
    try:
        o.plan(host_config_enabled=False)
        G = o.modul()
        y, n, hata = G.hostconf_yukle(G.get_plan("p1"))
        dogru((y, n, hata) == (0, 0, ""), f"kapaliyken sessiz kalmali: {(y, n, hata)}")
    finally: o.temizle()

@test("olay yayini yavas istemciden etkilenmez", "canli")
def t_olay_yayini():
    o = Ortam()
    try:
        G = o.modul()
        y = G.OlayYayini(kuyruk_max=3, abone_max=2)
        a = y.abone_ol(); b = y.abone_ol()
        dogru(a is not None and b is not None, "iki abone kabul edilmeli")
        dogru(y.abone_ol() is None, "sinir asilinca yeni abone reddedilmeli")
        for i in range(10): y.yayinla("t", {"i": i})
        alinan = []
        while True:
            pk = y.bekle(a, 0.01)
            if not pk: break
            alinan.append(pk[1]["i"])
        dogru(len(alinan) == 3, f"kuyruk sinirli olmali, {len(alinan)} geldi")
        dogru(alinan == [7, 8, 9], f"en yeni olaylar kalmali, {alinan}")
        dogru(y.bekle(b, 0.01) is not None, "diger abone etkilenmemeli")
        y.ayril(a); y.ayril(b)
        dogru(y.abone_sayisi() == 0, "ayrilan abone temizlenmeli")
        dogru(y.bekle(a, 0.01) is None, "ayrilan abone icin bekle None donmeli")
    finally: o.temizle()

@test("izleyici degisikligi olaya cevirir, degismeyeni yollamaz", "canli")
def t_izleyici():
    o = Ortam()
    try:
        o.plan()
        G = o.modul()
        no = G.OLAY.abone_ol()
        try:
            durum = G._izleyici_turu({})          # ilk tur: yalnizca taban alinir
            dogru(G.OLAY.bekle(no, 0.01) is None, "ilk turda olay yollanmamali")
            durum2 = G._izleyici_turu(durum)
            dogru(G.OLAY.bekle(no, 0.01) is None, "degisiklik yokken olay olmamali")
            G.log("izleyici testi satiri")        # log dosyasi buyudu
            G._izleyici_turu(durum2)
            pk = G.OLAY.bekle(no, 0.01)
            dogru(pk is not None and pk[0] == "log", f"log olayi beklenirdi: {pk}")
            dogru(any("izleyici testi" in x for x in pk[1]["satirlar"]),
                  "yeni satir olayda olmali")
            durum3 = G._izleyici_turu(G._izleyici_turu({}))
            G.put_pstate("p1", {"status": "basarili"})   # state.json degisti
            G._izleyici_turu(durum3)
            turler = []
            while True:
                pk = G.OLAY.bekle(no, 0.01)
                if not pk: break
                turler.append(pk[0])
            dogru("durum" in turler, f"durum olayi beklenirdi: {turler}")
        finally: G.OLAY.ayril(no)
    finally: o.temizle()

@test("canli akis ucu SSE bicimi dondurur", "canli")
def t_sse_ucu():
    o = Ortam()
    try:
        o.plan()
        s = o.sunucu()
        r = s.istek("/api/events", akis=True)
        dogru(r.getheader("Content-Type", "").startswith("text/event-stream"),
              f"content-type yanlis: {r.getheader('Content-Type')}")
        dogru(r.getheader("X-Accel-Buffering") == "no", "vekil arabellegi kapatilmali")
        ham = r.read(400).decode("utf-8", "replace")
        dogru("retry:" in ham, "yeniden baglanma araligi bildirilmeli")
        dogru("event: durum" in ham, "acilista tam durum yollanmali")
        dogru('"plans"' in ham, "durum paketi planlari icermeli")
        r.close()
    finally: o.temizle()

@test("degisiklik uctan uca akisa dusuyor", "canli")
def t_sse_uctan_uca():
    """Zincirin tamami: diskteki degisiklik -> izleyici -> yayin -> HTTP akisi."""
    import threading as _t
    o = Ortam()
    try:
        o.plan()
        c = o.oku_cfg(); c["sse_watch_ms"] = 100; c["sse_ping_sec"] = 1; o.yaz_cfg(c)
        s = o.sunucu()
        G = s.G                       # sunucu ile ayni modul ornegi
        G.cfg(force=True)
        dur = _t.Event()
        _t.Thread(target=G.izleyici_dongusu, args=(dur,), daemon=True).start()
        r = s.istek("/api/events", akis=True, zaman=10)
        try:
            r.readline()                                  # retry satiri
            basla = time.time()
            while "event: durum" not in r.readline().decode("utf-8", "replace"):
                dogru(time.time() - basla < 5, "acilis durumu gelmedi")
            G.log("uctan uca test satiri")                # log dosyasi degisti
            G.put_pstate("p1", {"status": "basarili", "summary": "uctan uca"})
            gorulen, basla = set(), time.time()
            while time.time() - basla < 6 and not {"log", "durum"} <= gorulen:
                ln = r.readline().decode("utf-8", "replace")
                if ln.startswith("event: "): gorulen.add(ln[7:].strip())
            dogru("log" in gorulen, f"log olayi akisa dusmeliydi: {gorulen}")
            dogru("durum" in gorulen, f"durum olayi akisa dusmeliydi: {gorulen}")
        finally:
            r.close(); dur.set()
        # Abone kopunca kayit temizlenmeli, is parcacigi sizmasin
        basla = time.time()
        while G.OLAY.abone_sayisi() and time.time() - basla < 6: time.sleep(0.05)
        dogru(G.OLAY.abone_sayisi() == 0,
              "kopan abone temizlenmeli (ping kopmayi fark etmeli)")
    finally: o.temizle()

@test("beni hatirla servis yeniden baslayinca yasar", "guvenlik")
def t_hatirla_kalici():
    o = Ortam()
    try:
        G = o.modul()
        gecici = G.new_session("root", "10.0.0.5", kalici=False)
        kalici = G.new_session("root", "10.0.0.5", kalici=True)
        dogru(G.get_session(kalici, "10.0.0.5") is not None, "oturum acilmali")
        G.DEPO.sifirla()                       # servis yeniden basladi
        dogru(G.get_session(kalici, "10.0.0.5") is None, "bellek gercekten bosalmali")
        dogru(G.DEPO.kalicilari_yukle() == 1, "yalnizca kalici oturum geri gelmeli")
        dogru(G.get_session(kalici, "10.0.0.5") is not None, "hatirlanan oturum yasamali")
        dogru(G.get_session(gecici, "10.0.0.5") is None, "gecici oturum yasamamali")
        import os
        dogru(oct(os.stat(G.oturum_dosyasi()).st_mode)[-3:] == "600", "token dosyasi 600 olmali")
    finally: o.temizle()

@test("oturum adres baglama kipleri", "guvenlik")
def t_ip_baglama():
    o = Ortam()
    try:
        G = o.modul()
        for kip, ayni_ag, baska_ag in (("ip", False, False), ("ag", True, False), ("yok", True, True)):
            C = G.cfg(); C["session_ip_bind"] = kip; G.save_cfg(C)
            t = G.new_session("root", "192.168.2.10", kalici=True)
            dogru(G.get_session(t, "192.168.2.10") is not None, f"{kip}: ayni adres gecmeli")
            dogru((G.get_session(t, "192.168.2.77") is not None) == ayni_ag,
                  f"{kip}: ayni ag beklenen sonucu vermeli")
            dogru((G.get_session(t, "10.9.9.9") is not None) == baska_ag,
                  f"{kip}: baska ag beklenen sonucu vermeli")
        C = G.cfg(); C["session_ip_bind"] = "yok"; G.save_cfg(C)
        t = G.new_session("root", "192.168.2.10", kalici=False)
        dogru(G.get_session(t, "10.9.9.9") is None,
              "kalici olmayan oturum kip ne olursa olsun adrese bagli kalmali")
    finally: o.temizle()

@test("Rapor butonu sunucuda karsilik bulur", "rapor")
def t_rapor_eylemi():
    o = Ortam()
    try:
        o.plan(weekly_report=True, mail_to="")
        G = o.modul()
        c = G.run_action("report", "p1")
        dogru(c["msg"] != "bilinmeyen islem", "'report' tanimli olmali")
        dogru(not c["ok"] and "alici" in c["msg"], "alici yoksa net sebep donmeli")
        dogru(not G.run_action("report", "yok-boyle-plan")["ok"], "olmayan plan reddedilmeli")
        o.plan(weekly_report=False, mail_to="a@b.c")
        dogru("kapali" in G.run_action("report", "p1")["msg"], "rapor kapaliysa soylenmeli")
    finally: o.temizle()

@test("html mail Outlook uyumlu ve kacisli uretilir", "rapor")
def t_html_mail():
    o = Ortam()
    try:
        G = o.modul()
        govde = ("[OK] Test <plan> - basarili\n" + "=" * 40 + "\n\nOZET\n"
                 "  Zaman        : 2026-08-09 03:47:12\n"
                 "  Hedef        : gdrive:proxmox-yedek\n"
                 "  2026-08-09 03:12  vm-100     48.1 GiB  vzdump-qemu-100.vma.zst\n\n"
                 "UYARILAR\n  ! Kota %91.0 - esik %90 asildi.\n")
        h = G.mail_html(govde, "basarili", "konu")
        dogru(h.startswith("<!DOCTYPE html>"), "doctype olmali")
        dogru("<table" in h and "role=\"presentation\"" in h, "tablo tabanli olmali")
        dogru("flex" not in h and "grid-template" not in h, "Outlook flex/grid anlamaz")
        dogru("[if mso]" in h, "mso kosullu blogu olmali")
        dogru("&lt;plan&gt;" in h, "baslik HTML-kacisli olmali")
        dogru(h.count("<style") <= 1, "stil satir ici olmali")
        dogru("OZET" in h and "UYARILAR" in h, "bolumler tasinmali")
        dogru("Kota %91.0" in h, "uyari metni tasinmali")
        dogru("gdrive:proxmox-yedek" in h, "iki nokta iceren deger bozulmamali")
        for d, renk in (("HATA", "#b3261e"), ("atlandi", "#8a5a00")):
            dogru(renk in G.mail_html(govde, d, "k"), f"{d} rengi kullanilmali")
    finally: o.temizle()

@test("html mail duz metin alternatifi ile birlikte gider", "rapor")
def t_mail_multipart():
    o = Ortam()
    try:
        G = o.modul()
        yakalanan = {}
        class SahteSMTP:
            def __init__(self, *a, **k): pass
            def starttls(self): pass
            def login(self, *a): pass
            def send_message(self, msg): yakalanan["m"] = msg
            def quit(self): pass
        G.smtplib.SMTP = SahteSMTP
        C = G.cfg()
        C["smtp_profiles"] = [{"id": "s1", "name": "t", "host": "h", "port": 25,
                               "security": "none", "user": "", "pass": "",
                               "from": "a@b.c"}]
        G.save_cfg(C)
        dogru(G.send_mail("x@y.z", "konu", "BASLIK\n\nOZET\n  A : 1\n", "s1"), "mail gitmeli")
        m = yakalanan["m"]
        dogru(m.is_multipart(), "multipart olmali")
        tipler = [x.get_content_type() for x in m.walk()]
        dogru("text/plain" in tipler, "duz metin parcasi olmali")
        dogru("text/html" in tipler, "html parcasi olmali")
    finally: o.temizle()

@test("ileri tarihli yedek negatif yas gostermez", "rapor")
def t_negatif_yas():
    o = Ortam()
    try:
        o.plan(keep_days=99, keep_count=9)
        G = o.modul()
        ileri = time.time() + 3600            # bir saat sonrasi
        gs = [{"guest": "qemu-100", "last": int(ileri), "sets": 1, "size": 100}]
        satirlar, eski = G._bolum_misafirler(G.get_plan("p1"), gs)
        dogru(not any(re.search(r"-[\d.]+ gun once", l) for l in satirlar),
              "negatif yas basilmamali: " + str(satirlar))
        dogru("0.0 gun once" in satirlar[0], "sifira kirpilmali: " + satirlar[0])
    finally: o.temizle()

@test("calisma maili detaylari icerir", "rapor")
def t_calisma_maili():
    o = Ortam()
    try:
        o.vzdump_seti("qemu-100", 0)
        o.plan()
        G = o.modul()
        G.do_copy(G.get_plan("p1"))
        snap = G.update_snapshot(G.get_plan("p1"))
        govde, n = G.build_run_mail(G.get_plan("p1"), "basarili", "ozet", snap,
                                    {"trigger": "test", "dur": 5, "uploaded": 1})
        for bolum in ["OZET", "YAPILANDIRMA", "DRIVE DURUMU", "UYARILAR"]:
            dogru(bolum in govde, f"'{bolum}' bolumu olmali")
        govde2, n2 = G.build_run_mail(G.get_plan("p1"), "HATA", "ozet", snap,
                                      {"trigger": "test", "dur": 5, "skipped": True})
        dogru("RETENTION CALISTIRILMADI" in govde2, "atlanan retention belirtilmeli")
        dogru(n2 > 0, "hata durumunda uyari uretilmeli")
    finally: o.temizle()

@test("bildirim secimleri dogru uygulanir", "rapor")
def t_bildirim():
    o = Ortam()
    try:
        o.plan(notify_success=True, notify_failure=False, notify_skipped=True)
        G = o.modul(); p = G.get_plan("p1")
        dogru(G.notify_wanted(p, "basarili"), "basarili acik")
        dogru(not G.notify_wanted(p, "HATA"), "hata kapali")
        dogru(G.notify_wanted(p, "atlandi"), "atlandi acik")
    finally: o.temizle()

# ============================ DIL ============================
@test("mail ve login Ingilizceye cevrilir", "dil")
def t_mail_ceviri():
    o = Ortam()
    try:
        c = o.oku_cfg(); c["dil"] = "en"; o.yaz_cfg(c)
        o.vzdump_seti("qemu-100", 0)
        o.plan(); G = o.modul()
        G.do_copy(G.get_plan("p1"))
        snap = G.update_snapshot(G.get_plan("p1"))
        govde, _ = G.build_run_mail(G.get_plan("p1"), "basarili", "x", snap,
                                    {"trigger": "zamanlanmis", "dur": 5, "uploaded": 1})
        cev = G.metni_cevir(govde)
        for beklenen in ("SUMMARY", "CONFIGURATION", "DRIVE STATUS", "WARNINGS",
                         "scheduled", "days", "files"):
            dogru(beklenen in cev, f"'{beklenen}' cevrilmis metinde olmali")
        dogru("OZET" not in cev and "zamanlanmis" not in cev, "Turkce kalinti olmamali")
        for x in ("Sign in", "Username", "Password", "Remember me"):
            dogru(x in G.metni_cevir(G.LOGIN_HTML), f"login: {x}")
    finally: o.temizle()

@test("Turkce kipte metin degismeden kalir", "dil")
def t_turkce_kalir():
    o = Ortam()
    try:
        c = o.oku_cfg(); c["dil"] = "tr"; o.yaz_cfg(c)
        o.plan(); G = o.modul()
        metin = "OZET\n  Zaman : 1\n  Yuklenen : 2 dosya"
        esit(G.metni_cevir(metin), metin, "Turkce kipte hicbir sey degismemeli")
    finally: o.temizle()

@test("hizalama ceviri sonrasi bozulmaz", "dil")
def t_hizalama():
    o = Ortam()
    try:
        c = o.oku_cfg(); c["dil"] = "en"; o.yaz_cfg(c)
        o.plan(); G = o.modul()
        cikti = G.metni_cevir("  Zaman        : a\n  Kalici silinen: b")
        iki_nokta = [l.index(":") for l in cikti.split("\n") if ":" in l]
        esit(len(set(iki_nokta)), 1, f"iki noktalar ayni sutunda olmali: {cikti!r}")
    finally: o.temizle()

# ============================ LOG AYRIMI ============================
@test("sistem ve plan loglari ayrilir", "log")
def t_log_ayrimi():
    o = Ortam()
    try:
        o.plan(); G = o.modul()
        G.log("sistem mesaji")
        G.log("plan mesaji", "p1")
        sistem = G.read_log("system"); plan = G.read_log("p1"); hepsi = G.read_log("all")
        dogru(any("sistem mesaji" in l for l in sistem), "sistem logunda olmali")
        dogru(not any("[p1]" in l for l in sistem), "sistem logunda plan satiri olmamali")
        dogru(any("plan mesaji" in l for l in plan), "plan logunda olmali")
        dogru(len(hepsi) >= len(sistem) + len(plan) - 1, "tumu ikisini de icermeli")
    finally: o.temizle()

# ============================ KESIF ============================
@test("klasor gezgini koklerin disina cikmaz", "kesif")
def t_gezgin():
    o = Ortam()
    try:
        G = o.modul()
        b = G.browse(o.dizin)
        esit(b["path"], o.dizin)
        dogru(any(d["name"] == "dump" for d in b["dirs"]), "alt klasor listelenmeli")
        esit(G.browse("/etc")["path"], o.dizin, "kok disina cikamamali")
        esit(G.browse("../../..")["path"], o.dizin, "gorece yol ile de cikamamali")
    finally: o.temizle()

@test("vzdump dosyalari taninir ve setlere ayrilir", "kesif")
def t_set_ayirma():
    o = Ortam()
    try:
        o.plan(); G = o.modul()
        dosyalar = [
            {"Name": "vzdump-qemu-100-2026_08_01-03_00_00.vma.zst", "Size": 10, "IsDir": False},
            {"Name": "vzdump-qemu-100-2026_08_01-03_00_00.log", "Size": 1, "IsDir": False},
            {"Name": "vzdump-lxc-201-2026_08_01-03_20_00.tar.zst", "Size": 5, "IsDir": False},
            {"Name": "alakasiz.txt", "Size": 1, "IsDir": False},
        ]
        setler = G.collect_sets(dosyalar)
        esit(len(setler), 2, "iki set olmali (alakasiz dosya sayilmamali)")
        qemu = [v for v in setler.values() if v["guest"] == "qemu-100"][0]
        esit(len(qemu["files"]), 2, "log dosyasi ayni sete ait olmali")
    finally: o.temizle()

# ============================ TYPESCRIPT ============================
@test("TypeScript strict modda hatasiz derlenir", "arayuz")
def t_tsc():
    tsc = os.path.join(KOK, "ui", "node_modules", ".bin", "tsc")
    if not os.path.exists(tsc):
        raise AssertionError("ATLA: npm install yapilmamis (cd ui && npm install)")
    r = subprocess.run([tsc, "--noEmit", "-p", os.path.join(KOK, "ui")],
                       capture_output=True, text=True)
    dogru(r.returncode == 0, "tsc hatalari:\n" + (r.stdout or r.stderr)[:600])

@test("gomulu arayuz guncel ve butun", "arayuz")
def t_bundle():
    o = Ortam()
    try:
        G = o.modul()
        dogru(len(G.HTML) > 50000, "arayuz gomulu olmali")
        for parca in ["wAdim", "w-ozet", "hesap-ekle-panel", "progBox", "bwAutoToggle"]:
            dogru(parca in G.HTML, f"'{parca}' gomulu arayuzde bulunmali (npm run build gerekebilir)")
        dogru("captcha" in G.LOGIN_HTML, "login sayfasi captcha icermeli")
    finally: o.temizle()

@test("ceviri cagrisi HTML'e sizmamis", "arayuz")
def t_ceviri_kacagi():
    """Toplu kelime degistirme bir donem C(...) cagrilarini metnin icine yazdi;
    arayuzde ekrana 'C(3 gun...' diye basiliyordu. Bir daha sessizce donmesin."""
    o = Ortam()
    try:
        G = o.modul()
        for desen, aciklama in (
            (r'>C\(', "HTML metninin icinde C( kalmis"),
            (r'title=C\(', "tirnaksiz title=C( ozniteligi"),
            (r'\bvC\(', "surum etiketine C( yapismis"),
            (r"^\s*\+ '\)<", "basibos kapanis parantezi ile baslayan HTML parcasi"),
        ):
            bulgu = re.findall(desen, G.HTML, re.M)
            dogru(not bulgu, f"{aciklama}: {bulgu[:3]}")
        # Ekrana basilan metinde ham fonksiyon cagrisi gorunmemeli
        for kotu in ["C(' + p.keep_days", '">C(', "vC('"]:
            dogru(kotu not in G.HTML, f"'{kotu}' arayuzde kalmamali")
    finally: o.temizle()

@test("sag tik menusu sistemi gomulu", "arayuz")
def t_sag_tik():
    o = Ortam()
    try:
        G = o.modul()
        for parca in ["menuKur", "sagTik", "ctx-oge", "planMenusu", "hesapMenusu",
                      "smtpMenusu", "logMenusu", "panoyaYaz", "dosyaIndir"]:
            dogru(parca in G.HTML, f"'{parca}' gomulu arayuzde bulunmali")
        for sec in ["data-plan", "data-hesap", "data-smtp"]:
            dogru(sec in G.HTML, f"'{sec}' menu hedefi isaretlenmeli")
        dogru("contextmenu" in G.HTML, "contextmenu dinleyicisi olmali")
        dogru("touchstart" in G.HTML, "dokunmatik uzun basma desteklenmeli")
    finally: o.temizle()

@test("F5 taslagi ve hata odaklama gomulu", "arayuz")
def t_f5_taslak():
    o = Ortam()
    try:
        G = o.modul()
        for parca in ["beforeunload", "taslakPlanla", "taslakSor", "taslakSil",
                      "hataOdakla", "ilkHataAlani"]:
            dogru(parca in G.HTML, f"'{parca}' gomulu arayuzde bulunmali")
        dogru('"password"' in G.HTML, "taslak sifre alanlarini disarida birakmali")
    finally: o.temizle()

@test("saklama ipuclari sabit gun sayisi icermez", "arayuz")
def t_saklama_ipucu():
    """Alanda 3 yazarken ipucu '14 gun' diyordu; metin artik degerden turetiliyor."""
    o = Ortam()
    try:
        G = o.modul()
        dogru("14 gün + günlük yedek" not in G.HTML, "sabit 14 gun ornegi kalmamali")
        for parca in ["eg-kd", "eg-kc", "eg-td", "saklamaIpucu"]:
            dogru(parca in G.HTML, f"'{parca}' bulunmali")
    finally: o.temizle()

# ============================ KOSUCU ============================
def main():
    gruplar = {}
    for grup, ad, fn in TESTLER:
        if SUZGEC and not any(s in grup or s in ad for s in SUZGEC): continue
        gruplar.setdefault(grup, []).append((ad, fn))
    if not gruplar:
        print("Eslesen test yok."); return 1
    gecti = kaldi = atlandi = 0
    basla = time.time()
    for grup, testler in gruplar.items():
        print(f"\n\033[1m{grup.upper()}\033[0m")
        for ad, fn in testler:
            ortam_yedek = dict(os.environ)
            try:
                fn()
                print(f"  \033[32m✓\033[0m {ad}"); gecti += 1
            except AssertionError as e:
                if str(e).startswith("ATLA:"):
                    print(f"  \033[33m•\033[0m {ad} — {str(e)[5:].strip()}"); atlandi += 1
                else:
                    print(f"  \033[31m✗\033[0m {ad}"); kaldi += 1
                    print("      " + str(e).replace("\n", "\n      "))
            except Exception as e:
                print(f"  \033[31m✗\033[0m {ad} — beklenmedik hata: {type(e).__name__}: {e}")
                kaldi += 1
                if AYRINTILI:
                    import traceback; traceback.print_exc()
            finally:
                os.environ.clear(); os.environ.update(ortam_yedek)
    sure = time.time() - basla
    print(f"\n\033[1mSONUC\033[0m  {gecti} gecti, {kaldi} kaldi"
          + (f", {atlandi} atlandi" if atlandi else "") + f"  ({sure:.1f} sn)")
    return 1 if kaldi else 0

if __name__ == "__main__":
    sys.exit(main())
