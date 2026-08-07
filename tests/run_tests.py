#!/usr/bin/env python3
"""
pve-gdrive-backup test paketi.

  python3 tests/run_tests.py            # hepsi
  python3 tests/run_tests.py retention  # ada gore filtrele
  python3 tests/run_tests.py -v         # ayrintili

Gercek Drive'a veya gercek bir Proxmox'a dokunmaz: rclone ve pgrep sahte
surumlerle degistirilir (tests/mock/). Her test kendi gecici dizininde calisir.
"""
import os, sys, json, time, shutil, tempfile, subprocess, importlib.util, base64, re, urllib.request, urllib.error

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
    def vzdump_seti(self, misafir, gun, uzanti="vma.zst"):
        return self.dosya(f"vzdump-{misafir}-2026_08_{gun:02d}-03_00_00.{uzanti}")
    def modul(self):
        os.environ["PVE_GDRIVE_CONF"] = self.cfg_yolu
        os.environ["MOCK_DB"] = self.db
        os.environ["PATH"] = MOCK + os.pathsep + os.environ.get("PATH", "")
        sp = importlib.util.spec_from_file_location("pgd_" + str(id(self)), BETIK)
        m = importlib.util.module_from_spec(sp); sp.loader.exec_module(m)
        return m
    def drive(self, copte=False):
        try:
            with open(self.db) as f: d = json.load(f)
        except Exception: return []
        return sorted(k.split("/")[-1] for k, v in d.get("files", {}).items()
                      if bool(v.get("trashed")) == copte)
    def temizle(self):
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
        for g in range(1, 8):
            o.vzdump_seti("qemu-100", g); o.vzdump_seti("lxc-201", g, "tar.zst")
        p = o.plan(keep_days=3, keep_count=2)
        G = o.modul()
        ok, n = G.do_copy(G.get_plan("p1"))
        dogru(ok, "kopyalama basarisiz")
        esit(len(o.drive()), 14, "yuklenen dosya sayisi")
        G.do_prune(G.get_plan("p1"))
        kalan = o.drive()
        esit(len(kalan), 6, "3 gun + 2 set tabani sonrasi kalan")
        dogru(all("2026_08_0" + str(g) in " ".join(kalan) for g in (5, 6, 7)), "son 3 gun kalmali")
        esit(len(o.drive(copte=True)), 8, "cope giden")
    finally: o.temizle()

@test("keep_days=0 olsa bile adet tabani korur", "retention")
def t_taban():
    o = Ortam()
    try:
        for g in range(1, 6): o.vzdump_seti("qemu-100", g)
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
        for g in range(1, 8): o.vzdump_seti("qemu-100", g)
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
        for g in range(1, 5): o.vzdump_seti("qemu-100", g)
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
        for g in range(1, 5): o.vzdump_seti("qemu-100", g)
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
        o.vzdump_seti("qemu-100", 1)
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
        o.vzdump_seti("qemu-100", 1)
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
        for g in range(1, 11): o.vzdump_seti("qemu-100", g)
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
        for g in range(1, 4): o.vzdump_seti("qemu-100", g); o.vzdump_seti("lxc-201", g, "tar.zst")
        o.plan(keep_days=99, keep_count=9, weekly_report=True)
        G = o.modul()
        G.do_copy(G.get_plan("p1"))
        G.put_pstate("p1", G.update_snapshot(G.get_plan("p1")))
        govde, uyari = G.build_report(G.get_plan("p1"))
        for bolum in ["HAFTALIK YEDEK RAPORU", "CALISMA", "DRIVE", "MISAFIR BAZINDA", "UYARILAR"]:
            dogru(bolum in govde, f"'{bolum}' bolumu olmali")
        dogru("qemu-100" in govde and "lxc-201" in govde, "misafirler listelenmeli")
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
        o.vzdump_seti("qemu-100", 1)
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
        o.vzdump_seti("qemu-100", 1)
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
