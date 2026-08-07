#!/usr/bin/env python3
"""
pve_gdrive.py - Proxmox vzdump yedeklerini Google Drive'a yukler + web UI.
Bagimlilik YOK (Python 3 stdlib + rclone). Proxmox HOST uzerinde calisir.

Cok planli: her plan kendi kaynak klasoru, remote'u, saklama gunleri,
calisma saati ve mail alicisi ile bagimsiz calisir.

Komutlar:
  python3 pve_gdrive.py init              # /etc/pve-gdrive.conf ornegi yazar
  python3 pve_gdrive.py tick              # vakti gelen planlari calistirir (timer bunu cagirir)
  python3 pve_gdrive.py run [--plan ID]   # plani hemen calistirir (ID yoksa: tum etkin planlar)
  python3 pve_gdrive.py serve             # web arayuzunu baslatir
  python3 pve_gdrive.py snapshot [--plan ID]  # Drive durumunu tazeler
  python3 pve_gdrive.py status            # durum JSON (stdout)
  python3 pve_gdrive.py plans             # planlari listeler
"""
import os, sys, json, time, base64, subprocess, smtplib, re, fcntl, hmac, threading, fnmatch
import hashlib, secrets, random, ssl, ipaddress, shutil, urllib.request, html as _html
from collections import deque
from datetime import datetime, timedelta
from email.message import EmailMessage
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, parse_qs, unquote

SURUM = "1.1.0"
CONFIG_PATH = os.environ.get("PVE_GDRIVE_CONF", "/etc/pve-gdrive.conf")
LOCK_DIR    = "/tmp"

# Plan disi, kurulum geneli ayarlar
GLOBAL_DEFAULTS = {
    "log_file": "/var/log/pve-gdrive.log",
    "state_file": "/var/lib/pve-gdrive/state.json",
    "ui_bind": "0.0.0.0",
    "ui_port": 8787,
    "ui_user": "admin",
    "ui_pass": "degistir-beni",   # ilk acilista pbkdf2 ile hash'lenip uzerine yazilir
    "api_token": "",              # otomasyon icin: Authorization: Bearer <token>
    # --- oturum ve giris guvenligi ---
    "remember_enabled": True,     # giris ekraninda "beni hatirla" secenegi
    "remember_days": 30,          # hatirlanan oturumun omru (gun)
    "session_timeout_min": 120,   # hareketsizlik suresi
    "session_absolute_h": 24,     # oturumun azami omru
    "login_max_attempts": 5,      # bu kadar hatali denemeden sonra kilit
    "login_lockout_min": 15,      # kilit suresi
    "captcha_enabled": True,
    "captcha_after_fails": 0,     # 0 = her giriste captcha iste
    # --- HTTPS (dogrudan, ters vekil olmadan) ---
    # Proxmox'un kendi sertifikasi kullanilir: tarayici uyarisi PVE arayuzuyle ayni olur.
    # Bos birakilirsa arayuz duz HTTP calisir.
    "ssl_cert": "/etc/pve/local/pve-ssl.pem",
    "ssl_key": "/etc/pve/local/pve-ssl.key",
    "cookie_secure": False,       # TLS acikken otomatik olarak zorlanir
    "trust_proxy_header": False,  # X-Forwarded-For'a guvenilsin mi (nginx arkasinda true)
    # --- otomatik guncelleme ---
    "update_check": True,         # gunde bir yeni surum var mi diye bak
    "update_auto": False,         # bulununca kendiliginden kur (varsayilan: sadece bildir)
    "update_url": "https://raw.githubusercontent.com/hzkucuk/pve-gdrive-backup/main/pve_gdrive.py",
    "update_backup_keep": 5,      # saklanacak eski surum sayisi
    "quota_cache_min": 15,        # hesap kotasi kac dakika onbellekte tutulsun
    # Zamanlayici: systemd timer yerine surecin kendi icinde calissin mi.
    # null = otomatik (konteynerde acik, systemd kurulumunda kapali)
    "debug": False,               # ayrintili hata izleri loga yazilsin mi
    "internal_scheduler": None,
    "scheduler_interval_sec": 300,
    # Arayuze yalnizca bu aglardan erisilebilir. Bos liste = kisitlama yok.
    # Firewall kurmaya gerek kalmaz; yanlis yazarsan config'ten geri alinir,
    # SSH ve Proxmox arayuzu bu ayardan hic etkilenmez.
    "allow_networks": [],
    "smtp_profiles": [],        # birden fazla gonderici hesap; her plan birini secer
    # --- asagidakiler eskiye uyumluluk icin; ilk acilista profile donusturulur ---
    "smtp_host": "smtp.gmail.com",
    "smtp_port": 587,
    "smtp_user": "",
    "smtp_pass": "",
    "mail_from": "",
    "browse_roots": ["/var/lib/vz", "/mnt/pve", "/mnt", "/srv"],
    "allow_account_cleanup": False,
    "history_max": 50,          # plan basina saklanacak calisma gecmisi kaydi
    "log_tail_lines": 250,      # UI'da gosterilecek log satiri
    "ui_refresh_sec": 5,        # UI kendini kac saniyede bir tazeler
    "rclone_timeout_min": 0,    # 0 = sinirsiz; plan basina rclone zaman asimi (dakika)
    "rclone_tail_lines": 40,    # rclone ciktisindan bellekte tutulacak son satir sayisi
    "snapshot_max_rows": 200,   # state.json'a yazilacak azami yedek/cop satiri (toplamlar tam kalir)
    "stats_interval_sec": 5,    # rclone ilerleme bildirim sikligi (UI bunu gosterir)
    "purge_batch": 50,          # cop temizliginde tek rclone cagrisinda silinecek dosya sayisi
    "purge_timeout_min": 30,    # cop temizligi rclone cagrisi icin zaman asimi (dakika)
    "log_max_mb": 5,            # log dosyasi bu boyutu asinca dondurulur
    "log_keep": 2,              # saklanacak eski log dosyasi sayisi
    "dump_regex": r"^(vzdump-(qemu|lxc)-(\d+)-(\d{4}_\d{2}_\d{2}-\d{2}_\d{2}_\d{2}))",
    "plans": []
}

# Her planin kendi ayarlari - GUN cinsinden olan her sey burada, parametrik
PLAN_DEFAULTS = {
    "id": "",
    "name": "Yeni plan",
    "enabled": True,
    "src_dir": "/var/lib/vz/dump",
    "remote": "gdrive:proxmox-yedek",
    "keep_days": 14,          # Drive'da normal duracagi gun sayisi
    "keep_count": 3,          # misafir basina her kosulda korunacak en yeni set (gun sinirindan bagimsiz)
    "drive_trash_days": 1,    # Google cop kutusunda bekleyecegi gun sayisi
    "run_at": "03:00",        # gunun saati
    "weekdays": [],           # bos = her gun, yoksa 1=Pzt .. 7=Paz
    "bwlimit": "30M",         # sabit sinir; "off" = sinirsiz
    "bwlimit_schedule": "",   # saat cizelgesi, or. "08:00,2M 19:00,30M 23:00,off"
    "bwlimit_upload_only": True,  # sinir yalnizca yuklemeye uygulansin
    # --- otomatik bant genisligi: hattaki diger trafige gore kendini ayarlar ---
    "bwlimit_auto": False,
    "bw_auto_link": "100M",       # toplam YUKLEME kapasiten (ISS hizin)
    "bw_auto_reserve_pct": 30,    # bu yuzde kadari her zaman digerlerine birakilir
    "bw_auto_min": "1M",          # hat mesgulken inilecek taban
    "bw_auto_max": "",            # bos = bwlimit degeri tavan olarak kullanilir
    "bw_auto_iface": "",          # bos = varsayilan rota arayuzu
    "bw_auto_interval_sec": 10,   # olcum ve ayar sikligi
    "bw_auto_smooth": 0.4,        # yumusatma katsayisi (0-1); dusuk = daha sakin
    "bw_auto_step_pct": 25,       # bu yuzdeden kucuk degisiklikler uygulanmaz
    "transfers": 2,
    "checkers": 4,
    "drive_chunk": "64M",     # rclone RAM kullanimi ~ drive_chunk x transfers
    "rclone_extra": [],       # ham rclone argumanlari, or. ["--exclude","*.log"]
    # --- kaynak kullanimi: hipervizoru ve uzerindeki VM/CT'leri yormamak icin ---
    "nice": 10,               # CPU onceligi (0-19, yuksek = daha nazik)
    "ionice_class": 2,        # 1=gercek zamanli 2=en iyi caba 3=bosta
    "ionice_level": 6,        # 0-7 (yuksek = daha nazik), yalnizca sinif 2 icin
    "buffer_size": "16M",     # rclone dosya basina tampon. RAM ~ (parca+tampon) x transfer
    "use_mmap": True,         # bellegi isletim sistemine geri verir, GC baskisini azaltir
    "fast_list": False,       # tek istekte tum agac: daha az API cagrisi, daha cok RAM
    "no_traverse": False,     # az sayida yeni dosya varken hedefi bastan sona listeleme
    "tpslimit": 0,            # saniyedeki azami API islemi (0 = sinirsiz)
    # --- Proxmox'un kendi yedegi ile cakismayi onleyen ayarlar ---
    "wait_for_vzdump": True,  # vzdump calisiyorsa yuklemeye baslama
    "vzdump_wait_min": 60,    # en fazla bu kadar dakika bekle, sonra bu turu atla (0 = hic bekleme)
    "min_age_min": 10,        # dosya en az bu kadar dakikadir degismemisse yukle (yarim dosya gitmesin)
    "skip_patterns": ["*.dat", "*.tmp", "*.part"],   # yazilmakta olan dosyalar
    "prune_on_failure": False,  # kopyalama basarisizsa retention CALISMASIN (varsayilan guvenli)
    "smtp_profile": "",       # bos = ilk profil
    "mail_to": "",
    # Hangi sonucta mail gitsin - bagimsiz secilir
    "notify_success": True,   # basarili bitince
    "notify_failure": True,   # hata alinca
    "notify_skipped": False,  # vzdump cakismasi yuzunden atlaninca
    # --- haftalik ozet raporu ---
    "weekly_report": True,
    "report_day": 1,          # 1=Pzt .. 7=Paz
    "report_at": "09:00",
    "report_mail_to": "",     # bos = mail_to ile ayni
    "report_days": 7,         # rapor kac gunluk donemi kapsasin
    "report_stale_days": 2,   # bu kadar gundur basarili yedek yoksa uyar
    "report_quota_warn": 90   # kota yuzdesi bu esigi asarsa uyar
}

_RE_CACHE = {}
def dump_re():
    """Dosya adi kalibi config'ten gelir: grup1=set adi, 2=tip, 3=id, 4=tarih."""
    pat = cfg().get("dump_regex") or GLOBAL_DEFAULTS["dump_regex"]
    if pat not in _RE_CACHE:
        try: _RE_CACHE[pat] = re.compile(pat)
        except re.error as e:
            print(f"UYARI: dump_regex gecersiz ({e}), varsayilana donuldu", file=sys.stderr)
            _RE_CACHE[pat] = re.compile(GLOBAL_DEFAULTS["dump_regex"])
    return _RE_CACHE[pat]
DT_FMT  = "%Y_%m_%d-%H_%M_%S"
TS_FMT  = "%Y-%m-%d %H:%M:%S"

# ---------- CONFIG ----------
_CACHE = {"mtime": None, "data": None}

def _migrate(c):
    """Eski tek planli duz config'i plan listesine cevirir."""
    if c.get("plans"): return c
    legacy = {k: c[k] for k in PLAN_DEFAULTS if k in c and k not in ("id", "name", "enabled")}
    if not legacy: return c
    p = dict(PLAN_DEFAULTS)
    p.update(legacy)
    p["id"] = "varsayilan"
    p["name"] = "Varsayilan plan"
    if "trash_grace_days" in c:          # eski isim
        p["drive_trash_days"] = c["trash_grace_days"]
    c["plans"] = [p]
    return c

def load_cfg():
    c = dict(GLOBAL_DEFAULTS)
    c["plans"] = []
    if os.path.exists(CONFIG_PATH):
        try:
            with open(CONFIG_PATH) as f: c.update(json.load(f))
        except Exception as e:
            print(f"UYARI: config okunamadi ({e})", file=sys.stderr)
    c = _migrate(c)
    if not c.get("smtp_profiles") and c.get("smtp_user"):
        c["smtp_profiles"] = [norm_smtp({
            "id": "varsayilan", "name": "Varsayilan", "host": c.get("smtp_host"),
            "port": c.get("smtp_port"), "user": c.get("smtp_user"),
            "pass": c.get("smtp_pass"), "from": c.get("mail_from") or c.get("smtp_user"),
            "security": "starttls"})]
    c["smtp_profiles"] = [norm_smtp(x) for x in c.get("smtp_profiles", [])]
    c["plans"] = [norm_plan(p) for p in c.get("plans", [])]
    return c

def cfg(force=False):
    """Config onbellekli okunur. Onbellek tazelenince hata ayiklama bayragi da guncellenir."""
    try: mt = os.path.getmtime(CONFIG_PATH)
    except Exception: mt = None
    if force or _CACHE["data"] is None or _CACHE["mtime"] != mt:
        _CACHE["data"] = load_cfg(); _CACHE["mtime"] = mt
        _HATA_AYIKLA["acik"] = bool(_CACHE["data"].get("debug")
                                    or os.environ.get("PVE_GDRIVE_DEBUG"))
    return _CACHE["data"]

def save_cfg(c):
    tmp = CONFIG_PATH + ".tmp"
    with open(tmp, "w") as f: json.dump(c, f, indent=2, ensure_ascii=False)
    os.chmod(tmp, 0o600); os.replace(tmp, CONFIG_PATH)
    _CACHE["data"] = None
    return cfg(force=True)

def norm_plan(p):
    q = dict(PLAN_DEFAULTS); q.update(p or {})
    q["id"] = str(q.get("id") or "").strip() or slug(q.get("name") or "plan")
    for k in ("keep_days", "keep_count", "transfers", "checkers"):
        try: q[k] = max(0, int(q[k]))
        except Exception: q[k] = PLAN_DEFAULTS[k]
    if not isinstance(q.get("rclone_extra"), list): q["rclone_extra"] = []
    try: q["nice"] = min(19, max(-20, int(q["nice"])))
    except Exception: q["nice"] = PLAN_DEFAULTS["nice"]
    try: q["ionice_class"] = min(3, max(0, int(q["ionice_class"])))
    except Exception: q["ionice_class"] = PLAN_DEFAULTS["ionice_class"]
    try: q["ionice_level"] = min(7, max(0, int(q["ionice_level"])))
    except Exception: q["ionice_level"] = PLAN_DEFAULTS["ionice_level"]
    try: q["tpslimit"] = max(0, int(q["tpslimit"]))
    except Exception: q["tpslimit"] = PLAN_DEFAULTS["tpslimit"]
    q["buffer_size"] = str(q.get("buffer_size") or "16M").strip()
    for k in ("use_mmap", "fast_list", "no_traverse"):
        q[k] = bool(q.get(k, PLAN_DEFAULTS[k]))
    q["bwlimit"] = str(q.get("bwlimit") or "off").strip()
    q["bwlimit_schedule"] = str(q.get("bwlimit_schedule") or "").strip()
    q["bwlimit_upload_only"] = bool(q.get("bwlimit_upload_only", True))
    q["bwlimit_auto"] = bool(q.get("bwlimit_auto", False))
    for k in ("bw_auto_link", "bw_auto_min", "bw_auto_max", "bw_auto_iface"):
        q[k] = str(q.get(k) or "").strip()
    for k, alt, ust in (("bw_auto_reserve_pct", 0, 95), ("bw_auto_interval_sec", 2, 3600),
                        ("bw_auto_step_pct", 1, 90)):
        try: q[k] = min(ust, max(alt, int(q[k])))
        except Exception: q[k] = PLAN_DEFAULTS[k]
    try: q["bw_auto_smooth"] = min(1.0, max(0.05, float(q["bw_auto_smooth"])))
    except Exception: q["bw_auto_smooth"] = PLAN_DEFAULTS["bw_auto_smooth"]
    if not isinstance(q.get("skip_patterns"), list):
        q["skip_patterns"] = list(PLAN_DEFAULTS["skip_patterns"])
    if not RE_SAAT.match(str(q.get("report_at", ""))): q["report_at"] = "09:00"
    try: q["report_day"] = min(7, max(1, int(q["report_day"])))
    except Exception: q["report_day"] = 1
    q["weekly_report"] = bool(q.get("weekly_report", True))
    for k in ("report_days", "report_stale_days", "report_quota_warn"):
        try: q[k] = max(0, int(q[k]))
        except Exception: q[k] = PLAN_DEFAULTS[k]
    for k in ("vzdump_wait_min", "min_age_min"):
        try: q[k] = max(0.0, float(q[k]))
        except Exception: q[k] = PLAN_DEFAULTS[k]
    for k in ("wait_for_vzdump", "prune_on_failure"):
        q[k] = bool(q.get(k, PLAN_DEFAULTS[k]))
    try: q["drive_trash_days"] = max(0.0, float(q["drive_trash_days"]))
    except Exception: q["drive_trash_days"] = PLAN_DEFAULTS["drive_trash_days"]
    if not RE_SAAT.match(str(q.get("run_at", ""))): q["run_at"] = "03:00"
    wd = q.get("weekdays") or []
    q["weekdays"] = sorted({int(x) for x in wd if str(x).isdigit() and 1 <= int(x) <= 7})
    q["enabled"] = bool(q.get("enabled", True))
    # eski tek secimli ayardan gocur
    if "notify_on" in q and not any(k in (p or {}) for k in ("notify_success", "notify_failure")):
        old = q.pop("notify_on", "always")
        q["notify_success"] = (old == "always")
        q["notify_failure"] = (old in ("always", "failure"))
        q["notify_skipped"] = False
    q.pop("notify_on", None)
    for k in ("notify_success", "notify_failure", "notify_skipped"):
        q[k] = bool(q.get(k, PLAN_DEFAULTS[k]))
    return q

RE_SAAT = re.compile(r"^([01]?\d|2[0-3]):[0-5]\d$")

def slug(s):
    s = re.sub(r"[^a-zA-Z0-9]+", "-", str(s).lower()).strip("-")
    return s or "plan"

def get_plan(pid, c=None):
    for p in (c or cfg()).get("plans", []):
        if p["id"] == pid: return p
    return None

# ---------- LOG / STATE ----------
_HATA_AYIKLA = {"acik": bool(os.environ.get("PVE_GDRIVE_DEBUG"))}

def yut(nerede, e):
    """Onemsiz bir hatayi yutar ama izini birakir.
    Yedekleme aracinda sessizce basarisiz olan islem, hata veren islemden tehlikelidir:
    kullanici her seyin yolunda oldugunu sanir. debug acikken loga dusr."""
    if not _HATA_AYIKLA["acik"]: return
    try: log(f"DEBUG [{nerede}]: {type(e).__name__}: {e}")
    except Exception: pass

def now_str(): return datetime.now().strftime(TS_FMT)

def rotate_log(lf):
    """Log dosyasi log_max_mb'yi asinca .1/.2 diye dondurulur; sinirsiz buyumez."""
    try:
        mx = float(cfg().get("log_max_mb") or 0) * 1024 * 1024
        if not mx or not os.path.exists(lf) or os.path.getsize(lf) <= mx: return
        keep = max(1, int(cfg().get("log_keep") or 1))
        last = f"{lf}.{keep}"
        if os.path.exists(last): os.remove(last)
        for i in range(keep - 1, 0, -1):
            a, b = f"{lf}.{i}", f"{lf}.{i+1}"
            if os.path.exists(a): os.replace(a, b)
        os.replace(lf, f"{lf}.1")
    except Exception as e: yut("rotate_log", e)

_LOG_KILIT = threading.Lock()

def log(msg, plan=None):
    """Birden fazla is parcacigi ayni anda yazabilir; satirlar ic ice girmesin."""
    line = f"{now_str()} | {('['+plan+'] ') if plan else ''}{msg}"
    with _LOG_KILIT:
      try:
          lf = cfg()["log_file"]
          os.makedirs(os.path.dirname(lf), exist_ok=True)
          rotate_log(lf)
          with open(lf, "a") as f: f.write(line + "\n")
      except Exception as e: yut("log", e)
      # Test kosumunda konsolu kirletmesin; servis ve CLI'da normal calisir.
      if not os.environ.get("PVE_GDRIVE_QUIET"): print(line, flush=True)

def tail_bytes(path, maxbytes=1024 * 1024):
    """Dosyanin sadece son maxbytes'ini okur - log buyuse de bellek sabit kalir."""
    try:
        with open(path, "rb") as f:
            f.seek(0, os.SEEK_END); size = f.tell()
            start = max(0, size - maxbytes)
            f.seek(start)
            data = f.read().decode("utf-8", errors="replace")
        lines = data.splitlines()
        return lines[1:] if start > 0 and lines else lines   # yarim kalan ilk satiri at
    except Exception:
        return []

LOG_TAG = re.compile(r"\|\s*\[([^\]]+)\]\s")

def read_log(src="all", n=None):
    """src: 'all' | 'system' (plan etiketi olmayan satirlar) | <plan-id>"""
    n = int(n or cfg().get("log_tail_lines") or 250)
    lines = tail_bytes(cfg().get("log_file", ""))
    if not lines: return ["(log yok)"]
    if src and src != "all":
        out = []
        for l in lines:
            m = LOG_TAG.search(l)
            tag = m.group(1) if m else ""
            if (src == "system" and not tag) or (src != "system" and tag == src):
                out.append(l)
        lines = out
    return lines[-n:] or ["(bu kaynakta kayit yok)"]

def human(n):
    try: n = float(n)
    except Exception as e:
        yut("human", e)
        return "-"
    for u in ["B", "KB", "MB", "GB", "TB"]:
        if n < 1024: return f"{n:.1f} {u}"
        n /= 1024
    return f"{n:.1f} PB"

EMPTY_PLAN_STATE = {"last_run": None, "status": "hic-calismadi", "summary": "", "history": [],
                    "backups": [], "trash": [], "quota": {}, "drive_trash": [], "updated": None}

def read_state():
    try:
        with open(cfg()["state_file"]) as f: st = json.load(f)
    except Exception:
        st = {}
    if "plans" not in st: st = {"plans": {}}
    return st

def write_state(st):
    try:
        sf = cfg()["state_file"]
        os.makedirs(os.path.dirname(sf), exist_ok=True)
        tmp = sf + ".tmp"
        with open(tmp, "w") as f: json.dump(st, f, indent=2, ensure_ascii=False)
        os.replace(tmp, sf)
    except Exception as e:
        log(f"state yazilamadi: {e}")

def pstate(st, pid):
    s = dict(EMPTY_PLAN_STATE); s.update(st.get("plans", {}).get(pid, {})); return s

def put_pstate(pid, patch):
    """Plan durumunu oku-guncelle-yaz (tek surecte cakisma olmasin diye her seferinde taze okur)."""
    st = read_state()
    s = pstate(st, pid); s.update(patch)
    st.setdefault("plans", {})[pid] = s
    st["updated"] = now_str()
    write_state(st)
    return s

# ---------- ILERLEME (canlı durum) ----------
def progress_path(pid):
    return os.path.join(os.path.dirname(cfg()["state_file"]), f"progress-{slug(pid)}.json")

def set_progress(pid, patch, merge=True):
    try:
        cur = (get_progress(pid) or {}) if merge else {}
        cur.update(patch); cur["updated"] = time.time()
        pth = progress_path(pid)
        os.makedirs(os.path.dirname(pth), exist_ok=True)
        tmp = pth + ".tmp"
        with open(tmp, "w") as f: json.dump(cur, f)
        os.replace(tmp, pth)
    except Exception as e: yut("set_progress", e)

def get_progress(pid):
    try:
        with open(progress_path(pid)) as f: return json.load(f)
    except Exception as e:
        yut("get_progress", e)
        return None

def clear_progress(pid):
    try: os.remove(progress_path(pid))
    except Exception as e: yut("clear_progress", e)

# rclone --stats-one-line ciktisi. DIKKAT: gercek cikti "Transferred:" oneki ICERMEZ:
#   INFO  :   976.597 KiB / 976.597 KiB, 100%, 88.775 KiB/s, ETA 0s
# Cok satirli formatta onek vardir, o yuzden onek istege bagli birakildi.
RE_STATS = re.compile(
    r"(?:Transferred:\s*)?([\d.]+\s*\w+)\s*/\s*([\d.]+\s*\w+)\s*,\s*(\d+)\s*%"
    r"(?:\s*,\s*([\d.]+\s*\w+/s))?(?:\s*,\s*ETA\s*(\S+))?")
RE_UNIT = re.compile(r"([\d.]+)\s*([KMGTP]?)i?B", re.I)
RE_DOSYA_SAYISI = re.compile(r"Transferred:\s*(\d+)\s*/\s*(\d+)\s*,\s*\d+\s*%\s*$")

def to_bytes(txt):
    m = RE_UNIT.search(str(txt) or "")
    if not m: return 0
    try: v = float(m.group(1))
    except Exception as e:
        yut("to_bytes", e)
        return 0
    return int(v * (1024 ** "BKMGTP".index((m.group(2) or "B").upper())))

def parse_stats(line):
    m = RE_STATS.search(line or "")
    if not m: return None
    done, total, pct, speed, eta = m.groups()
    return {"done": to_bytes(done), "total": to_bytes(total), "pct": int(pct),
            "done_h": done.strip(), "total_h": total.strip(),
            "speed": (speed or "").strip(), "speed_bps": to_bytes(speed or ""),
            "eta": (eta or "").strip()}

# ---------- RCLONE ----------
def rclone(args, timeout=None):
    """Kisa ciktili komutlar icin (lsjson, about, delete). Ciktiyi tam yakalar."""
    try:
        r = subprocess.run(["rclone"] + args, capture_output=True, text=True, timeout=timeout)
        return r.returncode, r.stdout, r.stderr
    except FileNotFoundError: return 127, "", "rclone bulunamadi (apt install rclone)"
    except subprocess.TimeoutExpired: return 124, "", "zaman asimi"

_ONEK_CACHE = {}

def kaynak_oneki(p):
    """rclone'u nice/ionice ile calistirmak icin komut oneki.
    Yedekleme isi hipervizordeki VM ve CT'leri yavaslatmamali."""
    onek = []
    n = int(p.get("nice", 10) or 0)
    if n and _var_mi("nice"): onek += ["nice", "-n", str(n)]
    sinif = int(p.get("ionice_class", 2) or 0)
    if sinif and _var_mi("ionice"):
        onek += ["ionice", "-c", str(sinif)]
        if sinif == 2: onek += ["-n", str(int(p.get("ionice_level", 6) or 0))]
    return onek

def _var_mi(ad):
    if ad not in _ONEK_CACHE:
        _ONEK_CACHE[ad] = any(os.access(os.path.join(d, ad), os.X_OK)
                              for d in os.environ.get("PATH", "/usr/bin:/bin").split(":") if d)
    return _ONEK_CACHE[ad]

def rclone_stream(args, timeout=None, on_line=None, onek=None):
    """Uzun ciktili komutlar (copy) icin. Cikti satir satir okunur ve yalnizca son
    rclone_tail_lines satiri bellekte tutulur; binlerce dosyada bile RAM sabit kalir."""
    n = max(1, int(cfg().get("rclone_tail_lines") or 40))
    try:
        pr = subprocess.Popen(list(onek or []) + ["rclone"] + args, stdout=subprocess.PIPE,
                              stderr=subprocess.STDOUT, text=True, bufsize=1)
    except FileNotFoundError:
        return 127, ["rclone bulunamadi (apt install rclone)"]
    buf = deque(maxlen=n)
    killer = threading.Timer(timeout, pr.kill) if timeout else None
    if killer:
        killer.daemon = True; killer.start()
    try:
        for line in pr.stdout:
            line = line.rstrip()
            buf.append(line)
            if on_line:
                try: on_line(line)
                except Exception as e: yut("rclone_stream", e)
        pr.wait()
    finally:
        if killer: killer.cancel()
        try: pr.stdout.close()
        except Exception as e: yut("rclone_stream", e)
    if pr.returncode and pr.returncode < 0: buf.append("zaman asimi: rclone durduruldu")
    return pr.returncode, list(buf)

def lsjson_ok(path, extra=None):
    """(basarili_mi, liste) dondurur. 'bos liste' ile 'listeleme hatasi' ayirt edilebilsin diye:
    ikisini karistirmak, silinmemis dosyayi silinmis sanmak gibi tehlikeli sonuclar dogurur."""
    rc, out, err = rclone(["lsjson", path] + (extra or []))
    if rc != 0: return False, []
    try: return True, json.loads(out)
    except Exception as e:
        yut("lsjson_ok", e)
        return False, []

def lsjson(path, extra=None):
    return lsjson_ok(path, extra)[1]

# ---------- LOCK ----------
def lock_path(pid): return os.path.join(LOCK_DIR, f"pve-gdrive-{slug(pid)}.lock")

def is_running(pid):
    try:
        f = open(lock_path(pid), "a+")
        fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)
        fcntl.flock(f, fcntl.LOCK_UN); f.close(); return False
    except OSError: return True
    except Exception as e:
        yut("is_running", e)
        return False

# ---------- PROXMOX YEDEGI ILE CAKISMA ----------
def vzdump_lock_held():
    """vzdump kilit dosyasi kilitli mi. En guvenilir sinyal: Proxmox vzdump bu kilidi tutar."""
    for lk in ("/run/vzdump.lock", "/var/run/vzdump.lock"):
        try:
            if not os.path.exists(lk): continue
            f = open(lk, "a+")
            try:
                fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)
                fcntl.flock(f, fcntl.LOCK_UN)
            except OSError:
                f.close(); return True          # kilitli => vzdump calisiyor
            f.close()
        except Exception as e: yut("vzdump_lock_held", e)
    return False

def vzdump_proc_running():
    """vzdump sureci var mi. -x KULLANILMAZ: vzdump bir perl betigi, surec adi 'perl' gorunur,
    bu yuzden tam komut satirinda (-f) aranir. Kendi surecimiz haric tutulur."""
    try:
        r = subprocess.run(["pgrep", "-f", r"(^|/)vzdump( |$)"],
                           capture_output=True, text=True, timeout=10)
        pids = [x for x in r.stdout.split() if x.strip() and x.strip() != str(os.getpid())]
        return bool(pids)
    except Exception:
        return False

def inprogress(p):
    """Kaynak klasorde yazilmakta olan (yarim) dosyalar."""
    try: names = os.listdir(p["src_dir"])
    except Exception as e:
        yut("inprogress", e)
        return []
    pats = p.get("skip_patterns") or []
    return [n for n in names if any(fnmatch.fnmatch(n, pat) for pat in pats)]

def active_writes(p):
    """Su anda GERCEKTEN yazilan yarim dosyalar. Coken bir yedekten kalan bayat .dat
    dosyasi planı sonsuza kadar bloklamasin diye sadece yakin zamanda dokunulmuslar sayilir."""
    win = (float(p.get("min_age_min") or 0) * 60) or 600
    now = time.time(); out = []
    for n in inprogress(p):
        try:
            if now - os.path.getmtime(os.path.join(p["src_dir"], n)) <= win: out.append(n)
        except Exception as e: yut("active_writes", e)
    return out

def vzdump_running(p=None):
    """Proxmox yedegi calisiyor mu? Uc bagimsiz sinyal; herhangi biri yeterli.
    p verilirse planin kendi kaynak klasorune yazilip yazilmadigina da bakilir."""
    if vzdump_lock_held(): return True
    if vzdump_proc_running(): return True
    if p is not None and active_writes(p): return True
    return False

def wait_for_vzdump(p):
    """vzdump bitene kadar bekler. True: yola devam, False: bu turu atla."""
    if not p.get("wait_for_vzdump", True): return True
    limit = float(p.get("vzdump_wait_min") or 0) * 60
    t0 = time.time(); warned = False
    while vzdump_running(p):
        if time.time() - t0 >= limit: return False
        if not warned:
            log(f"Proxmox yedegi (vzdump) calisiyor, bekleniyor "
                f"(en fazla {p.get('vzdump_wait_min')} dk)", p["id"])
            warned = True
        time.sleep(30)
    if warned: log("vzdump bitti, yuklemeye geciliyor", p["id"])
    return True

# ---------- ZAMANLAMA (tamamen parametrik) ----------
def _hhmm(p):
    try:
        hh, mm = str(p.get("run_at", "03:00")).split(":")[:2]
        return int(hh) % 24, int(mm) % 60
    except Exception as e:
        yut("_hhmm", e)
        return 3, 0

def next_run(p, ref=None):
    hh, mm = _hhmm(p); wd = p.get("weekdays") or []
    now = ref or datetime.now()
    for d in range(0, 9):
        c = (now + timedelta(days=d)).replace(hour=hh, minute=mm, second=0, microsecond=0)
        if c <= now: continue
        if wd and c.isoweekday() not in wd: continue
        return c
    return None

def is_due(p, s, ref=None):
    """Bugunun planlanan saati gectiyse ve o saatten sonra hic calismadiysa vakti gelmistir."""
    if not p.get("enabled", True): return False
    hh, mm = _hhmm(p); wd = p.get("weekdays") or []
    now = ref or datetime.now()
    sched = now.replace(hour=hh, minute=mm, second=0, microsecond=0)
    if now < sched: return False
    if wd and sched.isoweekday() not in wd: return False
    lr = s.get("last_run")
    if lr:
        try:
            if datetime.strptime(lr, TS_FMT) >= sched: return False
        except Exception as e: yut("is_due", e)
    return True

# ---------- HAFTALIK RAPOR ----------
def _at(p, key, dflt):
    try:
        hh, mm = str(p.get(key) or dflt).split(":")[:2]
        return int(hh) % 24, int(mm) % 60
    except Exception as e:
        yut("_at", e)
        return 9, 0

def next_report(p, ref=None):
    if not p.get("weekly_report", True): return None
    hh, mm = _at(p, "report_at", "09:00"); wd = int(p.get("report_day") or 1)
    now = ref or datetime.now()
    for d in range(0, 9):
        c = (now + timedelta(days=d)).replace(hour=hh, minute=mm, second=0, microsecond=0)
        if c <= now or c.isoweekday() != wd: continue
        return c
    return None

def report_due(p, s, ref=None):
    if not p.get("weekly_report", True): return False
    hh, mm = _at(p, "report_at", "09:00")
    now = ref or datetime.now()
    if now.isoweekday() != int(p.get("report_day") or 1): return False
    sched = now.replace(hour=hh, minute=mm, second=0, microsecond=0)
    if now < sched: return False
    lr = s.get("last_report")
    if lr:
        try:
            if datetime.strptime(lr, TS_FMT) >= sched: return False
        except Exception as e: yut("report_due", e)
    return True

# --- rapor bolumleri: iki rapor da (calisma maili ve haftalik ozet) bunlari kullanir ---
def _bolum_gunler(p):
    wd = p.get("weekdays") or []
    return "her gun" if not wd else ",".join(
        ["Pzt", "Sal", "Car", "Per", "Cum", "Cmt", "Paz"][d - 1] for d in wd)

def _bolum_kota(snap):
    """(satirlar, doluluk_yuzdesi)"""
    q = snap.get("quota", {}) or {}
    bt = snap.get("totals", {}) or {}
    tt = snap.get("trash_totals", {}) or {}
    used = float(q.get("used") or 0); total = float(q.get("total") or 0)
    pct = (used / total * 100) if total else 0
    return ([f"  Yedek        : {bt.get('count', 0)} dosya, {human(bt.get('size', 0))}",
             f"  Copte bekleyen: {tt.get('count', 0)} dosya, {human(tt.get('size', 0))}",
             f"  Kota         : {human(used)} / {human(total)} (%{pct:.1f}), bos {human(q.get('free'))}"],
            pct)

def _bolum_saklama(p):
    return [f"  Saklama      : {p['keep_days']} gun, misafir basina en az {p['keep_count']} set",
            f"  Cop suresi   : {p['drive_trash_days']} gun"]

def _bolum_misafirler(p, gs, esik_gun=None):
    """(satirlar, eski_misafirler). gs None ise Drive listelenememis demektir."""
    if gs is None: return ["  (Drive listelenemedi)"], []
    esik = int(esik_gun if esik_gun is not None else p["keep_days"])
    simdi = time.time(); satirlar = []; eski = []
    for g in gs:
        yas = (simdi - g["last"]) / 86400 if g["last"] else None
        iso = datetime.fromtimestamp(g["last"]).strftime("%Y-%m-%d %H:%M") if g["last"] else "-"
        isaret = ""
        if yas is not None and yas > esik:
            isaret = "  <-- SAKLAMA SURESINDEN ESKI"; eski.append(g["guest"])
        satirlar.append(f"  {g['guest']:10} {iso}  {g['sets']:>2} set  {human(g['size']):>9}"
                        f"  {('%.1f gun once' % yas) if yas is not None else ''}{isaret}")
    return satirlar, eski

def _bolum_cop(snap, azami=10):
    t = snap.get("trash", []) or []
    if not t: return []
    tt = snap.get("trash_totals", {}) or {}
    satirlar = [f"COP KUTUSUNDA BEKLEYEN ({tt.get('count', len(t))})"]
    for x in t[:azami]:
        kalan = f"{x['remain_days']} gun" if x.get("tracked") else "izlenmiyor"
        satirlar.append(f"  {human(x['size']):>9}  {kalan:>12} kaldi  {x['name']}")
    return satirlar + [""]

def _bolum_uyarilar(uyarilar):
    return ["UYARILAR"] + ([f"  ! {u}" for u in uyarilar] if uyarilar else ["  Yok."])

def guest_summary(p):
    """Misafir basina son yedek zamani ve set sayisi. Raporun en degerli kismi:
    aylardir yedeklenmeyen bir VM'i burada gorursun."""
    ok, files = lsjson_ok(p["remote"])
    if not ok: return None
    g = {}
    for f in files:
        if f.get("IsDir"): continue
        m = dump_re().match(f.get("Name", ""))
        if not m: continue
        key = f"{m.group(2)}-{m.group(3)}"
        e = g.setdefault(key, {"guest": key, "last": 0, "sets": set(), "size": 0})
        e["last"] = max(e["last"], dt_epoch(m.group(4)))
        e["sets"].add(m.group(4)); e["size"] += int(f.get("Size", 0) or 0)
    out = [{"guest": v["guest"], "last": v["last"], "sets": len(v["sets"]), "size": v["size"]}
           for v in g.values()]
    out.sort(key=lambda x: x["last"])
    return out

def local_guests(p):
    """Kaynak klasordeki misafirler - Drive'a hic cikmamis olan var mi diye."""
    try: names = os.listdir(p["src_dir"])
    except Exception as e:
        yut("local_guests", e)
        return set()
    out = set()
    for n in names:
        m = dump_re().match(n)
        if m: out.add(f"{m.group(2)}-{m.group(3)}")
    return out

def build_report(p):
    """Haftalik ozet metni + uyari sayisi. Bolumler _bolum_* ureticilerinden gelir."""
    days = int(p.get("report_days") or 7)
    s = pstate(read_state(), p["id"])
    snap = update_snapshot(p)
    cutoff = datetime.now() - timedelta(days=days)
    hist = []
    for h in s.get("history", []):
        try:
            if datetime.strptime(h["time"], TS_FMT) >= cutoff: hist.append(h)
        except Exception as e: yut("build_report", e)
    n_ok = sum(1 for h in hist if h.get("status") == "basarili")
    n_err = sum(1 for h in hist if h.get("status") == "HATA")
    n_skip = sum(1 for h in hist if h.get("status") == "atlandi")
    tot = {"yuklenen": 0, "copene": 0, "kalici-silinen": 0}
    for h in hist:
        for part in str(h.get("summary", "")).split("|"):
            kv = part.strip().split(":")
            if len(kv) == 2 and kv[0] in tot:
                try: tot[kv[0]] += int(kv[1])
                except Exception as e: yut("build_report", e)
    nr = next_run(p); nrep = next_report(p)
    kota_satirlari, pct = _bolum_kota(snap)

    L = ["HAFTALIK YEDEK RAPORU",
         f"Plan   : {p['name']} ({p['id']})",
         f"Donem  : son {days} gun  ({cutoff.strftime('%Y-%m-%d')} .. {now_str()[:10]})",
         f"Kaynak : {p['src_dir']}",
         f"Hedef  : {p['remote']}", "",
         "CALISMA",
         f"  Toplam       : {len(hist)}  ({n_ok} basarili, {n_err} hata, {n_skip} atlandi)",
         f"  Son calisma  : {s.get('last_run') or '-'}  ({s.get('status', '-')})",
         f"  Son ozet     : {s.get('summary') or '-'}",
         f"  Program      : {_bolum_gunler(p)} saat {p['run_at']}",
         f"  Sonraki      : {nr.strftime(TS_FMT) if nr else '-'}",
         f"  Yuklenen     : {tot['yuklenen']} dosya",
         f"  Cope giden   : {tot['copene']} dosya",
         f"  Kalici silinen: {tot['kalici-silinen']} dosya", "",
         "DRIVE"] + kota_satirlari + _bolum_saklama(p) + [""]

    uyari = []
    gs = None
    try: gs = guest_summary(p)
    except Exception as e: yut("build_report", e)
    misafir_satirlari, eski = _bolum_misafirler(p, gs)
    L += ["MISAFIR BAZINDA SON YEDEK"] + misafir_satirlari
    for g in eski:
        uyari.append(f"{g} icin en yeni yedek saklama suresinden eski - yedegi aliniyor mu?")
    if gs is not None:
        eksik = sorted(local_guests(p) - {g["guest"] for g in gs})
        if eksik:
            L.append(f"  Kaynakta olup Drive'da olmayan: {', '.join(eksik)}")
            uyari.append("Su misafirler henuz Drive'a cikmadi: " + ", ".join(eksik))
    L.append("")

    if s.get("status") == "HATA": uyari.append("Son calisma HATA ile bitti.")
    stale = int(p.get("report_stale_days") or 0)
    if stale and s.get("last_run"):
        try:
            gecen = (datetime.now() - datetime.strptime(s["last_run"], TS_FMT)).total_seconds() / 86400
            if gecen > stale and s.get("status") != "basarili":
                uyari.append(f"{gecen:.1f} gundur basarili yedek yok.")
        except Exception as e: yut("build_report", e)
    qw = int(p.get("report_quota_warn") or 0)
    if qw and pct >= qw: uyari.append(f"Kota %{pct:.1f} dolulua ulasti (esik %{qw}).")
    kalan = [t for t in snap.get("trash", []) if t.get("tracked") and t.get("remain_days", 1) <= 0]
    if kalan: uyari.append(f"{len(kalan)} dosya suresi doldugu halde Drive copunde duruyor.")

    L += _bolum_uyarilar(uyari)
    L += ["", f"Sonraki rapor: {nrep.strftime(TS_FMT) if nrep else '-'}",
          f"Uretim zamani: {now_str()}"]
    return "\n".join(L), len(uyari)

def send_report(p, trigger="zamanlanmis"):
    to = p.get("report_mail_to") or p.get("mail_to", "")
    if not to:
        log("haftalik rapor atlandi (alici yok)", p["id"]); return False
    try:
        body, n = build_report(p)
    except Exception as e:
        log(f"rapor uretilemedi, kisa surum gonderiliyor: {e}", p["id"])
        s = pstate(read_state(), p["id"])
        body = (f"Haftalik rapor uretilirken hata olustu: {e}\n\n"
                f"Plan: {p['name']} ({p['id']})\nSon calisma: {s.get('last_run')}\n"
                f"Durum: {s.get('status')}\nOzet: {s.get('summary')}\n"
                f"Loga bak: {cfg().get('log_file')}")
        n = 1
    konu = f"[Proxmox Yedek] Haftalik rapor - {p['name']}" + (f" ({n} uyari)" if n else "")
    ok = send_mail(to, konu, body, p.get("smtp_profile"))
    if ok:
        put_pstate(p["id"], {"last_report": now_str(), "last_report_warn": n})
        log(f"haftalik rapor gonderildi ({n} uyari) -> {to}", p["id"])
    return ok

# ---------- CEKIRDEK ----------
def do_copy(p):
    args = ["copy", p["src_dir"], p["remote"],
            "--transfers", str(p["transfers"]), "--checkers", str(p["checkers"]),
            "--drive-chunk-size", str(p["drive_chunk"]),
            "--bwlimit", (p.get("bw_auto_max") or p.get("bwlimit") or "off")
                         if p.get("bwlimit_auto") else bwlimit_arg(p),
            "--stats-one-line", "--stats", f"{int(cfg().get('stats_interval_sec') or 5)}s", "-v"]
    # Yazilmakta olan dosyalar hic alinmasin
    for pat in (p.get("skip_patterns") or []): args += ["--exclude", pat]
    # Son N dakikadir degismemis dosyalar alinsin: vzdump yazarken yakalamayalim
    if float(p.get("min_age_min") or 0) > 0: args += ["--min-age", f"{int(float(p['min_age_min']))}m"]
    args += ["--buffer-size", str(p.get("buffer_size") or "16M")]
    if p.get("use_mmap"): args += ["--use-mmap"]          # bellegi OS'e geri verir
    if p.get("fast_list"): args += ["--fast-list"]        # daha az API cagrisi
    if p.get("no_traverse"): args += ["--no-traverse"]    # hedefi bastan sona listeleme
    if int(p.get("tpslimit") or 0): args += ["--tpslimit", str(int(p["tpslimit"]))]
    args += list(p.get("rclone_extra") or [])
    oto = bool(p.get("bwlimit_auto"))
    port = rc_port(p["id"])
    # rc her zaman acilir: ilerleme bilgisi buradan yapisal olarak okunur.
    # Yalnizca 127.0.0.1'e baglanir, disaridan erisilemez.
    args += ["--rc", "--rc-addr", f"127.0.0.1:{port}", "--rc-no-auth"]
    log(f"rclone copy basladi: {p['src_dir']} -> {p['remote']}", p["id"])
    tmo = float(cfg().get("rclone_timeout_min") or 0) * 60 or None
    t0 = time.time()
    set_progress(p["id"], {"phase": "kopyalama", "phase_label": "Drive'a yükleniyor",
                           "started": t0, "pct": 0})
    rc_calisti = {"ok": False}
    def on_line(l):
        # rc API'si calisiyorsa yapisal veri onceliklidir; log ayristirma yalnizca yedek.
        if rc_calisti["ok"]: return
        st = parse_stats(l)
        if st: set_progress(p["id"], st)
    dur = threading.Event(); isciler = []
    isciler.append(threading.Thread(target=stats_izle, args=(p, port, dur, rc_calisti), daemon=True))
    if oto:
        isciler.append(threading.Thread(target=bw_auto_izle, args=(p, port, dur), daemon=True))
    for t in isciler: t.start()
    try:
        rc, tail = rclone_stream(args, timeout=tmo, on_line=on_line, onek=kaynak_oneki(p))
    finally:
        dur.set()
        for t in isciler: t.join(timeout=5)
    for l in tail[-4:]:
        if l.strip(): log("  " + l, p["id"])
    # Yuklenen dosya sayisi: once rclone'un bitis ozetinden, yoksa son rc olcumunden.
    # Boylece kopyalama oncesi sirf saymak icin tam uzak listeleme yapmamiza gerek kalmaz.
    yuklenen = None
    for l in reversed(tail):
        m = RE_DOSYA_SAYISI.search(l)
        if m: yuklenen = int(m.group(1)); break
    if yuklenen is None:
        yuklenen = int((get_progress(p["id"]) or {}).get("transfers") or 0)
    log(f"rclone copy bitti rc={rc}, yuklenen dosya: {yuklenen}", p["id"])
    return rc == 0, yuklenen

RE_BW = re.compile(r"^(off|\d+(?:\.\d+)?[BKMGT]?)$", re.I)
RE_BW_SCHED = re.compile(r"^\s*([01]?\d|2[0-3]):[0-5]\d\s*,\s*(off|\d+(?:\.\d+)?[BKMGT]?)\s*$", re.I)

def bwlimit_gecerli(txt):
    """(gecerli_mi, mesaj). Hem sabit deger hem saat cizelgesi kabul edilir."""
    txt = str(txt or "").strip()
    if not txt: return True, ""
    if RE_BW.match(txt): return True, ""
    parcalar = txt.split()
    if not parcalar: return False, "bos"
    for x in parcalar:
        if not RE_BW_SCHED.match(x):
            return False, f"'{x}' hatali - 'SS:DD,hiz' olmali (or. 08:00,2M)"
    return True, ""

def bwlimit_arg(p):
    """rclone --bwlimit degeri. Cizelge varsa o kullanilir, yoksa sabit sinir.
    Cizelgede yuklemeye ozel sinir icin 'hiz:hiz' bicimi (yukleme:indirme) desteklenir."""
    sched = str(p.get("bwlimit_schedule") or "").strip()
    sabit = str(p.get("bwlimit") or "off").strip() or "off"
    ham = sched if sched else sabit
    if not p.get("bwlimit_upload_only", True): return ham
    # rclone'da "yukleme:indirme" bicimi; indirmeyi sinirsiz birak
    def ikile(v):
        v = v.strip()
        return v if ":" in v or v.lower() == "off" else f"{v}:off"
    if sched:
        return " ".join(
            (lambda a: f"{a[0]},{ikile(a[1])}")(x.split(",", 1)) if "," in x else x
            for x in ham.split())
    return ikile(ham)

# ---------- OTOMATIK BANT GENISLIGI ----------
BW_BIRIM = {"": 1, "B": 1, "K": 1024, "M": 1024**2, "G": 1024**3, "T": 1024**4}

def bw_bytes(txt):
    """'30M' -> bayt/sn. 'off' veya bos -> 0 (sinirsiz)."""
    t = str(txt or "").strip()
    if not t or t.lower() == "off": return 0
    m = re.match(r"^([\d.]+)\s*([BKMGT]?)$", t, re.I)
    if not m: return 0
    try: return int(float(m.group(1)) * BW_BIRIM[m.group(2).upper()])
    except Exception as e:
        yut("bw_bytes", e)
        return 0

def bw_str(b):
    """bayt/sn -> rclone'un anladigi kisa gosterim."""
    b = int(max(0, b))
    if b <= 0: return "off"
    for birim, carp in (("G", 1024**3), ("M", 1024**2), ("K", 1024)):
        if b >= carp: return f"{b/carp:.2f}{birim}"
    return f"{b}B"

def net_ifaces():
    """Arayuz adi -> (rx, tx) bayt. Sanal/loopback disarida."""
    out = {}
    try:
        with open("/proc/net/dev") as f:
            for line in f.readlines()[2:]:
                ad, _, kalan = line.partition(":")
                ad = ad.strip()
                if ad == "lo" or ad.startswith(("veth", "tap", "fwbr", "fwln", "fwpr", "docker")):
                    continue
                p = kalan.split()
                if len(p) >= 9: out[ad] = (int(p[0]), int(p[8]))
    except Exception as e: yut("net_ifaces", e)
    return out

def default_iface():
    """Varsayilan rotanin arayuzu; bulunamazsa en cok trafik goren fiziksel arayuz."""
    try:
        with open("/proc/net/route") as f:
            for line in f.readlines()[1:]:
                p = line.split()
                if len(p) > 2 and p[1] == "00000000": return p[0]
    except Exception as e: yut("default_iface", e)
    ifs = net_ifaces()
    return max(ifs, key=lambda k: ifs[k][1]) if ifs else ""

def tx_bytes(iface):
    return net_ifaces().get(iface, (0, 0))[1]

def rc_port(pid):
    """Plan basina sabit ama cakismayan yerel rc portu."""
    return 15600 + (int(hashlib.sha256(str(pid).encode()).hexdigest()[:4], 16) % 300)

def rc_call(port, yol, *args):
    rc, out, err = rclone(["rc", "--rc-addr", f"127.0.0.1:{port}", "--rc-no-auth", yol, *args],
                          timeout=20)
    return rc == 0, (out or err or "").strip()

def stats_izle(p, port, dur_bayragi, rc_calisti):
    """rclone'un rc API'sinden yapisal ilerleme okur. Log metni ayristirmaktan
    cok daha guvenilir: alan adlari sabit, birim cevrimi gerekmez."""
    pid = p["id"]
    aralik = max(1, int(cfg().get("stats_interval_sec") or 5))
    while not dur_bayragi.is_set():
        dur_bayragi.wait(aralik)
        if dur_bayragi.is_set(): break
        ok, cikti = rc_call(port, "core/stats")
        if not ok: continue
        try: d = json.loads(cikti)
        except Exception: continue
        rc_calisti["ok"] = True          # rc yanit veriyor: log ayristirici devreden ciksin
        done = int(d.get("bytes") or 0); total = int(d.get("totalBytes") or 0)
        hiz = float(d.get("speed") or 0)
        eta = d.get("eta")
        yama = {"done": done, "total": total, "speed_bps": int(hiz),
                "done_h": human(done), "total_h": human(total),
                "speed": human(hiz) + "/s",
                "transfers": int(d.get("transfers") or 0),
                "errors": int(d.get("errors") or 0)}
        if total: yama["pct"] = min(100, int(done * 100 / total))
        if isinstance(eta, (int, float)) and eta >= 0:
            yama["eta"] = fmt_sure(eta)
        set_progress(pid, yama)

def fmt_sure(sn):
    sn = int(max(0, sn)); s, d, saat = sn % 60, (sn // 60) % 60, sn // 3600
    return (f"{saat}h" if saat else "") + (f"{d}m" if saat or d else "") + f"{s}s"

def bw_auto_izle(p, port, dur_bayragi):
    """rclone calisirken hattaki DIGER trafigi olcup sinirimizi canli ayarlar.
    Boylece UrBackup gibi baska bir yedekleme yazilimi hatti kullandiginda geri cekiliriz."""
    pid = p["id"]
    iface = p.get("bw_auto_iface") or default_iface()
    if not iface:
        log("otomatik bant genisligi: ag arayuzu bulunamadi, sabit sinir kullanilacak", pid); return
    aralik = max(2, int(p.get("bw_auto_interval_sec") or 10))
    link = bw_bytes(p.get("bw_auto_link")) or bw_bytes("100M")
    taban = bw_bytes(p.get("bw_auto_min")) or bw_bytes("1M")
    tavan = bw_bytes(p.get("bw_auto_max")) or bw_bytes(p.get("bwlimit")) or link
    pay = max(0.0, 1.0 - float(p.get("bw_auto_reserve_pct") or 0) / 100.0)
    log(f"otomatik bant genisligi acik: arayuz={iface} link={bw_str(link)} "
        f"taban={bw_str(taban)} tavan={bw_str(tavan)} pay=%{int(pay*100)}", pid)
    alfa = float(p.get("bw_auto_smooth") or 0.4)
    adim = float(p.get("bw_auto_step_pct") or 25) / 100.0
    onceki = tx_bytes(iface); son_hedef = 0
    onceki_done = float((get_progress(pid) or {}).get("done") or 0)
    diger_ema = None
    while not dur_bayragi.is_set():
        dur_bayragi.wait(aralik)
        if dur_bayragi.is_set(): break
        simdi = tx_bytes(iface)
        toplam = max(0, (simdi - onceki)) / aralik
        onceki = simdi
        # Kendi hizimizi rclone'un ORTALAMA hizindan degil, aktarilan bayt sayacinin
        # farkindan hesapla. Ortalama geriden geldigi icin kendi trafigimizi
        # "baskasinin" sanip salinim yapiyorduk.
        pr = get_progress(pid) or {}
        done = float(pr.get("done") or 0)
        bizim = max(0.0, (done - onceki_done) / aralik) if done >= onceki_done else 0.0
        onceki_done = done
        if bizim <= 0: bizim = float(pr.get("speed_bps") or 0)   # sayac yoksa ortalamaya dus
        diger_ham = max(0.0, toplam - bizim)
        diger_ema = diger_ham if diger_ema is None else (alfa * diger_ham + (1 - alfa) * diger_ema)
        diger = diger_ema
        hedef = int(max(taban, min(tavan, link * pay - diger)))
        # gereksiz API cagrisi ve salinim olmasin: kucuk degisimleri yok say
        if son_hedef and abs(hedef - son_hedef) < son_hedef * adim: continue
        ok, cikti = rc_call(port, "core/bwlimit", f"rate={bw_str(hedef)}")
        if ok:
            son_hedef = hedef
            set_progress(pid, {"bw_auto": bw_str(hedef), "bw_other": int(diger),
                               "bw_total": int(toplam), "bw_mine": int(bizim), "bw_iface": iface})
            log(f"bant genisligi -> {bw_str(hedef)} "
                f"(hat: {human(toplam)}/sn, bizim: {human(bizim)}/sn, "
                f"diger: {human(diger)}/sn)", pid)
        else:
            log(f"bant genisligi ayarlanamadi: {cikti[:120]}", pid)

def dt_epoch(s):
    try: return int(datetime.strptime(s, DT_FMT).timestamp())
    except Exception as e:
        yut("dt_epoch", e)
        return 0

def collect_sets(files):
    """Ayni vzdump setine ait dosyalari (log, notes, .vma.zst) tek kayitta toplar."""
    sets = {}
    for f in files:
        if f.get("IsDir"): continue
        m = dump_re().match(f.get("Name", ""))
        if not m: continue
        base = m.group(1)
        s = sets.setdefault(base, {"base": base, "guest": f"{m.group(2)}-{m.group(3)}",
                                   "dt": m.group(4), "epoch": dt_epoch(m.group(4)), "files": []})
        s["files"].append({"name": f["Name"], "size": int(f.get("Size", 0) or 0)})
    return sets

def do_prune(p, files=None):
    """keep_days disinda kalan setleri Google cop kutusuna gonderir.
    keep_count taban: misafir basina en yeni N set gun sinirina bakilmadan korunur."""
    keep_days = int(p["keep_days"]); keep_count = int(p["keep_count"])
    cutoff = time.time() - keep_days * 86400
    by_guest = {}
    for s in collect_sets(lsjson(p["remote"]) if files is None else files).values():
        by_guest.setdefault(s["guest"], []).append(s)
    st = read_state(); tracked = pstate(st, p["id"]).get("drive_trash", [])
    known = {e["name"] for e in tracked}
    moved = 0
    for guest, lst in by_guest.items():
        lst.sort(key=lambda s: (s["epoch"], s["dt"]), reverse=True)
        for i, s in enumerate(lst):
            if i < keep_count: continue            # guvenlik tabani
            if s["epoch"] >= cutoff: continue      # gun siniri icinde
            for fi in s["files"]:
                fn = fi["name"]
                rc, o, e = rclone(["deletefile", f"{p['remote']}/{fn}"])   # varsayilan: Drive copune
                if rc == 0:
                    moved += 1
                    log(f"drive copune tasindi: {fn}", p["id"])
                    if fn not in known:
                        tracked.append({"name": fn, "size": fi["size"], "trashed_at": int(time.time())})
                        known.add(fn)
                else:
                    log(f"cope tasima HATA {fn}: {(e or '').strip()}", p["id"])
    if moved: put_pstate(p["id"], {"drive_trash": tracked})
    return moved

def drive_trash_names(p):
    """(listeleme_basarili, isimler). Basarisizsa cagiran taraf hicbir kaydi dusurmemeli."""
    ok, files = lsjson_ok(p["remote"], ["--drive-trashed-only"])
    return ok, {f["Name"] for f in files if not f.get("IsDir")}

def do_purge_trash(p):
    """drive_trash_days dolan dosyalari Google cop kutusundan kalici siler.
    Once kapsamli silme (sadece bu remote yolu), tutmazsa istege bagli hesap geneli cleanup."""
    days = float(p["drive_trash_days"]); now = int(time.time())
    entries = pstate(read_state(), p["id"]).get("drive_trash", [])
    due = [e for e in entries if now - int(e.get("trashed_at", 0)) >= days * 86400]
    if not due: return 0
    # Dosya basina ayri rclone cagrisi Drive API'sinde ~5-8 sn suruyordu.
    # Tek cagrida --include tekrarlanarak toplu silinir; cok dosyada parcalara bolunur.
    boy = max(1, int(cfg().get("purge_batch") or 50))
    for i in range(0, len(due), boy):
        parca = due[i:i + boy]
        args = ["delete", p["remote"], "--drive-trashed-only", "--drive-use-trash=false"]
        for e in parca: args += ["--include", e["name"]]
        rc, o, err = rclone(args, timeout=float(cfg().get("purge_timeout_min") or 30) * 60)
        if rc != 0:
            log(f"cop temizleme HATA ({len(parca)} dosya): {(err or '').strip()[:200]}", p["id"])
    listed, still = drive_trash_names(p)
    if not listed:
        log("cop listelenemedi, hicbir kayit dusurulmedi (sonraki calismada dogrulanacak)", p["id"])
        return 0
    ok   = [e for e in due if e["name"] not in still]
    fail = [e for e in due if e["name"] in still]
    if fail and cfg().get("allow_account_cleanup"):
        log(f"{len(fail)} dosya kapsamli silmeyle gitmedi -> rclone cleanup (HESAP GENELI)", p["id"])
        rc, o, err = rclone(["cleanup", p["remote"].split(":")[0] + ":"])
        if rc == 0:
            listed, still = drive_trash_names(p)
            if listed:
                ok += [e for e in fail if e["name"] not in still]
                fail = [e for e in fail if e["name"] in still]
        else:
            log(f"cleanup HATA: {(err or '').strip()}", p["id"])
    for e in ok: log(f"kalici silindi (drive copu): {e['name']}", p["id"])
    if fail:
        log(f"UYARI: {len(fail)} dosya Drive copunde kaldi, sonraki calismada tekrar denenecek "
            f"(allow_account_cleanup ile zorlanabilir)", p["id"])
    done = {e["name"] for e in ok}
    if done:
        st = read_state()
        keep = [e for e in pstate(st, p["id"]).get("drive_trash", []) if e["name"] not in done]
        put_pstate(p["id"], {"drive_trash": keep})
    return len(ok)

def update_snapshot(p):
    quota = {}
    rc, out, err = rclone(["about", p["remote"].split(":")[0] + ":", "--json"])
    if rc == 0:
        try: quota = json.loads(out)
        except Exception as e: yut("update_snapshot", e)
    backups = []
    for f in lsjson(p["remote"]):
        if f.get("IsDir"): continue
        m = dump_re().match(f["Name"])
        backups.append({"name": f["Name"], "guest": (f"{m.group(2)}-{m.group(3)}" if m else "-"),
                        "size": f.get("Size", 0), "mod": f.get("ModTime", "")})
    backups.sort(key=lambda x: x["mod"], reverse=True)
    b_totals = {"count": len(backups), "size": sum(int(x["size"] or 0) for x in backups)}
    tracked = {e["name"]: e for e in pstate(read_state(), p["id"]).get("drive_trash", [])}
    grace = float(p["drive_trash_days"]) * 86400
    def row(name, size):
        e = tracked.get(name)
        rem = max(0.0, grace - (time.time() - int(e["trashed_at"]))) if e else 0.0
        return {"name": name, "size": size, "remain_days": round(rem / 86400, 2), "tracked": bool(e)}
    trash, seen = [], set()
    for f in lsjson(p["remote"], ["--drive-trashed-only"]):
        if f.get("IsDir"): continue
        trash.append(row(f["Name"], f.get("Size", 0))); seen.add(f["Name"])
    for n, e in tracked.items():
        if n not in seen: trash.append(row(n, e.get("size", 0)))
    trash.sort(key=lambda x: x["remain_days"])
    t_totals = {"count": len(trash), "size": sum(int(x["size"] or 0) for x in trash)}
    # state.json ve /api/status sinirsiz buyumesin: satirlar kirpilir, toplamlar tam kalir
    mx = max(1, int(cfg().get("snapshot_max_rows") or 200))
    return {"quota": quota, "backups": backups[:mx], "trash": trash[:mx],
            "totals": b_totals, "trash_totals": t_totals, "updated": now_str()}

# ---------- MAIL (cok profilli) ----------
SMTP_DEFAULT = {"id": "", "name": "", "host": "smtp.gmail.com", "port": 587,
                "user": "", "pass": "", "from": "", "security": "starttls"}

def norm_smtp(x):
    q = dict(SMTP_DEFAULT); q.update(x or {})
    q["id"] = str(q.get("id") or "").strip() or slug(q.get("name") or q.get("user") or "smtp")
    q["name"] = str(q.get("name") or q["id"])
    try: q["port"] = int(q["port"])
    except Exception: q["port"] = 587
    if q.get("security") not in ("starttls", "ssl", "none"): q["security"] = "starttls"
    return q

def smtp_profiles(c=None):
    return [norm_smtp(x) for x in (c or cfg()).get("smtp_profiles", [])]

def get_smtp(pid=None, c=None):
    """Plan bir profil adi verir; bos ise ilk profil kullanilir."""
    ps = smtp_profiles(c)
    if not ps: return None
    if pid:
        for x in ps:
            if x["id"] == pid: return x
        log(f"UYARI: '{pid}' smtp profili yok, ilk profil kullaniliyor")
    return ps[0]

def send_mail(to, subject, body, profile=None):
    prof = get_smtp(profile)
    if not prof:
        log("mail atlandi (tanimli SMTP profili yok)"); return False
    if not to:
        log("mail atlandi (alici bos)"); return False
    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = prof.get("from") or prof.get("user")
    msg["To"] = to
    msg.set_content(body)
    sec = prof.get("security", "starttls")
    try:
        if sec == "ssl":
            srv = smtplib.SMTP_SSL(prof["host"], prof["port"], timeout=30)
        else:
            srv = smtplib.SMTP(prof["host"], prof["port"], timeout=30)
            if sec == "starttls": srv.starttls()
        if prof.get("user"): srv.login(prof["user"], prof.get("pass", ""))
        srv.send_message(msg); srv.quit()
        log(f"mail gonderildi [{prof['id']}] -> {to}"); return True
    except Exception as e:
        log(f"mail HATA [{prof['id']}]: {e}"); return False

def notify_wanted(p, status):
    return bool(p.get({"basarili": "notify_success", "HATA": "notify_failure",
                       "atlandi": "notify_skipped"}.get(status, "notify_failure"), False))

def maybe_report(p, status, summary, snap, detay=None):
    if not notify_wanted(p, status):
        log(f"mail atlandi ('{status}' icin bildirim kapali)", p["id"]); return
    try:
        body, uyari = build_run_mail(p, status, summary, snap, detay or {})
    except Exception as e:
        log(f"mail govdesi uretilemedi: {e}", p["id"])
        body = f"Durum: {status}\nOzet: {summary}\nZaman: {now_str()}"; uyari = 0
    konu = f"[Proxmox Yedek] {p['name']} - {status}" + (f" ({uyari} uyari)" if uyari else "")
    send_mail(p.get("mail_to", ""), konu, body, p.get("smtp_profile"))

def build_run_mail(p, status, summary, snap, detay):
    """Tek bir calismanin detayli raporu: ne yapildi, ne silindi, ne durumda."""
    nr = next_run(p); nrep = next_report(p)
    kota_satirlari, pct = _bolum_kota(snap)
    simge = {"basarili": "[OK]", "HATA": "[HATA]", "atlandi": "[ATLANDI]"}.get(status, "[?]")
    L = [f"{simge} {p['name']} - {status}", "=" * 58, "",
         "OZET",
         f"  Zaman        : {now_str()}",
         f"  Tetikleyen   : {detay.get('trigger', '-')}",
         f"  Sure         : {detay.get('dur', '-')} sn",
         f"  Yuklenen     : {detay.get('uploaded', 0)} dosya",
         f"  Cope tasinan : {detay.get('moved', 0)} dosya  ({p['keep_days']} gunden eski setler)",
         f"  Kalici silinen: {detay.get('purged', 0)} dosya  "
         f"(copte {p['drive_trash_days']} gun bekleyenler)", ""]
    if detay.get("skipped"):
        L += ["  ! Yukleme basarisiz oldugu icin RETENTION CALISTIRILMADI.",
              "    Hicbir yedek silinmedi; sorun giderilince kendiliginden devam eder.", ""]
    if detay.get("yarim"):
        L += [f"  ! Yazilmakta olan {len(detay['yarim'])} dosya atlandi: "
              + ", ".join(detay["yarim"][:5]), ""]
    L += ["YAPILANDIRMA",
          f"  Kaynak       : {p['src_dir']}",
          f"  Hedef        : {p['remote']}"] + _bolum_saklama(p) + [
          f"  Program      : {_bolum_gunler(p)} saat {p['run_at']}",
          f"  Sonraki      : {nr.strftime(TS_FMT) if nr else '-'}",
          f"  Hiz siniri   : {p['bwlimit']} | es zamanli: {p['transfers']}", "",
          "DRIVE DURUMU"] + kota_satirlari + [""]

    gs = None
    try: gs = guest_summary(p)
    except Exception as e: yut("build_run_mail", e)
    eski = []
    if gs:
        misafir_satirlari, eski = _bolum_misafirler(p, gs)
        L += ["MISAFIR BAZINDA SON YEDEK"] + misafir_satirlari + [""]
    bl = snap.get("backups", [])[:10]
    if bl:
        L.append("EN YENI YEDEKLER")
        for b in bl:
            L.append(f"  {str(b.get('mod', ''))[:19].replace('T', ' ')}  {b['guest']:9}"
                     f"  {human(b['size']):>9}  {b['name']}")
        L.append("")
    L += _bolum_cop(snap)

    uyari = []
    if status == "HATA": uyari.append("Calisma HATA ile bitti - log dosyasina bak.")
    if detay.get("skipped"): uyari.append("Retention atlandi, eski yedekler birikmeye devam ediyor.")
    qw = int(p.get("report_quota_warn") or 0)
    if qw and pct >= qw: uyari.append(f"Kota %{pct:.1f} - esik %{qw} asildi.")
    if eski: uyari.append("Saklama suresinden eski yedegi olanlar: " + ", ".join(eski))
    L += _bolum_uyarilar(uyari)
    L += ["", f"Haftalik rapor: {nrep.strftime(TS_FMT) if nrep else 'kapali'}",
          f"Log: {cfg().get('log_file')}"]
    return "\n".join(L), len(uyari)

# ---------- CALISTIRMA ----------
def _gecmise_ekle(pid, kayit):
    """Calisma gecmisine bir satir ekler ve history_max ile sinirlar."""
    s = pstate(read_state(), pid)
    h = s.get("history", [])
    h.insert(0, kayit)
    return h[:int(cfg().get("history_max") or 50)]

def _asama_atlandi(p, pid, trigger):
    """vzdump bitmedi: hicbir sey yapmadan cik. Bu bir hata degil, sonraki turda denenir."""
    ozet = f"Proxmox yedegi calisiyordu ({p.get('vzdump_wait_min')} dk beklendi)"
    log("vzdump hala calisiyor, bu tur atlandi (sonraki kontrolde tekrar denenecek)", pid)
    put_pstate(pid, {"status": "atlandi", "summary": ozet, "last_skip": now_str(),
                     "last_run": now_str(), "last_trigger": trigger,
                     "history": _gecmise_ekle(pid, {"time": now_str(), "status": "atlandi",
                                                    "summary": ozet, "trigger": trigger})})
    if notify_wanted(p, "atlandi"):
        try: maybe_report(p, "atlandi", ozet, update_snapshot(p), {"trigger": trigger, "dur": 0})
        except Exception as e: log(f"atlandi maili gonderilemedi: {e}", pid)

def _asama_kopyala(p, pid):
    """(basarili_mi, yuklenen_dosya, atlanan_yarim_dosyalar)"""
    yarim = inprogress(p)
    if yarim:
        log(f"yazilmakta olan {len(yarim)} dosya atlanacak: {', '.join(yarim[:3])}"
            + (" ..." if len(yarim) > 3 else ""), pid)
    # Kopyalama oncesi "kac dosya vardi" listelemesi yapilmaz: yuklenen sayisi rclone'un
    # kendi ozetinden gelir, boylece her calismada bir tam uzak listeleme tasarruf edilir.
    ok, uploaded = do_copy(p)
    return ok, uploaded, yarim

def _retention_calissin_mi(p, ok, listed, pid):
    """PROJENIN EN ONEMLI GUVENLIK KURALI.

    Yukleme basarisizsa veya Drive listelenemiyorsa HICBIR SEY SILINMEZ. Aksi halde
    yeni yedek Drive'a cikmadan eskiler silinir ve hem yerelde hem Drive'da yedeksiz
    kalinabilir. prune_on_failure yalnizca kullanici bilerek actiysa bunu gevsetir,
    o durumda bile Drive listelenebiliyor olmasi sarttir.
    """
    if ok and listed: return True, ""
    sebep = "yukleme basarisiz" if not ok else "Drive listelenemedi"
    if p.get("prune_on_failure") and listed:
        log(f"{sebep} ama prune_on_failure acik, retention yine de calisiyor", pid)
        return True, sebep
    log(f"{sebep} -> retention ATLANDI, hicbir yedek silinmedi", pid)
    return False, sebep

def _asama_retention(p, pid, ok):
    """(cope_tasinan, kalici_silinen, retention_atlandi_mi)"""
    set_progress(pid, {"phase": "listeleme", "phase_label": "Drive listeleniyor"})
    listed, files = lsjson_ok(p["remote"])
    calissin, _ = _retention_calissin_mi(p, ok, listed, pid)
    if not calissin: return 0, 0, True
    set_progress(pid, {"phase": "retention",
                       "phase_label": "Eski yedekler çöpe taşınıyor", "pct": 100})
    moved = do_prune(p, files)
    set_progress(pid, {"phase": "cop", "phase_label": "Çöp kutusu temizleniyor"})
    purged = do_purge_trash(p)
    return moved, purged, False

def _asama_bitir(p, pid, trigger, started, ok, uploaded, moved, purged, atlandi, yarim):
    set_progress(pid, {"phase": "ozet", "phase_label": "Durum güncelleniyor"})
    snap = update_snapshot(p)
    dur = int(time.time() - started)
    status = "basarili" if ok else "HATA"
    summary = (f"yuklenen:{uploaded} | copene:{moved} | kalici-silinen:{purged} | sure:{dur}s"
               + (" | RETENTION ATLANDI" if atlandi else ""))
    log("OZET: " + status + " | " + summary, pid)
    patch = {"last_run": now_str(), "status": status, "summary": summary,
             "last_trigger": trigger, "last_duration": dur,
             "history": _gecmise_ekle(pid, {"time": now_str(), "status": status,
                                            "summary": summary, "trigger": trigger})}
    patch.update(snap)
    put_pstate(pid, patch)
    maybe_report(p, status, summary, snap,
                 {"trigger": trigger, "dur": dur, "uploaded": uploaded, "moved": moved,
                  "purged": purged, "skipped": atlandi, "yarim": yarim})

def _asama_hata(p, pid, e):
    log(f"BEKLENMEDIK HATA: {e}", pid)
    put_pstate(pid, {"last_run": now_str(), "status": "HATA", "summary": str(e)})
    if p.get("notify_failure", True):
        send_mail(p.get("mail_to", ""), f"[Proxmox Yedek] {p['name']} - HATA",
                  f"Plan: {p['name']} ({pid})\nIstisna: {e}\nZaman: {now_str()}",
                  p.get("smtp_profile"))

def do_run(pid, trigger="zamanlanmis"):
    """Bir plani bastan sona calistirir. Asamalar _asama_* fonksiyonlarindadir;
    burada yalnizca sira, kilit ve hata yonetimi durur."""
    p = get_plan(pid)
    if not p:
        log(f"plan bulunamadi: {pid}"); return
    if not p.get("enabled", True) and trigger == "zamanlanmis":
        return
    lock = open(lock_path(pid), "a+")
    try: fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        log("zaten calisan bir yedek var, cikiliyor", pid); return
    started = time.time()
    put_pstate(pid, {"status": "calisiyor"})
    set_progress(pid, {"phase": "hazirlik", "phase_label": "Hazırlanıyor", "started": started,
                       "trigger": trigger, "plan": p["name"], "pct": 0}, merge=False)
    try:
        if not os.path.isdir(p["src_dir"]):
            raise RuntimeError(f"kaynak klasor yok: {p['src_dir']}")
        set_progress(pid, {"phase": "vzdump-bekleme",
                           "phase_label": "Proxmox yedeği bekleniyor"})
        if not wait_for_vzdump(p):
            _asama_atlandi(p, pid, trigger); return
        ok, uploaded, yarim = _asama_kopyala(p, pid)
        moved, purged, atlandi = _asama_retention(p, pid, ok)
        _asama_bitir(p, pid, trigger, started, ok, uploaded, moved, purged, atlandi, yarim)
    except Exception as e:
        _asama_hata(p, pid, e)
    finally:
        clear_progress(pid)
        fcntl.flock(lock, fcntl.LOCK_UN); lock.close()

def do_tick():
    """systemd timer bunu sik araliklarla cagirir; vakti gelen planlari calistirir."""
    st = read_state(); ran = []
    for p in cfg().get("plans", []):
        try:
            if is_due(p, pstate(st, p["id"])):
                log(f"vakti geldi (saat {p['run_at']}), calistiriliyor", p["id"])
                do_run(p["id"], "zamanlanmis"); ran.append(p["id"])
                st = read_state()
        except Exception as e:
            log(f"plan calistirilamadi: {e}", p.get("id", "?"))
    try:
        d = guncelleme_kontrol()
        if d.get("yeni_var") and cfg().get("update_auto"):
            log("otomatik guncelleme aciik, yeni surum kuruluyor")
            s = guncelleme_uygula()
            log(("guncelleme: " + s.get("msg", "")) if s.get("ok") else ("guncelleme basarisiz: " + s.get("msg", "")))
    except Exception as e:
        log(f"guncelleme kontrolu atlandi: {e}")
    # Haftalik raporlar yedekten bagimsiz kontrol edilir: plan kapali olsa bile
    # "hic calismiyor" uyarisinin gitmesi gerekir.
    for p in cfg().get("plans", []):
        try:
            if report_due(p, pstate(st, p["id"])):
                log("haftalik rapor vakti", p["id"])
                send_report(p); ran.append(p["id"] + " (rapor)")
                st = read_state()
        except Exception as e:
            log(f"haftalik rapor uretilemedi: {e}", p.get("id", "?"))
    return ran

# ---------- RCLONE HESAPLARI (birden fazla Google hesabi) ----------
AUTH_OUT = "/tmp/pve-gdrive-auth.out"
_AUTH = {"proc": None, "url": None, "started": 0}

def rclone_remotes():
    """Yapilandirilmis rclone remote'lari. Her plan bunlardan birini hedef secer."""
    rc, out, err = rclone(["listremotes", "--long"])
    res = []
    if rc != 0: return res
    for line in out.splitlines():
        if not line.strip(): continue
        parts = line.split(":", 1)
        if len(parts) != 2: continue
        res.append({"name": parts[0].strip(), "type": parts[1].strip()})
    return res

_KOTA_ONBELLEK = {}   # hesap adi -> {"veri": {...}, "zaman": ts}

def remote_quota_onbellekli(name, zorla=False):
    """Kota sorgusu her arayuz yenilemesinde yapilmaz: bir rclone about cagrisi
    saniyeler surer ve Drive API kotasini yer. quota_cache_min kadar onbellekte tutulur."""
    ttl = float(cfg().get("quota_cache_min") or 15) * 60
    kayit = _KOTA_ONBELLEK.get(name)
    if not zorla and kayit and (time.time() - kayit["zaman"]) < ttl:
        return kayit["veri"]
    veri = remote_quota(name)
    _KOTA_ONBELLEK[name] = {"veri": veri, "zaman": time.time()}
    return veri

def hesap_ozeti(zorla=False):
    """Ana ekranda gosterilecek hesap + kota listesi."""
    out = []
    for r in rclone_remotes():
        q = remote_quota_onbellekli(r["name"], zorla)
        toplam = float(q.get("total") or 0); kullanilan = float(q.get("used") or 0)
        out.append({"name": r["name"], "type": r["type"], "quota": q,
                    "pct": round(kullanilan / toplam * 100, 1) if toplam else None})
    return out

def remote_quota(name):
    rc, out, err = rclone(["about", f"{name}:", "--json"], timeout=45)
    if rc != 0: return {"ok": False, "error": (err or "").strip()[:200]}
    try: q = json.loads(out); q["ok"] = True; return q
    except Exception as e:
        yut("remote_quota", e)
        return {"ok": False, "error": "cozumlenemedi"}

AUTH_PORT = 53682

def port_dolu_mu(port):
    import socket
    sk = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        sk.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        sk.bind(("127.0.0.1", port)); return False
    except OSError:
        return True
    finally:
        try: sk.close()
        except Exception as e: yut("port_dolu_mu", e)

def artik_authorize_temizle():
    """Yarida kalmis rclone yetkilendirme sureclerini kapatir.
    Bunlar OAuth portunu tutar ve sonraki denemeyi engeller."""
    kapatilan = 0
    try:
        r = subprocess.run(["pgrep", "-af", "rclone"], capture_output=True, text=True, timeout=10)
        for satir in r.stdout.splitlines():
            pid, _, cmd = satir.partition(" ")
            if ("authorize" in cmd or "config create" in cmd) and pid.isdigit():
                try: os.kill(int(pid), 9); kapatilan += 1
                except Exception as e: yut("artik_authorize_temizle", e)
    except Exception as e: yut("artik_authorize_temizle", e)
    return kapatilan

def auth_start():
    """rclone'un OAuth yardimcisini baslatir. Google, tarayiciyi 127.0.0.1:53682'ye
    yonlendirdigi icin bu adres kullanicinin KENDI makinesinde acilabilir olmali;
    bu yuzden SSH tuneli komutu da birlikte dondurulur."""
    auth_stop()
    try: os.remove(AUTH_OUT)
    except Exception as e: yut("auth_start", e)
    tunel = f"ssh -N -L {AUTH_PORT}:127.0.0.1:{AUTH_PORT} root@{local_ip() or os.uname().nodename}"
    if port_dolu_mu(AUTH_PORT):
        n = artik_authorize_temizle()
        time.sleep(1)
        if port_dolu_mu(AUTH_PORT):
            return {"ok": False, "url": None, "tunnel": tunel,
                    "msg": f"{AUTH_PORT} portu dolu ve serbest birakilamadi. "
                           f"Sunucuda kontrol et: ss -tlnp | grep {AUTH_PORT}"}
        if n: log(f"yarida kalmis {n} rclone sureci kapatildi (OAuth portu serbest birakildi)")
    f = open(AUTH_OUT, "w")
    _AUTH["proc"] = subprocess.Popen(
        ["rclone", "authorize", "drive", "--drive-scope", "drive.file", "--auth-no-open-browser"],
        stdout=f, stderr=subprocess.STDOUT, start_new_session=True)
    _AUTH["started"] = time.time(); _AUTH["url"] = None
    hata = ""
    for _ in range(60):
        time.sleep(0.25)
        try: txt = open(AUTH_OUT).read()
        except Exception: txt = ""
        m = re.search(r"(http://127\.0\.0\.1:\d+/auth\?state=\S+)", txt)
        if m: _AUTH["url"] = m.group(1); break
        me = re.search(r"^(Error:.*|.*failed.*)$", txt, re.M)
        if me: hata = me.group(1).strip()[:200]; break
    if not _AUTH["url"]:
        log(f"OAuth baslatilamadi: {hata or 'zaman asimi'}")
    return {"ok": bool(_AUTH["url"]), "url": _AUTH["url"], "tunnel": tunel,
            "msg": "" if _AUTH["url"] else (hata or "rclone yetkilendirme baslatilamadi, log dosyasina bak")}

def auth_status():
    try: txt = open(AUTH_OUT).read()
    except Exception: txt = ""
    got = '"access_token"' in txt
    return {"ok": True, "ready": got, "url": _AUTH.get("url"),
            "waiting": bool(_AUTH.get("proc") and _AUTH["proc"].poll() is None)}

def auth_token():
    try: txt = open(AUTH_OUT).read()
    except Exception as e:
        yut("auth_token", e)
        return None
    m = re.search(r'(\{"access_token".*?\})\s*$', txt, re.M | re.S)
    return m.group(1) if m else None

def auth_stop():
    pr = _AUTH.get("proc")
    if pr and pr.poll() is None:
        try: pr.kill()
        except Exception as e: yut("auth_stop", e)
    _AUTH["proc"] = None
    artik_authorize_temizle()

def local_ip():
    try:
        import socket
        sk = socket.socket(socket.AF_INET, socket.SOCK_DGRAM); sk.connect(("8.8.8.8", 53))
        ip = sk.getsockname()[0]; sk.close(); return ip
    except Exception as e:
        yut("local_ip", e)
        return ""

def remote_create(name, token):
    name = re.sub(r"[^A-Za-z0-9_-]", "", str(name or "")).strip()
    if not name: return {"ok": False, "msg": "gecersiz hesap adi"}
    if any(r["name"] == name for r in rclone_remotes()):
        return {"ok": False, "msg": f"'{name}' zaten var"}
    try: json.loads(token)
    except Exception as e:
        yut("remote_create", e)
        return {"ok": False, "msg": "jeton gecerli JSON degil"}
    # --non-interactive: rclone jetonu dogrulamak icin OAuth sunucusu acip asili kalmasin
    rc, out, err = rclone(["config", "create", name, "drive", "scope=drive.file",
                           f"token={token}", "--non-interactive"], timeout=60)
    if rc != 0: return {"ok": False, "msg": (err or out).strip()[:200]}
    try: os.chmod(os.path.expanduser("~/.config/rclone/rclone.conf"), 0o600)
    except Exception as e: yut("remote_create", e)
    q = remote_quota(name)
    _KOTA_ONBELLEK.pop(name, None)
    log(f"rclone hesabi eklendi: {name}")
    return {"ok": True, "msg": f"'{name}' eklendi" + (
        f" ({human(q.get('used'))}/{human(q.get('total'))} kullanimda)" if q.get("ok") else
        " ama kota okunamadi: " + str(q.get("error", ""))[:80]), "name": name}

def remote_delete(name):
    used = [p["name"] for p in cfg().get("plans", []) if p["remote"].split(":")[0] == name]
    if used: return {"ok": False, "msg": "su planlar kullaniyor: " + ", ".join(used)}
    rc, out, err = rclone(["config", "delete", name])
    if rc != 0: return {"ok": False, "msg": (err or "").strip()[:200]}
    _KOTA_ONBELLEK.pop(name, None)
    log(f"rclone hesabi silindi: {name} (Drive'daki dosyalara dokunulmadi)")
    return {"ok": True, "msg": f"'{name}' silindi (Drive'daki dosyalar duruyor)"}

# ---------- PROXMOX KESFI ----------
def pve_storages():
    """/etc/pve/storage.cfg icinden yedek alabilen depolari ve dump klasorlerini cikarir."""
    out, cur = [], None
    try:
        with open("/etc/pve/storage.cfg") as f:
            for line in f:
                m = re.match(r"^(\w+):\s*(\S+)", line)
                if m:
                    cur = {"type": m.group(1), "name": m.group(2), "path": None, "content": ""}
                    out.append(cur)
                elif cur is not None:
                    m2 = re.match(r"\s+(\w+)\s+(.*)", line)
                    if m2:
                        k, v = m2.group(1), m2.group(2).strip()
                        if k in ("path", "content", "export"): cur[k] = v
    except Exception:
        return []
    res = []
    for s in out:
        if not s.get("path") or "backup" not in (s.get("content") or ""): continue
        d = os.path.join(s["path"], "dump")
        res.append({"name": s["name"], "type": s["type"], "path": d,
                    "exists": os.path.isdir(d), "dumps": count_dumps(d)})
    return res

def count_dumps(path):
    try: return len([x for x in os.listdir(path) if dump_re().match(x)])
    except Exception as e:
        yut("count_dumps", e)
        return 0

def browse(path):
    """UI dizin gezgini. browse_roots disina cikilamaz."""
    roots = [os.path.abspath(r) for r in (cfg().get("browse_roots") or ["/"])]
    p = os.path.abspath(path or (roots[0] if roots else "/"))
    if not any(p == r or p.startswith(r + os.sep) for r in roots):
        p = roots[0] if roots else "/"
    dirs = []
    try:
        for n in sorted(os.listdir(p)):
            fp = os.path.join(p, n)
            if os.path.isdir(fp) and not os.path.islink(fp):
                dirs.append({"name": n, "path": fp, "dumps": count_dumps(fp)})
    except Exception as e:
        return {"path": p, "error": str(e), "dirs": [], "roots": roots, "dumps": 0}
    parent = os.path.dirname(p)
    if not any(parent == r or parent.startswith(r + os.sep) for r in roots): parent = ""
    return {"path": p, "parent": parent, "dirs": dirs, "roots": roots, "dumps": count_dumps(p)}

def konteyner_mi():
    """Docker/LXC gibi bir konteynerde miyiz? Zamanlayici ve yeniden baslatma
    davranisi buna gore degisir."""
    if os.environ.get("PVE_GDRIVE_CONTAINER"): return True
    if os.path.exists("/.dockerenv"): return True
    try:
        with open("/proc/1/cgroup") as f:
            ic = f.read()
        if any(x in ic for x in ("docker", "containerd", "kubepods", "lxc")): return True
    except Exception as e: yut("konteyner_mi", e)
    try:
        with open("/proc/1/comm") as f:
            return f.read().strip() != "systemd"
    except Exception as e:
        yut("konteyner_mi", e)
        return False

def ic_zamanlayici_acik():
    v = cfg().get("internal_scheduler")
    return konteyner_mi() if v is None else bool(v)

def servisi_yeniden_baslat():
    """systemd varsa servisi yeniden baslatir; konteynerde surecten cikar,
    Docker'in restart politikasi ayaga kaldirir."""
    if konteyner_mi():
        log("guncelleme kuruldu, konteyner yeniden baslatiliyor (restart policy)")
        threading.Timer(2.0, lambda: os._exit(0)).start()
        return "konteyner yeniden başlatılıyor"
    subprocess.Popen(["systemctl", "restart", "pve-gdrive-ui.service"], start_new_session=True)
    return "servis yeniden başlatılıyor"

def zamanlayici_dongusu(dur_bayragi):
    """Konteynerde systemd timer olmadigi icin tick'i surecin kendisi calistirir."""
    aralik = max(30, int(cfg().get("scheduler_interval_sec") or 300))
    log(f"ic zamanlayici acik ({aralik} sn'de bir kontrol)")
    while not dur_bayragi.is_set():
        dur_bayragi.wait(aralik)
        if dur_bayragi.is_set(): break
        try: do_tick()
        except Exception as e: log(f"zamanlayici hatasi: {e}")

# ---------- OTOMATIK GUNCELLEME ----------
# Guncelleme YALNIZCA program dosyasini degistirir. /etc/pve-gdrive.conf'a ve
# planlara dokunulmaz; yeni ayarlar norm_plan()/load_cfg() icindeki varsayilanlardan
# gelir, eski config otomatik gocer. Yine de her guncellemede ikisinin de yedegi alinir.
class GuncellemeDurumu(dict):
    """Guncelleme kontrolunun sureç genelindeki durumu. Sozluk gibi davranir
    (mevcut cagri yerleri degismesin) ama sorumlulugu adlandirilmis olur."""
    def __init__(self):
        super().__init__(son_kontrol=0, uzak_surum=None, hata="", kuruluyor=False)
    def sifirla(self):
        self.update(son_kontrol=0, uzak_surum=None, hata="", kuruluyor=False)

GUNCELLEME_DURUMU = GuncellemeDurumu()

def surum_dizi(v):
    try: return tuple(int(x) for x in re.findall(r"\d+", str(v))[:3])
    except Exception as e:
        yut("surum_dizi", e)
        return (0,)

def surum_yeni_mi(uzak, yerel=None):
    return surum_dizi(uzak) > surum_dizi(yerel or SURUM)

def betik_yolu():
    return os.path.abspath(getattr(sys.modules["__main__"], "__file__", __file__))

def yedek_dizini():
    d = os.path.join(os.path.dirname(cfg().get("state_file", "/var/lib/pve-gdrive/x")), "yedek")
    os.makedirs(d, exist_ok=True)
    return d

def guncelleme_indir(url=None, zaman_asimi=30):
    """(kaynak_metin, surum, hata). Ag hatasi programi durdurmaz."""
    url = url or cfg().get("update_url")
    if not url: return None, None, "guncelleme adresi tanimli degil"
    try:
        istek = urllib.request.Request(url, headers={"User-Agent": f"pve-gdrive/{SURUM}"})
        with urllib.request.urlopen(istek, timeout=zaman_asimi) as y:
            ham = y.read().decode("utf-8", "replace")
    except Exception as e:
        return None, None, f"indirilemedi: {e}"
    if len(ham) < 20000 or "def do_run(" not in ham:
        return None, None, "indirilen dosya beklenen bicimde degil"
    m = re.search(r'^SURUM\s*=\s*"([^"]+)"', ham, re.M)
    if not m: return None, None, "surum bilgisi bulunamadi"
    return ham, m.group(1), ""

def guncelleme_kontrol(zorla=False):
    d = GUNCELLEME_DURUMU
    if not zorla and not cfg().get("update_check", True):
        return {"ok": True, "kapali": True, "surum": SURUM}
    if not zorla and time.time() - d["son_kontrol"] < 86400:
        return {"ok": True, "surum": SURUM, "uzak": d["uzak_surum"],
                "yeni_var": bool(d["uzak_surum"] and surum_yeni_mi(d["uzak_surum"])),
                "hata": d["hata"]}
    ham, uzak, hata = guncelleme_indir()
    d["son_kontrol"] = time.time(); d["uzak_surum"] = uzak; d["hata"] = hata
    if hata: log(f"guncelleme kontrolu basarisiz: {hata}")
    elif surum_yeni_mi(uzak): log(f"yeni surum var: {uzak} (kurulu: {SURUM})")
    return {"ok": not hata, "surum": SURUM, "uzak": uzak,
            "yeni_var": bool(uzak and surum_yeni_mi(uzak)), "hata": hata}

def guncelleme_uygula(zorla=False):
    """Indir -> dogrula -> yedekle -> kur -> servisi yeniden baslat.
    Basarisiz olursa eski surume geri doner. Config ve planlar korunur."""
    if GUNCELLEME_DURUMU["kuruluyor"]:
        return {"ok": False, "msg": "guncelleme zaten suruyor"}
    calisan = [p["id"] for p in cfg().get("plans", []) if is_running(p["id"])]
    if calisan and not zorla:
        return {"ok": False, "msg": "su planlar calisiyor, once bitmeli: " + ", ".join(calisan)}
    ham, uzak, hata = guncelleme_indir()
    if hata: return {"ok": False, "msg": hata}
    if not surum_yeni_mi(uzak) and not zorla:
        return {"ok": True, "msg": f"zaten guncel ({SURUM})", "surum": SURUM}
    GUNCELLEME_DURUMU["kuruluyor"] = True
    try:
        hedef = betik_yolu()
        yd = yedek_dizini(); damga = datetime.now().strftime("%Y%m%d-%H%M%S")
        # 1) Once yeni dosyayi gecici yere yaz ve SOZDIZIMI ile calisabilirligini dogrula
        gecici = hedef + f".yeni-{damga}"
        with open(gecici, "w", encoding="utf-8") as f: f.write(ham)
        os.chmod(gecici, 0o755)
        r = subprocess.run([sys.executable, "-m", "py_compile", gecici],
                           capture_output=True, text=True, timeout=60)
        if r.returncode != 0:
            os.remove(gecici)
            return {"ok": False, "msg": "indirilen surum derlenmiyor, kurulmadi"}
        ortam = dict(os.environ); ortam["PVE_GDRIVE_QUIET"] = "1"
        r2 = subprocess.run([sys.executable, gecici, "plans"], capture_output=True,
                            text=True, timeout=60, env=ortam)
        if r2.returncode != 0:
            os.remove(gecici)
            return {"ok": False, "msg": "yeni surum mevcut config ile calismadi, kurulmadi: "
                                        + (r2.stderr or "")[:200]}
        # 2) Program ve config yedegi
        prog_yedek = os.path.join(yd, f"pve_gdrive-{SURUM}-{damga}.py")
        shutil.copy2(hedef, prog_yedek)
        try: shutil.copy2(CONFIG_PATH, os.path.join(yd, f"config-{damga}.json"))
        except Exception as e: yut("guncelleme_uygula", e)
        # 3) Yerine koy (atomik) ve servisi yeniden baslat
        os.replace(gecici, hedef)
        log(f"guncelleme kuruldu: {SURUM} -> {uzak} (yedek: {prog_yedek})")
        yedek_temizle(yd)
        nasil = servisi_yeniden_baslat()
        return {"ok": True, "msg": f"{uzak} kuruldu, {nasil}. Planlar ve ayarlar korundu.",
                "surum": uzak, "yedek": prog_yedek}
    except Exception as e:
        log(f"guncelleme HATA: {e}")
        return {"ok": False, "msg": f"guncelleme basarisiz: {e}"}
    finally:
        GUNCELLEME_DURUMU["kuruluyor"] = False

def yedek_temizle(yd):
    try:
        tut = max(1, int(cfg().get("update_backup_keep") or 5))
        for onek in ("pve_gdrive-", "config-"):
            dosyalar = sorted((f for f in os.listdir(yd) if f.startswith(onek)), reverse=True)
            for f in dosyalar[tut:]:
                try: os.remove(os.path.join(yd, f))
                except Exception as e: yut("yedek_temizle", e)
    except Exception as e: yut("yedek_temizle", e)

def guncelleme_geri_al():
    """En son yedege doner."""
    try:
        yd = yedek_dizini()
        adaylar = sorted((f for f in os.listdir(yd) if f.startswith("pve_gdrive-")), reverse=True)
        if not adaylar: return {"ok": False, "msg": "geri donulecek yedek yok"}
        kaynak = os.path.join(yd, adaylar[0])
        shutil.copy2(kaynak, betik_yolu())
        os.chmod(betik_yolu(), 0o755)
        log(f"onceki surume donuldu: {adaylar[0]}")
        nasil = servisi_yeniden_baslat()
        return {"ok": True, "msg": f"{adaylar[0]} geri yuklendi, {nasil}"}
    except Exception as e:
        return {"ok": False, "msg": f"geri alinamadi: {e}"}

# ---------- TLS ----------
TLS_AKTIF = False

def ssl_context():
    """Sertifika ve anahtar okunabiliyorsa TLS baglami dondurur, yoksa None.
    Hata durumunda servis duz HTTP ile ayakta kalir - arayuze erisim kaybedilmesin."""
    C = cfg()
    cert, key = str(C.get("ssl_cert") or ""), str(C.get("ssl_key") or "")
    if not cert or not key: return None
    for yol in (cert, key):
        if not os.path.exists(yol):
            log(f"TLS kapali: dosya yok -> {yol}"); return None
        if not os.access(yol, os.R_OK):
            log(f"TLS kapali: okuma izni yok -> {yol}"); return None
    try:
        ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        ctx.minimum_version = ssl.TLSVersion.TLSv1_2
        ctx.set_ciphers("ECDHE+AESGCM:ECDHE+CHACHA20:DHE+AESGCM:!aNULL:!MD5:!DSS")
        ctx.load_cert_chain(cert, key)
        return ctx
    except Exception as e:
        log(f"TLS kapali: sertifika yuklenemedi ({e})"); return None

def cert_bilgisi():
    """Arayuzde gostermek icin sertifikanin ozeti."""
    C = cfg(); cert = str(C.get("ssl_cert") or "")
    if not TLS_AKTIF or not cert: return None
    try:
        ham = ssl._ssl._test_decode_cert(cert)
        return {"konu": dict(x[0] for x in ham.get("subject", ()) if x).get("commonName", "-"),
                "veren": dict(x[0] for x in ham.get("issuer", ()) if x).get("commonName", "-"),
                "bitis": ham.get("notAfter", "-")}
    except Exception:
        return {"konu": "-", "veren": "-", "bitis": "-"}

# ---------- KIMLIK DOGRULAMA ----------
class GuvenlikDeposu:
    """Oturumlar, captcha kodlari ve hatali giris sayaclari tek yerde, tek kilit altinda.

    Onceden uc ayri modul duzeyi sozluk vardi; testte sifirlanmalari zordu ve
    eszamanlilik varsayimlari ortuktu. Davranis ayni, sorumluluk tek noktada."""

    def __init__(self):
        self.kilit = threading.Lock()
        self.oturumlar = {}    # token -> {user, ip, created, last, csrf, kalici, bitis}
        self.captchalar = {}   # cid   -> {code, exp}
        self.hatalar = {}      # ip    -> {"n": int, "until": ts, "last": ts}

    def sifirla(self):
        """Yalnizca testler icin: depoyu bosaltir."""
        with self.kilit:
            self.oturumlar.clear(); self.captchalar.clear(); self.hatalar.clear()

DEPO = GuvenlikDeposu()

# Geriye donuk isimler: cagri yerleri degismesin diye ayni sozluklere isaret ederler.
SESSIONS = DEPO.oturumlar
CAPTCHAS = DEPO.captchalar
FAILS = DEPO.hatalar
_SEC_LOCK = DEPO.kilit

def hash_pw(pw, salt=None, iters=None):
    iters = int(iters or 200000)
    salt = salt or base64.b64encode(os.urandom(16)).decode()
    dk = hashlib.pbkdf2_hmac("sha256", str(pw).encode(), salt.encode(), iters)
    return f"pbkdf2_sha256${iters}${salt}${base64.b64encode(dk).decode()}"

def verify_pw(pw, stored):
    stored = str(stored or "")
    if not stored: return False
    if not stored.startswith("pbkdf2_sha256$"):
        return hmac.compare_digest(str(pw), stored)      # eski duz metin sifre
    try:
        _, iters, salt, h = stored.split("$", 3)
        dk = hashlib.pbkdf2_hmac("sha256", str(pw).encode(), salt.encode(), int(iters))
        return hmac.compare_digest(base64.b64encode(dk).decode(), h)
    except Exception:
        return False

def ensure_hashed_pw():
    """Config'de duz metin sifre varsa hash'e cevirip dosyaya yazar."""
    C = cfg()
    if not str(C.get("ui_pass", "")).startswith("pbkdf2_sha256$"):
        C["ui_pass"] = hash_pw(C.get("ui_pass", ""))
        try:
            save_cfg(C); log("UI sifresi hash'lendi (config guncellendi)")
        except Exception as e:
            log(f"sifre hash'lenemedi: {e}")

def izinli_aglar():
    aglar = []
    for x in cfg().get("allow_networks") or []:
        try: aglar.append(ipaddress.ip_network(str(x).strip(), strict=False))
        except Exception: log(f"UYARI: gecersiz ag tanimi yok sayildi: {x}")
    return aglar

def ip_izinli(ip):
    """Bos liste = herkese acik. Tanimliysa yalnizca listedeki aglar."""
    aglar = izinli_aglar()
    if not aglar: return True
    try: adres = ipaddress.ip_address(str(ip))
    except Exception as e:
        yut("ip_izinli", e)
        return False
    return any(adres in a for a in aglar)

def client_ip(h):
    if cfg().get("trust_proxy_header"):
        xf = h.headers.get("X-Forwarded-For", "")
        if xf: return xf.split(",")[0].strip()
    try: return h.client_address[0]
    except Exception as e:
        yut("client_ip", e)
        return "?"

def locked_out(ip):
    """Kalan kilit suresi (sn). DIKKAT: sure dolmus olsa bile hatali deneme sayaci
    silinmez - silinirse her denemede sayac sifirlanir ve kilit hic devreye girmez."""
    with _SEC_LOCK:
        f = FAILS.get(ip)
        if not f: return 0
        left = f.get("until", 0) - time.time()
        if left <= 0:
            f["until"] = 0
            return 0
        return int(left)

def note_fail(ip):
    C = cfg()
    with _SEC_LOCK:
        f = FAILS.setdefault(ip, {"n": 0, "until": 0, "last": 0})
        f["last"] = time.time()
        f["n"] += 1
        if f["n"] >= int(C.get("login_max_attempts") or 5):
            f["until"] = time.time() + float(C.get("login_lockout_min") or 15) * 60
            f["n"] = 0
            log(f"GUVENLIK: {ip} adresi {C.get('login_lockout_min')} dk kilitlendi (cok fazla hatali giris)")

def note_ok(ip):
    with _SEC_LOCK: FAILS.pop(ip, None)

def fail_count(ip):
    with _SEC_LOCK: return (FAILS.get(ip) or {}).get("n", 0)

def gc_sessions():
    C = cfg(); now = time.time()
    idle = float(C.get("session_timeout_min") or 120) * 60
    absmax = float(C.get("session_absolute_h") or 24) * 3600
    with _SEC_LOCK:
        for t in [t for t, v in SESSIONS.items()
                  # hatirlanan oturumda hareketsizlik siniri uygulanmaz, mutlak bitis gecerli
                  if (now > v.get("bitis", now + absmax))
                  or (not v.get("kalici") and (now - v["last"] > idle
                                               or now - v["created"] > absmax))]:
            SESSIONS.pop(t, None)
        for c in [c for c, v in CAPTCHAS.items() if v["exp"] < now]:
            CAPTCHAS.pop(c, None)
        # bir saattir dokunulmamis ve kilitli olmayan deneme kayitlarini unut
        for i in [i for i, v in FAILS.items()
                  if v.get("until", 0) < now and now - v.get("last", 0) > 3600]:
            FAILS.pop(i, None)

def new_session(user, ip, kalici=False):
    """kalici=True ise oturum 'beni hatirla' omrunu alir ve cerez tarayici
    kapaninca silinmez. Oturum yine de IP'ye baglidir."""
    tok = secrets.token_urlsafe(32); now = time.time()
    omur = (float(cfg().get("remember_days") or 30) * 86400 if kalici
            else float(cfg().get("session_absolute_h") or 24) * 3600)
    with _SEC_LOCK:
        SESSIONS[tok] = {"user": user, "ip": ip, "created": now, "last": now,
                         "csrf": secrets.token_urlsafe(24),
                         "kalici": bool(kalici), "bitis": now + omur}
    return tok

def get_session(tok, ip):
    gc_sessions()
    with _SEC_LOCK:
        v = SESSIONS.get(tok)
        if not v: return None
        if v["ip"] != ip: return None          # oturum baska IP'ye tasinamaz
        v["last"] = time.time()
        return v

# --- CAPTCHA (disa bagimliligi yok, SVG olarak uretilir) ---
CAP_CHARS = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"     # karisan harfler (I,O,0,1) yok

def new_captcha():
    gc_sessions()
    code = "".join(random.choice(CAP_CHARS) for _ in range(5))
    cid = secrets.token_urlsafe(12)
    with _SEC_LOCK:
        CAPTCHAS[cid] = {"code": code, "exp": time.time() + 300}
    return cid

def check_captcha(cid, ans):
    with _SEC_LOCK:
        v = CAPTCHAS.pop(cid, None)            # tek kullanimlik
    if not v or v["exp"] < time.time(): return False
    return str(ans or "").strip().upper() == v["code"]

def captcha_svg(cid):
    with _SEC_LOCK:
        v = CAPTCHAS.get(cid)
    code = v["code"] if v else "-----"
    W, H = 190, 62
    out = [f'<svg xmlns="http://www.w3.org/2000/svg" width="{W}" height="{H}" viewBox="0 0 {W} {H}">',
           f'<rect width="{W}" height="{H}" fill="#0d1117"/>']
    for _ in range(7):
        x1, y1, x2, y2 = (random.randint(0, W), random.randint(0, H),
                          random.randint(0, W), random.randint(0, H))
        out.append(f'<line x1="{x1}" y1="{y1}" x2="{x2}" y2="{y2}" '
                   f'stroke="#2b3a4d" stroke-width="{random.randint(1,2)}"/>')
    for i, ch in enumerate(code):
        x = 18 + i * 33 + random.randint(-4, 4)
        y = 44 + random.randint(-6, 6)
        rot = random.randint(-28, 28)
        col = random.choice(["#7ee2a8", "#9fd0ff", "#ffd479", "#c9b0ff", "#e6edf3"])
        out.append(f'<text x="{x}" y="{y}" font-family="Menlo,monospace" font-size="{random.randint(28,36)}" '
                   f'font-weight="700" fill="{col}" transform="rotate({rot} {x} {y})">{ch}</text>')
    for _ in range(45):
        out.append(f'<circle cx="{random.randint(0,W)}" cy="{random.randint(0,H)}" '
                   f'r="{random.randint(1,2)}" fill="#3a4a5e"/>')
    out.append("</svg>")
    return "".join(out)

def public_status():
    C = cfg(); st = read_state(); plans = []
    for p in C.get("plans", []):
        s = pstate(st, p["id"])
        nr = next_run(p)
        q = dict(p); q.pop("rclone_extra", None)
        run = is_running(p["id"])
        plans.append({**q, "rclone_extra": p.get("rclone_extra", []),
                      "state": s, "running": run,
                      "progress": get_progress(p["id"]) if run else None,
                      "next_report": (lambda r: r.strftime(TS_FMT) if r else None)(next_report(p)),
                      "next_run": nr.strftime(TS_FMT) if nr else None,
                      "src_exists": os.path.isdir(p["src_dir"]),
                      "src_dumps": count_dumps(p["src_dir"])})
    return {"plans": plans, "updated": st.get("updated"),
            "settings": {k: C.get(k) for k in
                         ("ui_bind", "ui_port", "ui_user", "smtp_host", "smtp_port", "smtp_user",
                          "mail_from", "browse_roots", "allow_account_cleanup", "history_max",
                          "log_tail_lines", "ui_refresh_sec", "rclone_timeout_min", "dump_regex",
                          "rclone_tail_lines", "snapshot_max_rows", "log_max_mb", "log_keep",
                          "stats_interval_sec", "purge_batch", "purge_timeout_min",
                          "ssl_cert", "ssl_key", "cookie_secure", "allow_networks",
                          "update_check", "update_auto", "update_url", "update_backup_keep", "debug",
                          "quota_cache_min",
                          "remember_enabled", "remember_days", "session_timeout_min",
                          "log_file", "state_file")},
            "smtp": [{k: v for k, v in x.items() if k != "pass"} for x in smtp_profiles(C)],
            "smtp_ready": bool(smtp_profiles(C)),
            "tls": {"aktif": TLS_AKTIF, "sertifika": cert_bilgisi()},
            "hesaplar": hesap_ozeti(),
            "surum": SURUM, "konteyner": konteyner_mi(),
            "ic_zamanlayici": ic_zamanlayici_acik(),
            "guncelleme": {"uzak": GUNCELLEME_DURUMU["uzak_surum"],
                           "yeni_var": bool(GUNCELLEME_DURUMU["uzak_surum"]
                                            and surum_yeni_mi(GUNCELLEME_DURUMU["uzak_surum"])),
                           "otomatik": bool(cfg().get("update_auto")),
                           "hata": GUNCELLEME_DURUMU["hata"]}}

class H(BaseHTTPRequestHandler):
    server_version = "pve-gdrive"
    protocol_version = "HTTP/1.1"
    sess = None

    def log_message(self, *a): pass

    def _send(self, code, ctype, body, extra=None):
        if isinstance(body, str): body = body.encode()
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("X-Frame-Options", "SAMEORIGIN")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("Cache-Control", "no-store")
        if TLS_AKTIF:
            self.send_header("Strict-Transport-Security", "max-age=31536000")
        self.send_header("Content-Security-Policy",
                         "default-src 'self'; style-src 'self' 'unsafe-inline'; "
                         "script-src 'self' 'unsafe-inline'; img-src 'self' data:; "
                         "form-action 'self'; frame-ancestors 'self'; base-uri 'none'")
        for k, v in (extra or []): self.send_header(k, v)
        self.end_headers()
        try: self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError): pass

    def _cookie(self, tok, sil=False, omur_sn=None):
        C = cfg()
        parts = [f"pgs={'' if sil else tok}", "Path=/", "HttpOnly", "SameSite=Strict"]
        if sil: parts.append("Max-Age=0")
        elif omur_sn: parts.append(f"Max-Age={int(omur_sn)}")   # tarayici kapaninca silinmesin
        if C.get("cookie_secure") or TLS_AKTIF: parts.append("Secure")
        return ("Set-Cookie", "; ".join(parts))

    def _session(self):
        m = re.search(r"pgs=([A-Za-z0-9_\-]+)", self.headers.get("Cookie", "") or "")
        return get_session(m.group(1), client_ip(self)) if m else None

    def _auth(self, need_csrf=False):
        C = cfg()
        au = self.headers.get("Authorization", "")
        if C.get("api_token") and au.startswith("Bearer "):
            if hmac.compare_digest(au[7:], str(C["api_token"])):   # otomasyon icin
                self.sess = {"user": "api", "csrf": ""}
                return True
        s = self._session()
        if not s:
            self._json({"ok": False, "login": True, "msg": "oturum gerekli"}, 401)
            return False
        if need_csrf and not hmac.compare_digest(
                self.headers.get("X-CSRF-Token", ""), s.get("csrf", "")):
            log(f"GUVENLIK: CSRF dogrulamasi basarisiz ({client_ip(self)})")
            self._json({"ok": False, "msg": "CSRF doğrulaması başarısız, sayfayı yenile"}, 403)
            return False
        self.sess = s
        return True

    def _do_login(self): return do_login(self)

    def _login_page(self, hata="", cid=None):
        C = cfg()
        cap = bool(C.get("captcha_enabled", True)) and \
              fail_count(client_ip(self)) >= int(C.get("captcha_after_fails") or 0)
        cid = cid or (new_captcha() if cap else "")
        kilit = locked_out(client_ip(self))
        hatirla_acik = bool(C.get("remember_enabled", True))
        self._send(200, "text/html; charset=utf-8",
                   LOGIN_HTML.replace("{{HATA}}", _html.escape(hata))
                             .replace("{{ERRD}}", "block" if hata else "none")
                             .replace("{{CAPD}}", "block" if cap else "none")
                             .replace("{{DISABLED}}", "disabled" if kilit else "")
                             .replace("{{HATIRLA}}", "flex" if hatirla_acik else "none")
                             .replace("{{HATIRLAGUN}}", str(int(C.get("remember_days") or 30)))
                             .replace("{{CID}}", cid or ""))

    def _json(self, obj, code=200):
        self._send(code, "application/json; charset=utf-8", json.dumps(obj, ensure_ascii=False))

    def _body(self):
        try: n = int(self.headers.get("Content-Length", "0"))
        except Exception: n = 0
        if n <= 0: return {}
        try: return json.loads(self.rfile.read(n).decode() or "{}")
        except Exception as e:
            yut("_body", e)
            return {}

    def handle_one_request(self):
        try: BaseHTTPRequestHandler.handle_one_request(self)
        except (BrokenPipeError, ConnectionResetError): self.close_connection = True

    def do_HEAD(self):
        """Saglik kontrolu / ters vekil icin: govde yok, yalnizca basliklar."""
        try:
            if not ip_izinli(client_ip(self)):
                self._send(403, "text/plain; charset=utf-8", ""); return
            u = urlparse(self.path)
            kod = 200 if (u.path == "/" or self._session()) else 401
            self._send(kod, "text/html; charset=utf-8", "")
        except Exception:
            try: self._send(500, "text/plain; charset=utf-8", "")
            except Exception as e: yut("do_HEAD", e)

    def do_GET(self):
        try: self._get()
        except Exception as e:
            log(f"API hatasi GET {self.path}: {e}")
            try: self._json({"ok": False, "msg": f"sunucu hatasi: {e}"}, 500)
            except Exception as e: yut("do_GET", e)

    def do_POST(self):
        try: self._post()
        except Exception as e:
            log(f"API hatasi POST {self.path}: {e}")
            try: self._json({"ok": False, "msg": f"sunucu hatasi: {e}"}, 500)
            except Exception as e: yut("do_POST", e)

    def _ag_kontrol(self):
        ip = client_ip(self)
        if ip_izinli(ip): return True
        log(f"GUVENLIK: izinli ag disindan istek reddedildi ({ip} -> {self.path})")
        self._send(403, "text/plain; charset=utf-8",
                   "Bu ağdan erişim kapalı.\n")
        return False

    def _get(self):
        if not self._ag_kontrol(): return
        u = urlparse(self.path); p = u.path; q = parse_qs(u.query)
        if p == "/captcha.svg":
            self._send(200, "image/svg+xml; charset=utf-8", captcha_svg(q.get("cid", [""])[0]))
            return
        if p == "/" and not self._session():
            self._login_page(); return
        if not self._auth(): return
        if p == "/":
            self._send(200, "text/html; charset=utf-8", HTML)
        elif p == "/api/status":
            st = public_status()
            st["csrf"] = (self.sess or {}).get("csrf", "")
            st["user"] = (self.sess or {}).get("user", "")
            self._json(st)
        elif p == "/api/log":
            src = q.get("src", ["all"])[0] or "all"
            self._send(200, "text/plain; charset=utf-8", "\n".join(read_log(src)))
        elif p == "/api/browse":
            self._json(browse(unquote(q.get("path", [""])[0])))
        elif p == "/api/remotes":
            rs = rclone_remotes()
            if q.get("quota", [""])[0] == "1":
                zorla = q.get("force", [""])[0] == "1"
                for r in rs: r["quota"] = remote_quota_onbellekli(r["name"], zorla)
            self._json({"remotes": rs})
        elif p == "/api/remote/auth/status":
            self._json(auth_status())
        elif p == "/api/update/check":
            self._json(guncelleme_kontrol(zorla=q.get("force", [""])[0] == "1"))
        elif p == "/api/ifaces":
            ifs = net_ifaces(); vars = default_iface()
            self._json({"default": vars,
                        "ifaces": [{"name": k, "rx": v[0], "tx": v[1], "default": k == vars}
                                   for k, v in sorted(ifs.items())]})
        elif p == "/api/storages":
            self._json({"storages": pve_storages()})
        else:
            self._send(404, "text/plain; charset=utf-8", "yok")

    def _post(self):
        if not self._ag_kontrol(): return
        u = urlparse(self.path); path = u.path; q = parse_qs(u.query)
        if path == "/login": return self._do_login()
        if path == "/logout":
            m = re.search(r"pgs=([A-Za-z0-9_\-]+)", self.headers.get("Cookie", "") or "")
            if m:
                with _SEC_LOCK: SESSIONS.pop(m.group(1), None)
            self._send(200, "application/json; charset=utf-8", '{"ok":true}', [self._cookie("", True)])
            return
        if not self._auth(need_csrf=True): return
        pid = q.get("plan", [""])[0]
        if path == "/api/plan/save":
            self._json(save_plan(self._body()))
        elif path == "/api/plan/delete":
            self._json(delete_plan(pid))
        elif path == "/api/remote/auth/start":
            self._json(auth_start())
        elif path == "/api/remote/auth/finish":
            tok = auth_token()
            if not tok: self._json({"ok": False, "msg": "jeton henuz gelmedi"})
            else:
                r = remote_create(self._body().get("name", ""), tok)
                if r.get("ok"):
                    auth_stop()
                    try: os.remove(AUTH_OUT)
                    except Exception as e: yut("_post", e)
                self._json(r)
        elif path == "/api/remote/auth/cancel":
            auth_stop()
            try: os.remove(AUTH_OUT)
            except Exception as e: yut("_post", e)
            self._json({"ok": True, "msg": "iptal edildi"})
        elif path == "/api/remote/add":
            b = self._body(); self._json(remote_create(b.get("name", ""), b.get("token", "")))
        elif path == "/api/remote/delete":
            self._json(remote_delete(q.get("name", [""])[0]))
        elif path == "/api/remote/test":
            n = q.get("name", [""])[0]; qq = remote_quota(n)
            self._json({"ok": bool(qq.get("ok")),
                        "msg": (f"{n}: {human(qq.get('used'))} / {human(qq.get('total'))} kullanimda, "
                                f"cop {human(qq.get('trashed'))}") if qq.get("ok")
                               else f"{n}: HATA {qq.get('error','')}"})
        elif path == "/api/update/apply":
            self._json(guncelleme_uygula())
        elif path == "/api/update/rollback":
            self._json(guncelleme_geri_al())
        elif path == "/api/smtp/save":
            self._json(smtp_save(self._body()))
        elif path == "/api/smtp/delete":
            self._json(smtp_delete(q.get("id", [""])[0]))
        elif path == "/api/smtp/test":
            self._json(smtp_test(q.get("id", [""])[0], q.get("to", [""])[0]))
        elif path == "/api/settings/save":
            self._json(save_settings(self._body()))
        elif path == "/api/action":
            self._json(run_action(q.get("do", [""])[0], pid))
        else:
            self._json({"ok": False, "msg": "bilinmeyen istek"}, 404)

def _login_body(handler):
    try: n = int(handler.headers.get("Content-Length", "0"))
    except Exception: n = 0
    raw = handler.rfile.read(n).decode("utf-8", "replace") if n > 0 else ""
    ctype = (handler.headers.get("Content-Type") or "").lower()
    if "json" in ctype:
        try: return json.loads(raw or "{}")
        except Exception as e:
            yut("_login_body", e)
            return {}
    return {k: v[0] for k, v in parse_qs(raw).items()}

def save_plan(data):
    C = cfg(); plans = list(C.get("plans", []))
    pid = str(data.get("id") or "").strip()
    if not str(data.get("name") or "").strip(): return {"ok": False, "msg": "plan adi bos olamaz"}
    if not str(data.get("remote") or "").strip(): return {"ok": False, "msg": "remote bos olamaz"}
    if ":" not in str(data.get("remote")): return {"ok": False, "msg": "remote 'ad:klasor' biciminde olmali"}
    for alan, etiket in (("bwlimit", "hiz siniri"), ("bwlimit_schedule", "hiz cizelgesi")):
        if alan in data:
            ok, msg = bwlimit_gecerli(data[alan])
            if not ok: return {"ok": False, "msg": f"{etiket}: {msg}"}
    if pid and any(p["id"] == pid for p in plans):
        plans = [norm_plan({**p, **data, "id": pid}) if p["id"] == pid else p for p in plans]
        msg = "plan guncellendi"
    else:
        base = slug(data.get("name")); pid = base; i = 2
        while any(p["id"] == pid for p in plans): pid = f"{base}-{i}"; i += 1
        plans.append(norm_plan({**data, "id": pid})); msg = "plan olusturuldu"
    C["plans"] = plans; save_cfg(C)
    log(f"{msg}: {pid}")
    return {"ok": True, "msg": msg, "id": pid}

def delete_plan(pid):
    C = cfg()
    if not get_plan(pid, C): return {"ok": False, "msg": "plan bulunamadi"}
    if is_running(pid): return {"ok": False, "msg": "plan calisirken silinemez"}
    C["plans"] = [p for p in C.get("plans", []) if p["id"] != pid]; save_cfg(C)
    st = read_state(); st.get("plans", {}).pop(pid, None); write_state(st)
    log(f"plan silindi: {pid} (Drive'daki dosyalara dokunulmadi)")
    return {"ok": True, "msg": "plan silindi (Drive'daki yedekler duruyor)"}

def smtp_save(data):
    C = cfg(); ps = list(C.get("smtp_profiles", []))
    if not str(data.get("name") or "").strip(): return {"ok": False, "msg": "profil adi bos olamaz"}
    if not str(data.get("host") or "").strip(): return {"ok": False, "msg": "sunucu bos olamaz"}
    pid = str(data.get("id") or "").strip()
    if pid and any(norm_smtp(x)["id"] == pid for x in ps):
        out = []
        for x in ps:
            x = norm_smtp(x)
            if x["id"] == pid:
                merged = {**x, **data, "id": pid}
                if not data.get("pass"): merged["pass"] = x.get("pass", "")   # bos = degistirme
                out.append(norm_smtp(merged))
            else: out.append(x)
        ps = out; msg = "profil guncellendi"
    else:
        base = slug(data.get("name")); pid = base; i = 2
        while any(norm_smtp(x)["id"] == pid for x in ps): pid = f"{base}-{i}"; i += 1
        ps.append(norm_smtp({**data, "id": pid})); msg = "profil olusturuldu"
    C["smtp_profiles"] = ps; save_cfg(C); log(f"smtp {msg}: {pid}")
    return {"ok": True, "msg": msg, "id": pid}

def smtp_delete(pid):
    C = cfg()
    used = [p["name"] for p in C.get("plans", []) if (p.get("smtp_profile") or "") == pid]
    if used: return {"ok": False, "msg": "su planlar kullaniyor: " + ", ".join(used)}
    n = len(C.get("smtp_profiles", []))
    C["smtp_profiles"] = [x for x in C.get("smtp_profiles", []) if norm_smtp(x)["id"] != pid]
    if len(C["smtp_profiles"]) == n: return {"ok": False, "msg": "profil bulunamadi"}
    save_cfg(C); log(f"smtp profili silindi: {pid}")
    return {"ok": True, "msg": "profil silindi"}

def smtp_test(pid, to):
    prof = get_smtp(pid)
    if not prof: return {"ok": False, "msg": "profil yok"}
    to = to or prof.get("from") or prof.get("user")
    ok = send_mail(to, f"[Proxmox Yedek] SMTP testi - {prof['name']}",
                   f"Bu bir test mailidir.\n\nProfil : {prof['name']} ({prof['id']})\n"
                   f"Sunucu : {prof['host']}:{prof['port']} ({prof['security']})\n"
                   f"Gonderen: {prof.get('from') or prof.get('user')}\nZaman  : {now_str()}", pid)
    return {"ok": ok, "msg": (f"test maili gonderildi -> {to}") if ok else "gonderilemedi (loga bak)"}

def save_settings(data):
    C = cfg()
    for k in ("ui_bind", "ui_user", "smtp_host", "smtp_user", "mail_from", "dump_regex",
              "ssl_cert", "ssl_key", "log_file", "state_file"):
        if k in data: C[k] = str(data[k])
    for k in ("ui_port", "smtp_port", "history_max", "log_tail_lines", "ui_refresh_sec",
              "rclone_tail_lines", "snapshot_max_rows", "log_keep", "stats_interval_sec",
              "purge_batch"):
        if k in data:
            try: C[k] = int(data[k])
            except Exception as e: yut("save_settings", e)
    for k in ("rclone_timeout_min", "log_max_mb"):
        if k in data:
            try: C[k] = float(data[k])
            except Exception as e: yut("save_settings", e)
    if data.get("ui_pass"): C["ui_pass"] = str(data["ui_pass"])
    if data.get("smtp_pass"): C["smtp_pass"] = str(data["smtp_pass"])
    if "allow_account_cleanup" in data: C["allow_account_cleanup"] = bool(data["allow_account_cleanup"])
    if "cookie_secure" in data: C["cookie_secure"] = bool(data["cookie_secure"])
    for k in ("update_check", "update_auto", "debug", "remember_enabled"):
        if k in data: C[k] = bool(data[k])
    for k in ("remember_days", "session_timeout_min"):
        if k in data:
            try: C[k] = max(1, int(data[k]))
            except Exception as e: yut("save_settings", e)
    if data.get("update_url"): C["update_url"] = str(data["update_url"])
    if isinstance(data.get("allow_networks"), list):
        temiz, hatali = [], []
        for x in data["allow_networks"]:
            x = str(x).strip()
            if not x: continue
            try: ipaddress.ip_network(x, strict=False); temiz.append(x)
            except Exception: hatali.append(x)
        if hatali: return {"ok": False, "msg": "gecersiz ag tanimi: " + ", ".join(hatali[:3])}
        C["allow_networks"] = temiz
    if isinstance(data.get("browse_roots"), list):
        C["browse_roots"] = [str(x) for x in data["browse_roots"] if str(x).strip()]
    save_cfg(C); log("ayarlar guncellendi")
    return {"ok": True, "msg": "ayarlar kaydedildi (port/bind degistiyse servisi yeniden baslat)"}

def run_action(do, pid):
    p = get_plan(pid)
    if do in ("backup", "prune", "purgetrash", "refresh", "testmail") and not p:
        return {"ok": False, "msg": "plan bulunamadi"}
    if do == "backup":
        if is_running(pid): return {"ok": False, "msg": "bu plan zaten calisiyor"}
        subprocess.Popen([sys.executable, os.path.abspath(__file__), "run", "--plan", pid,
                          "--trigger", "manuel"], start_new_session=True)
        return {"ok": True, "msg": "yedek baslatildi"}
    if do in ("prune", "purgetrash", "refresh"):
        if is_running(pid): return {"ok": False, "msg": "yedek calisirken yapilamaz"}
        msg = "durum tazelendi"
        if do == "prune": msg = f"{do_prune(p)} dosya Drive copune tasindi"
        elif do == "purgetrash": msg = f"{do_purge_trash(p)} dosya kalici silindi"
        put_pstate(pid, update_snapshot(p))
        return {"ok": True, "msg": msg}
    if do == "testmail":
        nr = next_run(p)
        ok = send_mail(p.get("mail_to", ""), f"[Proxmox Yedek] TEST - {p['name']}",
                       f"Test maili - {now_str()}\nPlan: {p['name']} ({pid})\n"
                       f"Kaynak: {p['src_dir']}\nHedef: {p['remote']}\n"
                       f"Saklama: {p['keep_days']} gun / cop: {p['drive_trash_days']} gun\n"
                       f"Sonraki calisma: {nr.strftime(TS_FMT) if nr else '-'}", p.get("smtp_profile"))
        return {"ok": ok, "msg": "mail gonderildi" if ok else "mail HATA (loga bak)"}
    return {"ok": False, "msg": "bilinmeyen islem"}

def do_login(handler):
    C = cfg(); ip = client_ip(handler)
    kalan = locked_out(ip)
    if kalan:
        log(f"GUVENLIK: kilitli adresten giris denemesi ({ip})")
        return handler._login_page(f"Çok fazla hatalı deneme. {kalan//60+1} dakika sonra tekrar dene.")
    b = _login_body(handler)
    cap_gerek = bool(C.get("captcha_enabled", True)) and \
                fail_count(ip) >= int(C.get("captcha_after_fails") or 0)
    if cap_gerek and not check_captcha(b.get("cid", ""), b.get("captcha", "")):
        note_fail(ip)
        return handler._login_page("Doğrulama kodu hatalı.")
    kullanici = str(b.get("user", ""))
    dogru_kul = hmac.compare_digest(kullanici, str(C.get("ui_user", "")))
    dogru_sif = verify_pw(b.get("pass", ""), C.get("ui_pass", ""))
    if not (dogru_kul and dogru_sif):
        note_fail(ip)
        log(f"GUVENLIK: hatali giris denemesi ({ip}, kullanici='{kullanici[:32]}')")
        return handler._login_page("Kullanıcı adı veya şifre hatalı.")
    note_ok(ip)
    hatirla = bool(C.get("remember_enabled", True)) and str(b.get("remember", "")).lower() in (
        "1", "true", "on", "evet", "yes")
    tok = new_session(C.get("ui_user", ""), ip, kalici=hatirla)
    omur = float(C.get("remember_days") or 30) * 86400 if hatirla else None
    log(f"giris basarili: {C.get('ui_user')} ({ip})"
        + (f" [hatirlaniyor, {int(C.get('remember_days') or 30)} gun]" if hatirla else ""))
    handler._send(302, "text/html; charset=utf-8", "",
                  [handler._cookie(tok, omur_sn=omur), ("Location", "/")])

def serve():
    global TLS_AKTIF
    C = cfg()
    ensure_hashed_pw()
    httpd = ThreadingHTTPServer((C["ui_bind"], int(C["ui_port"])), H)
    ctx = ssl_context()
    if ctx:
        httpd.socket = ctx.wrap_socket(httpd.socket, server_side=True)
        TLS_AKTIF = True
    sema = "https" if TLS_AKTIF else "http"
    log(f"web UI hazir -> {sema}://{C['ui_bind']}:{C['ui_port']}  (kullanici: {C['ui_user']})"
        + (f" | surum {SURUM}" + (" | konteyner" if konteyner_mi() else "")))
    if ic_zamanlayici_acik():
        dur = threading.Event()
        threading.Thread(target=zamanlayici_dongusu, args=(dur,), daemon=True).start()
    if TLS_AKTIF:
        b = cert_bilgisi() or {}
        log(f"TLS acik | sertifika: {b.get('konu')} | veren: {b.get('veren')} | bitis: {b.get('bitis')}")
    else:
        log("TLS kapali - arayuz duz HTTP. VPN disinda kullanma.")
    httpd.serve_forever()

def init_conf():
    if os.path.exists(CONFIG_PATH):
        print(f"{CONFIG_PATH} zaten var, dokunulmadi."); return
    c = dict(GLOBAL_DEFAULTS)
    c["plans"] = [norm_plan({"id": "gunluk", "name": "Gunluk yedek"})]
    with open(CONFIG_PATH, "w") as f: json.dump(c, f, indent=2, ensure_ascii=False)
    os.chmod(CONFIG_PATH, 0o600)
    print(f"yazildi: {CONFIG_PATH}  (remote, ui_pass ve mail bilgilerini duzenle)")

LOGIN_HTML = r"""<!doctype html><html lang="tr"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Giriş — Proxmox Yedek</title><style>
*{box-sizing:border-box;margin:0;padding:0}
body{background:#0d1117;color:#e6edf3;font:14px/1.5 -apple-system,Segoe UI,Roboto,Arial;
 display:flex;align-items:center;justify-content:center;min-height:100vh;padding:20px}
.box{background:#161b22;border:1px solid #232b36;border-radius:14px;padding:26px;width:100%;max-width:380px}
h1{font-size:18px;margin-bottom:4px}
.sub{font-size:12px;color:#8b97a5;margin-bottom:18px}
label{display:block;font-size:12px;color:#9fb4c9;margin:12px 0 5px}
input{width:100%;background:#0d1117;color:#e6edf3;border:1px solid #30363d;border-radius:8px;
 padding:9px 11px;font:14px inherit}
input:focus{outline:none;border-color:#58a6ff}
button{width:100%;margin-top:18px;background:#238636;color:#fff;border:1px solid #2ea043;
 border-radius:8px;padding:10px;font-weight:700;font-size:14px;cursor:pointer}
button:hover{background:#2ea043}
button:disabled{background:#30363d;border-color:#30363d;cursor:not-allowed}
.err{background:#5c1a1a;color:#ff9b9b;border-radius:8px;padding:9px 12px;font-size:13px;margin-bottom:14px}
.cap{display:flex;gap:10px;align-items:center;margin-top:5px}
.cap img{border-radius:8px;border:1px solid #30363d;background:#0d1117}
.cap button{width:auto;margin:0;padding:8px 10px;background:#21262d;border-color:#30363d;font-size:12px}
.foot{font-size:11px;color:#6e7c8c;margin-top:16px;text-align:center;line-height:1.6}
.hatirla{align-items:center;gap:8px;margin-top:14px;font-size:13px;color:#9fb4c9;cursor:pointer}
.hatirla input{width:auto;margin:0}
</style></head><body>
<form class="box" method="POST" action="/login" autocomplete="off">
  <h1>🗄️ Proxmox → Drive Yedek</h1>
  <div class="sub">Devam etmek için giriş yap</div>
  <div class="err" id="err" style="display:{{ERRD}}">{{HATA}}</div>
  <label for="u">Kullanıcı</label>
  <input id="u" name="user" autocomplete="username" required autofocus>
  <label for="p">Şifre</label>
  <input id="p" name="pass" type="password" autocomplete="current-password" required>
  <div id="capwrap" style="display:{{CAPD}}">
    <label for="c">Doğrulama kodu</label>
    <div class="cap">
      <img id="capimg" src="/captcha.svg?cid={{CID}}" width="190" height="62" alt="doğrulama kodu">
      <button type="button" onclick="yenile()" title="Yeni kod">↻</button>
    </div>
    <input id="c" name="captcha" style="margin-top:8px" maxlength="5"
           placeholder="resimdeki 5 karakter" autocomplete="off">
  </div>
  <label class="hatirla" style="display:{{HATIRLA}}">
    <input type="checkbox" name="remember" value="1"> Beni hatırla ({{HATIRLAGUN}} gün)
  </label>
  <input type="hidden" name="cid" id="cid" value="{{CID}}">
  <button type="submit" {{DISABLED}}>Giriş yap</button>
  <div class="foot">İşaretlemezsen oturum hareketsiz kalınca sona erer.<br>
    Çok fazla hatalı denemede adresin geçici olarak kilitlenir.</div>
</form>
<script>
function yenile(){var r=Math.random().toString(36).slice(2);
 fetch("/captcha.svg?cid=").then(function(){});
 location.reload()}
</script></body></html>"""

# --- UI BUNDLE START (build_ui.py uretir, elle duzenleme) ---
HTML = r'''<!doctype html><html lang="tr"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Proxmox → Drive Yedek</title>
<style>
*{box-sizing:border-box;margin:0;padding:0}
body{background:#0d1117;color:#e6edf3;font:14px/1.5 -apple-system,Segoe UI,Roboto,Arial}
.wrap{max-width:1280px;margin:0 auto;padding:18px}
header{display:flex;align-items:center;gap:10px;flex-wrap:wrap;margin-bottom:16px}
h1{font-size:19px;font-weight:650}
.pill{padding:3px 10px;border-radius:999px;font-weight:700;font-size:11px;white-space:nowrap}
.ok{background:#1a4d2e;color:#7ee2a8}.err{background:#5c1a1a;color:#ff9b9b}
.run{background:#4d3d1a;color:#ffd479}.idle{background:#23303f;color:#9fb4c9}
.off{background:#2a2a2a;color:#8b8b8b}
.muted,.small{color:#8b97a5;font-size:12px}
.card{background:#161b22;border:1px solid #232b36;border-radius:12px;padding:14px}
.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(165px,1fr));gap:12px;margin-bottom:16px}
.k{font-size:12px;color:#8b97a5}.v{font-size:20px;font-weight:700;margin-top:4px}
.bar{height:9px;border-radius:6px;background:#232b36;overflow:hidden;margin-top:8px}
.bar>i{display:block;height:100%;background:linear-gradient(90deg,#3fb950,#58a6ff)}
button{background:#21262d;color:#e6edf3;border:1px solid #30363d;border-radius:8px;padding:7px 12px;cursor:pointer;font-weight:600;font-size:13px}
button:hover{background:#2d333b}
button.primary{background:#238636;border-color:#2ea043}button.primary:hover{background:#2ea043}
button.warn{background:#8a2b2b;border-color:#b03636}
button.sm{padding:4px 9px;font-size:12px}
.btns{display:flex;gap:6px;flex-wrap:wrap}
.plans{display:grid;grid-template-columns:repeat(auto-fill,minmax(340px,1fr));gap:12px;margin-bottom:18px}
.plan{background:#161b22;border:1px solid #232b36;border-radius:12px;padding:14px;cursor:pointer}
.plan.sel{border-color:#58a6ff;box-shadow:0 0 0 1px #58a6ff33}
.plan h3{font-size:15px;margin-bottom:2px;display:flex;align-items:center;gap:8px;flex-wrap:wrap}
.row{display:flex;justify-content:space-between;gap:10px;font-size:12px;padding:3px 0;border-bottom:1px solid #1c2330}
.row b{font-weight:600;color:#c9d4df;text-align:right;word-break:break-all}
.row span{color:#8b97a5;white-space:nowrap}
.pbtns{display:flex;gap:6px;flex-wrap:wrap;margin-top:10px}
.panel{background:#161b22;border:1px solid #232b36;border-radius:12px;padding:14px;margin-bottom:16px}
.panel h2{font-size:14px;margin-bottom:10px;color:#c9d4df;display:flex;justify-content:space-between;align-items:center;gap:8px;flex-wrap:wrap}
.cols{display:grid;grid-template-columns:1fr 1fr;gap:16px}
@media(max-width:900px){.cols{grid-template-columns:1fr}}
table{width:100%;border-collapse:collapse;font-size:13px}
th,td{text-align:left;padding:6px 8px;border-bottom:1px solid #232b36;white-space:nowrap}
th{color:#8b97a5;font-weight:600}td.r,th.r{text-align:right}
.tag{font-size:11px;padding:1px 7px;border-radius:6px;background:#1f2a37;color:#9fd0ff}
.tag.lxc{background:#2a2140;color:#c9b0ff}
pre{background:#0a0e13;border:1px solid #232b36;border-radius:10px;padding:12px;max-height:320px;overflow:auto;font:12px/1.5 ui-monospace,Menlo,monospace;color:#b9c6d3}
.flash{position:fixed;right:16px;bottom:16px;background:#238636;color:#fff;padding:10px 16px;border-radius:8px;opacity:0;transition:.3s;pointer-events:none;z-index:99;max-width:60vw}
.flash.show{opacity:1}
.mask{position:fixed;inset:0;background:#000a;display:none;align-items:flex-start;justify-content:center;padding:24px;overflow:auto;z-index:50}
.mask.show{display:flex}
.modal{background:#161b22;border:1px solid #30363d;border-radius:14px;padding:18px;width:100%;max-width:760px}
.modal h2{font-size:16px;margin-bottom:4px}
fieldset{border:1px solid #232b36;border-radius:10px;padding:12px;margin:12px 0}
legend{font-size:12px;color:#8b97a5;padding:0 6px}
.f{display:grid;grid-template-columns:170px 1fr;gap:8px;align-items:start;margin-bottom:10px}
@media(max-width:640px){.f{grid-template-columns:1fr}}
.f>label{font-size:12px;color:#9fb4c9;padding-top:7px;cursor:help;border-bottom:none}
.f>label{border-bottom:none}
.f>label.tip{text-decoration:underline dotted #46536380;text-underline-offset:3px}
input,select,textarea{width:100%;background:#0d1117;color:#e6edf3;border:1px solid #30363d;border-radius:7px;padding:7px 9px;font:13px/1.4 inherit}
input:focus,select:focus,textarea:focus{outline:none;border-color:#58a6ff}
input.bad,select.bad,textarea.bad{border-color:#f85149;background:#1d1113}
input[type=checkbox]{width:auto}
.hint{font-size:11px;color:#6e7c8c;margin-top:4px;line-height:1.45}
.hint b{color:#9fb4c9}
.eg{font-size:11px;color:#7d8fa3;background:#0f151d;border-left:2px solid #2d3d50;padding:5px 8px;margin-top:5px;border-radius:0 6px 6px 0}
.errmsg{font-size:11px;color:#ff9b9b;margin-top:4px;display:none}
.errmsg.show{display:block}
.wd{display:flex;gap:5px;flex-wrap:wrap}
.wd label,.cb{display:flex;align-items:center;gap:5px;background:#0d1117;border:1px solid #30363d;border-radius:7px;padding:5px 9px;font-size:12px;cursor:pointer}
.cbrow{display:flex;gap:6px;flex-wrap:wrap}
.inline{display:flex;gap:6px;align-items:center}
.dirlist{max-height:300px;overflow:auto;border:1px solid #232b36;border-radius:8px;margin-top:8px}
.dirlist div{padding:7px 10px;border-bottom:1px solid #1c2330;cursor:pointer;display:flex;justify-content:space-between;gap:8px}
.dirlist div:hover{background:#1c2330}
.mbtns{display:flex;gap:8px;justify-content:flex-end;margin-top:14px;flex-wrap:wrap}
.crumb{font:12px/1.4 ui-monospace,Menlo,monospace;color:#9fd0ff;word-break:break-all}
.tabs{display:flex;gap:4px;flex-wrap:wrap}
.tabs button{padding:4px 10px;font-size:12px;background:#0d1117}
.tabs button.on{background:#1f6feb;border-color:#1f6feb;color:#fff}
code{background:#0f151d;padding:1px 5px;border-radius:4px;font:12px ui-monospace,Menlo,monospace;color:#9fd0ff}

/* --- plan sihirbazi --- */
.wsteps{display:flex;gap:6px;flex-wrap:wrap;margin:12px 0 4px}
.wsteps span{font-size:11px;padding:4px 10px;border-radius:999px;background:#0d1117;
 border:1px solid #30363d;color:#8b97a5;white-space:nowrap}
.wsteps span.on{background:#1f6feb;border-color:#1f6feb;color:#fff;font-weight:700}
.wsteps span.ok{background:#1a4d2e;border-color:#2ea043;color:#7ee2a8}
.wbaslik{font-size:14px;color:#c9d4df;margin:12px 0 2px}
.wbaslik .small{display:block;margin-top:2px}
.ozet{width:100%;border-collapse:collapse;font-size:13px}
.ozet td{padding:6px 8px;border-bottom:1px solid #232b36;vertical-align:top}
.ozet td:first-child{color:#8b97a5;width:40%;white-space:nowrap}
.ozet td:last-child{color:#e6edf3;word-break:break-all}
.ozet tr.uyari td{color:#ffd479}

/* --- ana ekran hesap/kota seridi --- */
.hesaplar{display:grid;grid-template-columns:repeat(auto-fill,minmax(230px,1fr));gap:10px;margin-bottom:14px}
.hesap{background:#161b22;border:1px solid #232b36;border-radius:10px;padding:10px 12px;cursor:pointer}
.hesap:hover{border-color:#30404f}
.hesap .ad{display:flex;justify-content:space-between;align-items:baseline;gap:8px}
.hesap .ad b{font-size:13px}
.hesap .ad span{font-size:11px;color:#8b97a5}
.hesap .mini{height:6px;border-radius:4px;background:#232b36;overflow:hidden;margin:7px 0 5px}
.hesap .mini i{display:block;height:100%;background:#3fb950}
.hesap.uyari .mini i{background:#d29922}
.hesap.dolu .mini i{background:#f85149}
.hesap .alt{font-size:11px;color:#8b97a5}
.hesap.hata{border-color:#5c1a1a}

</style></head><body>
<div class="wrap">
<header>
  <h1>🗄️ Proxmox → Google Drive Yedek</h1>
  <span id="tlsrozet"></span>
  <span id="uprozet"></span>
  <span class="muted" id="hinfo"></span>
  <span style="flex:1"></span>
  <button onclick="openSettings()" title="Hesaplar, mail profilleri, güvenlik ve gelişmiş ayarlar">⚙ Ayarlar</button>
  <button class="primary" onclick="openEditor(null)">+ Yeni Plan</button>
</header>

<div class="hesaplar" id="hesapserit"></div>
<div class="plans" id="plans"></div>
<div id="detail"></div>

<div class="panel"><h2>Log
  <span class="tabs" id="logtabs"></span></h2>
  <pre id="log">yükleniyor…</pre>
</div>
</div>

<div class="mask" id="m-edit"><div class="modal">
  <h2 id="ed-title">Plan</h2>
  <div class="small" id="ed-alt">Gün cinsinden tüm değerler burada. Alan adlarının üstüne gelince açıklama çıkar.</div>
  <div id="w-adimlar" class="wsteps"></div>
<div class="wstep" data-step="1" style="display:none">
  <div class="wbaslik"><b>1. Plan</b> <span class="small">Bu plana bir ad ver</span></div>
  <fieldset><legend>Hazır senaryolar</legend>
    <div class="btns">
      <button class="sm" onclick="preset('gunluk')" title="Her gün 03:00, Drive'da 14 gün, çöpte 1 gün. Çoğu kurulum için doğru başlangıç.">📅 Günlük — 14 gün</button>
      <button class="sm" onclick="preset('haftalik')" title="Her Pazar 05:00, Drive'da 180 gün, çöpte 7 gün, düşük hız. Uzun süreli arşiv.">🗄️ Haftalık arşiv — 6 ay</button>
      <button class="sm" onclick="preset('kritik')" title="Her gün 02:00, Drive'da 30 gün, misafir başına en az 7 set, çöpte 3 gün.">🔒 Kritik — 30 gün</button>
      <button class="sm" onclick="preset('test')" title="Her gün, 2 gün sakla, çöpte yarım gün. Kurulumu denemek için.">🧪 Test</button>
    </div>
    <div class="hint">Senaryo seçmek formu doldurur; sonra istediğini değiştirebilirsin. Kaydetmeden hiçbir şey uygulanmaz.</div>
  </fieldset>
  <fieldset><legend>Genel</legend>
    <div class="f"><label class="tip" title="Listede ve mail konularında görünecek ad. Plan kimliği bu addan türetilir.">Plan adı</label>
      <div><input id="e-name" placeholder="Günlük VM yedeği">
        <div class="errmsg" id="err-name"></div>
        <div class="eg">Örnek: <b>Günlük VM + CT</b>, <b>Haftalık arşiv</b>, <b>Sadece veritabanı sunucusu</b></div></div></div>
    <div class="f"><label class="tip" title="Kapalıysa zamanlayıcı bu planı atlar. Elle 'Yedekle' ile yine çalıştırabilirsin.">Etkin</label>
      <div><label class="cb"><input type="checkbox" id="e-enabled"> zamanlayıcı bu planı çalıştırsın</label></div></div>
  </fieldset>
</div>
<div class="wstep" data-step="2" style="display:none">
  <div class="wbaslik"><b>2. Kaynak</b> <span class="small">Proxmox'ta hangi klasör yedeklenecek</span></div>
  <fieldset><legend>Kaynak (Proxmox)</legend>
    <div class="f"><label class="tip" title="Proxmox'un vzdump çıktılarını yazdığı klasör. Depo başına ayrı bir dump klasörü olur.">Yedek klasörü</label>
      <div><div class="inline"><input id="e-src" placeholder="/var/lib/vz/dump">
        <button class="sm" onclick="openBrowser()">📁 Gözat</button></div>
        <div class="errmsg" id="err-src"></div>
        <div class="hint" id="e-srchint"></div><div id="e-stor" class="hint"></div>
        <div class="eg">Depo <code>local</code> → <code>/var/lib/vz/dump</code> · Depo <code>Usb1Tb</code> → <code>/mnt/pve/Usb1Tb/dump</code></div></div></div>
  </fieldset>
</div>
<div class="wstep" data-step="3" style="display:none">
  <div class="wbaslik"><b>3. Hedef</b> <span class="small">Hangi Google hesabına, hangi klasöre</span></div>
  <fieldset><legend>Hedef (Google hesabı)</legend>
    <div class="f"><label class="tip" title="Yedeğin yükleneceği Google hesabı. Başkasının hesabını da ekleyip burada seçebilirsin.">Hesap</label>
      <div><div class="inline"><select id="e-acct" style="flex:1"></select>
        <button class="sm primary" onclick="wHesapEkle()">＋ Yeni hesap</button>
        <button class="sm" onclick="openAccounts()">👤 Yönet</button></div>
        <div class="errmsg" id="err-acct"></div>
        <div class="hint" id="e-accthint"></div></div></div>
    <div id="w-hesap-yuvasi" style="margin:10px 0"></div>
    <div class="f"><label class="tip" title="Hesabın içindeki hedef klasör. Yoksa ilk çalışmada oluşturulur.">Klasör</label>
      <div><input id="e-folder" placeholder="proxmox-yedek">
        <div class="errmsg" id="err-folder"></div>
        <div class="hint"><b>Her plan farklı klasöre yazmalı.</b> Aynı klasörü paylaşan iki plan birbirinin yedeğini eski sanıp siler.</div>
        <div class="eg">Örnek: <code>proxmox-yedek</code> · <code>arsiv/2026</code> · <code>pve/usb1tb</code></div></div></div>
  </fieldset>
</div>
<div class="wstep" data-step="4" style="display:none">
  <div class="wbaslik"><b>4. Saklama</b> <span class="small">Yedekler ne kadar süre kalsın</span></div>
  <fieldset><legend>Saklama süreleri</legend>
    <div class="f"><label class="tip" title="Bu günden eski yedek setleri Google çöp kutusuna gönderilir. Süre dosyanın adındaki tarihe göre hesaplanır.">Drive'da tut (gün)</label>
      <div><input type="number" min="0" id="e-kd"><div class="errmsg" id="err-kd"></div>
        <div class="eg">14 gün + günlük yedek ≈ 14 set. Yer hesabı: günlük yedek boyutu × gün sayısı.</div></div></div>
    <div class="f"><label class="tip" title="Güvenlik tabanı: misafir başına bu kadar set, gün sınırına bakılmadan korunur.">En az set (adet)</label>
      <div><input type="number" min="0" id="e-kc"><div class="errmsg" id="err-kc"></div>
        <div class="eg">Bir VM 3 aydır yedeklenmiyorsa gün kuralı hepsini silerdi; bu ayar son <b>N</b> seti korur. <b>0 yazma.</b></div></div></div>
    <div class="f"><label class="tip" title="Google çöp kutusunda bekleme süresi. Bu süre dolunca kalıcı silinir ve kota boşalır.">Çöpte bekle (gün)</label>
      <div><input type="number" min="0" step="0.5" id="e-td"><div class="errmsg" id="err-td"></div>
        <div class="eg">Yanlış silme olursa bu süre içinde Drive'dan geri alabilirsin. <b>0</b> = çöpe uğramadan hemen kalıcı sil.</div></div></div>
  </fieldset>
</div>
<div class="wstep" data-step="5" style="display:none">
  <div class="wbaslik"><b>5. Zamanlama</b> <span class="small">Ne zaman çalışsın, çakışma nasıl önlensin</span></div>
  <fieldset><legend>Zamanlama</legend>
    <div class="f"><label class="tip" title="Planın başlayacağı saat (24 saat biçimi). Proxmox'un kendi yedek işi bittikten sonrasını seç.">Saat</label>
      <div><input id="e-runat" placeholder="03:00"><div class="errmsg" id="err-runat"></div>
        <div class="eg">Proxmox işin 21:00'de başlıyorsa <b>03:00</b> güvenli. Çakışırsa zaten beklenir ama boşuna beklemeyelim.</div></div></div>
    <div class="f"><label class="tip" title="Hiçbiri seçili değilse her gün çalışır. Seçersen sadece o günler.">Günler</label>
      <div><div class="wd" id="e-wd"></div>
        <div class="hint">Boş = her gün. Haftalık arşiv için tek gün seç.</div></div></div>
  </fieldset>
  <fieldset><legend>Proxmox ile çakışma koruması</legend>
    <div class="f"><label class="tip" title="Proxmox'un kendi yedeği çalışırken yüklemeye başlanmaz. Kilit dosyası, süreç ve yazılan dosyalar kontrol edilir.">vzdump'ı bekle</label>
      <div><label class="cb"><input type="checkbox" id="e-wv"> Proxmox yedeği biterken bekle</label></div></div>
    <div class="f"><label class="tip" title="Bu süre dolarsa tur atlanır ve sonraki kontrolde yeniden denenir. Hiçbir şey silinmez.">En fazla bekleme (dk)</label>
      <div><input type="number" min="0" id="e-wvm"><div class="errmsg" id="err-wvm"></div>
        <div class="eg">Proxmox yedeğin ~1.5 saat sürüyorsa <b>120</b> uygun. <b>0</b> = hiç bekleme, hemen atla.</div></div></div>
    <div class="f"><label class="tip" title="Sadece bu kadar dakikadır değişmemiş dosyalar yüklenir. Yazılmakta olan yedek yarım gitmez.">Dosya yaşı (dk)</label>
      <div><input type="number" min="0" id="e-mage"><div class="errmsg" id="err-mage"></div>
        <div class="eg"><b>10</b> iyi bir değer. Çok düşürürsen yazılmakta olan dosyayı yakalama riski artar.</div></div></div>
    <div class="f"><label class="tip" title="Bu desenlere uyan dosyalar hiç yüklenmez. vzdump geçici dosyaları burada.">Atlanacak desenler</label>
      <div><input id="e-skip" placeholder="*.dat *.tmp *.part"><div class="errmsg" id="err-skip"></div>
        <div class="eg">Boşlukla ayır. vzdump yazarken <code>.dat</code> uzantısı kullanır.</div></div></div>
    <div class="f"><label class="tip" title="Kapalıyken yükleme başarısızsa hiçbir yedek silinmez. Açmanız önerilmez.">Hatada retention</label>
      <div><label class="cb"><input type="checkbox" id="e-pof"> yükleme başarısız olsa da eskileri sil</label>
        <div class="hint">⚠ <b>Kapalı bırak.</b> Açarsan yeni yedek Drive'a çıkmadan eskiler silinebilir — hem yerelde hem Drive'da yedeksiz kalma riski.</div></div></div>
  </fieldset>
</div>
<div class="wstep" data-step="6" style="display:none">
  <div class="wbaslik"><b>6. Aktarım</b> <span class="small">Hız ve kaynak kullanımı</span></div>
  <fieldset><legend>Aktarım</legend>
    <div class="f"><label class="tip" title="Sabit yükleme hız sınırı. Çizelge doluysa çizelge önceliklidir.">Hız sınırı</label>
      <div><input id="e-bw" placeholder="30M"><div class="errmsg" id="err-bw"></div>
        <div class="eg"><code>30M</code> = 30 MB/sn · <code>2M</code> yavaş hat için · <code>off</code> = sınırsız</div></div></div>
    <div class="f"><label class="tip" title="Saate göre değişen hız sınırı. Doluysa yukarıdaki sabit sınırın yerine geçer.">Saatlik çizelge</label>
      <div><input id="e-bwsch" placeholder="08:00,2M 19:00,30M 23:00,off"><div class="errmsg" id="err-bwsch"></div>
        <div class="eg">Mesaide hattı boğma, gece hızlan: <code>08:00,2M 19:00,10M 23:00,off</code><br>
          Her giriş <b>o saatten itibaren</b> geçerli olur. Boş bırakırsan sabit sınır kullanılır.</div>
        <div class="btns" style="margin-top:6px">
          <button class="sm" onclick="bwPreset('')">Çizelgesiz</button>
          <button class="sm" onclick="bwPreset('08:00,2M 19:00,10M 23:00,off')">Mesai dostu</button>
          <button class="sm" onclick="bwPreset('00:00,off 07:00,1M 23:00,off')">Sadece gece</button>
        </div></div></div>
    <div class="f"><label class="tip" title="Açıkken sınır yalnızca yüklemeye uygulanır, indirme etkilenmez.">Sadece yükleme</label>
      <div><label class="cb"><input type="checkbox" id="e-bwup"> sınır yalnızca yüklemeye uygulansın</label>
        <div class="hint">Bu araç zaten yükleme yapar; kapatırsan listeleme/indirme de yavaşlar.</div></div></div>
  </fieldset>
  <fieldset><legend>Otomatik bant genişliği</legend>
    <div class="f"><label class="tip" title="Hattaki diğer trafiği ölçüp yükleme hızını canlı ayarlar. Başka bir yedekleme yazılımı hattı kullandığında geri çekilir.">Otomatik mod</label>
      <div><label class="cb"><input type="checkbox" id="e-bwauto" onchange="bwAutoToggle()"> hattaki diğer trafiğe göre kendini ayarla</label>
        <div class="hint">Açıkken sabit sınır ve çizelge devre dışı kalır. Ölçüm periyodik yapılır ve
          rclone'un hız sınırı çalışırken değiştirilir — yükleme kesilmez.</div>
        <div class="eg">Sunucunda UrBackup gibi başka bir yedekleme varsa bu modu aç:
          o yüklerken sen yavaşlar, hat boşalınca hızlanırsın.</div></div></div>
    <div id="bwauto-box" style="display:none">
      <div class="f"><label class="tip" title="İnternet bağlantının toplam YÜKLEME kapasitesi. Hesaplamanın temeli budur.">Hat kapasitesi</label>
        <div><input id="e-bwlink" placeholder="100M"><div class="errmsg" id="err-bwlink"></div>
          <div class="eg">Yükleme hızın. 100 Mbit ≈ <code>12M</code> · 1 Gbit ≈ <code>120M</code> ·
            simetrik 100/100 fiber ≈ <code>12M</code>. <b>Bit değil bayt yaz.</b></div></div></div>
      <div class="f"><label class="tip" title="Hattın bu yüzdesi her zaman diğer uygulamalara bırakılır.">Diğerlerine ayrılan</label>
        <div><input type="number" min="0" max="95" id="e-bwres"><div class="errmsg" id="err-bwres"></div>
          <div class="eg">%30 iyi bir başlangıç. Yükseltirsen daha nazik, düşürürsen daha hızlı olursun.</div></div></div>
      <div class="f"><label class="tip" title="Hat çok meşgulken bile bu hızın altına inilmez.">Alt sınır</label>
        <div><input id="e-bwmin" placeholder="1M"><div class="errmsg" id="err-bwmin"></div>
          <div class="eg">Yedeğin hiç ilerlememesini engeller.</div></div></div>
      <div class="f"><label class="tip" title="Hat boşken bile bu hızın üstüne çıkılmaz. Boşsa yukarıdaki sabit sınır tavan olur.">Üst sınır</label>
        <div><input id="e-bwmax" placeholder="(boş = sabit sınır)"><div class="errmsg" id="err-bwmax"></div></div></div>
      <div class="f"><label class="tip" title="Trafiğin ölçüleceği ağ arayüzü. Proxmox'ta köprü yerine fiziksel/bond arayüzü seçmek VM ve CT trafiğini de kapsar.">Ağ arayüzü</label>
        <div><select id="e-bwif"></select>
          <div class="hint" id="e-bwifhint"></div></div></div>
      <div class="f"><label class="tip" title="Ölçüm ve ayar sıklığı.">Ölçüm aralığı (sn)</label>
        <div><input type="number" min="2" max="3600" id="e-bwint"><div class="errmsg" id="err-bwint"></div></div></div>
      <div class="f"><label class="tip" title="Ölçümü yumuşatır. Düşük değer daha sakin, yüksek değer daha çevik ama salınıma yatkın.">Yumuşatma (0-1)</label>
        <div><input type="number" min="0.05" max="1" step="0.05" id="e-bwsm"><div class="errmsg" id="err-bwsm"></div>
          <div class="eg"><b>0.4</b> dengeli. Hızın sürekli inip çıkıyorsa düşür.</div></div></div>
      <div class="f"><label class="tip" title="Hesaplanan yeni sınır mevcuttan bu yüzdeden az farklıysa uygulanmaz.">Değişim eşiği (%)</label>
        <div><input type="number" min="1" max="90" id="e-bwstep"><div class="errmsg" id="err-bwstep"></div>
          <div class="eg">Gereksiz ayar yapılmasını ve salınımı engeller. <b>25</b> iyi bir değer.</div></div></div>
    </div>
    <div class="f"><label class="tip" title="Aynı anda kaç dosya yüklensin. Bellek kullanımı: parça boyutu × bu sayı.">Eşzamanlı transfer</label>
      <div><input type="number" min="1" id="e-tr"><div class="errmsg" id="err-tr"></div></div></div>
    <div class="f"><label class="tip" title="Karşılaştırma işçisi sayısı. Yüklemeyi değil, 'bu dosya zaten var mı' kontrolünü hızlandırır.">Checkers</label>
      <div><input type="number" min="1" id="e-ck"><div class="errmsg" id="err-ck"></div></div></div>
    <div class="f"><label class="tip" title="rclone'un Drive'a yüklerken kullandığı parça boyutu. Doğrudan RAM kullanır.">Drive parça boyutu</label>
      <div><input id="e-chunk" placeholder="64M"><div class="errmsg" id="err-chunk"></div>
        <div class="eg" id="e-ram">RAM ≈ parça × transfer</div></div></div>
    <div class="f"><label class="tip" title="Ham rclone argümanları. Buradaki her şey komut satırına eklenir.">Ek rclone argümanı</label>
      <div><input id="e-extra" placeholder="--tpslimit 5">
        <div class="eg">Örnek: <code>--exclude *.log</code> log dosyalarını yükleme · <code>--tpslimit 5</code> API hızını kıs</div></div></div>
  </fieldset>
</div>
<div class="wstep" data-step="7" style="display:none">
  <div class="wbaslik"><b>7. Bildirim</b> <span class="small">Kim, ne zaman haberdar olsun</span></div>
  <fieldset><legend>Bildirim</legend>
    <div class="f"><label class="tip" title="Mailin hangi hesaptan gönderileceği. Mail düğmesinden yeni profil ekleyebilirsin.">Gönderen profili</label>
      <div><div class="inline"><select id="e-smtp" style="flex:1"></select>
        <button class="sm" onclick="openSmtp()">✉ Yönet</button></div>
        <div class="hint" id="e-smtphint"></div></div></div>
    <div class="f"><label class="tip" title="Bildirimlerin gideceği adres. Virgülle birden fazla yazabilirsin.">Alıcı</label>
      <div><input id="e-mail" placeholder="sen@gmail.com"><div class="errmsg" id="err-mail"></div>
        <div class="eg">Birden fazla: <code>sen@gmail.com, ekip@firma.com</code></div></div></div>
    <div class="f"><label class="tip" title="Hangi sonuçta mail gelsin. Bağımsız seçilir.">Ne zaman mail</label>
      <div><div class="cbrow">
        <label class="cb"><input type="checkbox" id="e-nsuc"> ✔ Başarılı olunca</label>
        <label class="cb"><input type="checkbox" id="e-nerr"> ✖ Hata olunca</label>
        <label class="cb"><input type="checkbox" id="e-nskip"> ⏸ Atlanınca</label></div>
        <div class="hint">Her gün mail istemiyorsan sadece "Hata" ve "Atlandı" bırak; haftalık rapor genel durumu zaten özetler.</div></div></div>
  </fieldset>
  <fieldset><legend>Haftalık rapor</legend>
    <div class="f"><label class="tip" title="Haftada bir, son dönemin özetini ve uyarıları mail atar.">Rapor gönder</label>
      <div><label class="cb"><input type="checkbox" id="e-wr"> haftalık özet raporu gönder</label>
        <div class="hint">Rapor içinde: çalışma sayıları, yüklenen/silinen dosyalar, kota, <b>misafir bazında son yedek tarihi</b> ve uyarılar.</div></div></div>
    <div class="f"><label class="tip" title="Raporun gönderileceği gün.">Gün</label>
      <div><select id="e-rday"></select></div></div>
    <div class="f"><label class="tip" title="Raporun gönderileceği saat.">Saat</label>
      <div><input id="e-rat" placeholder="09:00"><div class="errmsg" id="err-rat"></div></div></div>
    <div class="f"><label class="tip" title="Rapor kaç günlük dönemi kapsasın.">Dönem (gün)</label>
      <div><input type="number" min="1" id="e-rdays"><div class="errmsg" id="err-rdays"></div></div></div>
    <div class="f"><label class="tip" title="Bu kadar gündür başarılı yedek yoksa raporda uyarı çıkar.">Uyarı eşiği (gün)</label>
      <div><input type="number" min="0" id="e-rstale"><div class="errmsg" id="err-rstale"></div>
        <div class="eg">Günlük yedekte <b>2</b> uygun. Haftalık planda <b>8</b> yap, yoksa boşuna uyarır.</div></div></div>
    <div class="f"><label class="tip" title="Kota bu yüzdeyi aşarsa raporda uyarı çıkar.">Kota uyarısı (%)</label>
      <div><input type="number" min="0" max="100" id="e-rquota"><div class="errmsg" id="err-rquota"></div></div></div>
    <div class="f"><label class="tip" title="Boş bırakırsan yukarıdaki alıcıya gider.">Rapor alıcısı</label>
      <div><input id="e-rmail" placeholder="(boş = yukarıdaki alıcı)"><div class="errmsg" id="err-rmail"></div></div></div>
  </fieldset>
</div>
<div class="wstep" data-step="8" style="display:none">
  <div class="wbaslik"><b>8. Özet</b> <span class="small">Kaydetmeden önce kontrol et</span></div>
  <fieldset><legend>Özet</legend>
    <div id="w-ozet"></div>
  </fieldset>
</div>
  <div class="mbtns">
    <button onclick="closeM('m-edit')">Vazgeç</button>
    <span style="flex:1"></span>
    <button id="w-geri" onclick="wAdim(-1)" style="display:none">‹ Geri</button>
    <button id="w-ileri" class="primary" onclick="wAdim(1)" style="display:none">İleri ›</button>
    <button id="w-kaydet" class="primary" onclick="savePlan()">Kaydet</button>
  </div>
</div></div>

<div class="mask" id="m-browse"><div class="modal" style="max-width:620px">
  <h2>📁 Yedek klasörünü seç</h2>
  <div class="small">Proxmox host üzerindeki klasörler. Parantez içi: tanınan vzdump dosyası sayısı.</div>
  <div id="b-stor" class="btns" style="margin-top:10px"></div>
  <div class="crumb" id="b-path" style="margin-top:10px"></div>
  <div class="dirlist" id="b-list"></div>
  <div class="mbtns"><button onclick="closeM('m-browse')">Vazgeç</button>
    <button class="primary" onclick="pickHere()">Bu klasörü seç</button></div>
</div></div>

<div class="mask" id="m-acct"><div class="modal">
  <h2>👤 Google hesapları</h2>
  <div class="small">Her plan bu hesaplardan birine yazar. Başkasının hesabını da ekleyebilirsin.</div>
  <div id="a-list" style="margin-top:12px"></div>
  <div id="hesap-ekle-yuvasi"></div>
  <div class="mbtns"><button onclick="closeM('m-acct')">Kapat</button></div>
</div></div>

<div class="mask" id="m-smtp"><div class="modal">
  <h2>✉ Mail gönderici profilleri</h2>
  <div class="small">Her plan istediği profilden mail atar. Farklı hesaplar, farklı sunucular olabilir.</div>
  <div id="s-list" style="margin-top:12px"></div>
  <fieldset><legend id="s-formtitle">Yeni profil</legend>
    <input type="hidden" id="s-id">
    <div class="f"><label class="tip" title="Hazır sağlayıcı seçersen sunucu, port ve güvenlik otomatik dolar.">Sağlayıcı şablonu</label>
      <div><select id="s-preset" onchange="smtpPreset()">
        <option value="">— seç —</option>
        <option value="gmail">Gmail</option>
        <option value="outlook">Outlook / Microsoft 365</option>
        <option value="yandex">Yandex</option>
        <option value="yahoo">Yahoo</option>
        <option value="custom">Özel (elle gir)</option>
      </select><div class="hint" id="s-presethint"></div></div></div>
    <div class="f"><label class="tip" title="Profilin görünen adı. Plan formunda bu ad çıkar.">Profil adı</label>
      <div><input id="s-name" placeholder="Gmail (kişisel)"><div class="errmsg" id="err-sname"></div></div></div>
    <div class="f"><label class="tip" title="SMTP sunucu adresi.">Sunucu</label>
      <div><input id="s-host" placeholder="smtp.gmail.com"><div class="errmsg" id="err-shost"></div></div></div>
    <div class="f"><label class="tip" title="STARTTLS genelde 587, SSL genelde 465.">Port</label>
      <div><input type="number" id="s-port" min="1" max="65535"><div class="errmsg" id="err-sport"></div></div></div>
    <div class="f"><label class="tip" title="Şifreleme yöntemi. Sağlayıcı şablonu bunu da doldurur.">Güvenlik</label>
      <div><select id="s-sec">
        <option value="starttls">STARTTLS (587)</option>
        <option value="ssl">SSL/TLS (465)</option>
        <option value="none">Yok (şifresiz)</option></select></div></div>
    <div class="f"><label class="tip" title="SMTP kullanıcı adı, genelde tam e-posta adresi.">Kullanıcı</label>
      <div><input id="s-user" placeholder="adresin@gmail.com"><div class="errmsg" id="err-suser"></div></div></div>
    <div class="f"><label class="tip" title="Düzenlerken boş bırakırsan mevcut şifre korunur.">Şifre</label>
      <div><input type="password" id="s-pass" placeholder="(düzenlemede boş = değişmesin)">
        <div class="hint" id="s-passhint">Gmail ve Yahoo için hesap şifresi <b>çalışmaz</b>, uygulama şifresi üret.</div></div></div>
    <div class="f"><label class="tip" title="Mailde görünecek gönderen adresi. Boşsa kullanıcı adı kullanılır.">Gönderen</label>
      <div><input id="s-from" placeholder="adresin@gmail.com"><div class="errmsg" id="err-sfrom"></div></div></div>
    <div class="btns"><button class="primary" onclick="smtpSave()">Kaydet</button>
      <button onclick="smtpClear()">Formu temizle</button></div>
  </fieldset>
  <div class="mbtns"><button onclick="closeM('m-smtp')">Kapat</button></div>
</div></div>

<div class="mask" id="m-set"><div class="modal">
  <h2>⚙ Ayarlar</h2><div class="small">Tüm planlar için ortak.</div>
  <div class="btns" style="margin-top:12px">
    <button onclick="openAccounts()" title="Google hesaplarını yönet: kota, bağlantı testi, silme">👤 Google hesapları</button>
    <button onclick="openSmtp()" title="Mail gönderici profilleri">✉ Mail profilleri</button>
  </div>
  <fieldset><legend>Web arayüzü</legend>
    <div class="f"><label class="tip" title="Arayüzün dinleyeceği adres. 127.0.0.1 yaparsan sadece SSH tüneli/ters vekil üzerinden erişilir.">Dinlenen adres</label>
      <div><input id="g-bind"><div class="errmsg" id="err-gbind"></div>
        <div class="eg"><code>0.0.0.0</code> her yerden · <code>127.0.0.1</code> sadece yerel (nginx/SSH tüneli ile)</div></div></div>
    <div class="f"><label>Port</label><div><input type="number" id="g-port" min="1" max="65535"><div class="errmsg" id="err-gport"></div></div></div>
    <div class="f"><label>Kullanıcı</label><div><input id="g-user"><div class="errmsg" id="err-guser"></div></div></div>
    <div class="f"><label class="tip" title="Boş bırakırsan mevcut şifre değişmez.">Yeni şifre</label>
      <div><input type="password" id="g-pass" placeholder="boş = değişmesin"></div></div>
    <div class="f"><label>Tazeleme (sn)</label><div><input type="number" min="1" id="g-refresh"><div class="errmsg" id="err-grefresh"></div></div></div>
    <div class="hint">Adres veya port değişirse servisi yeniden başlat: <code>systemctl restart pve-gdrive-ui</code></div>
  </fieldset>
  <fieldset><legend>HTTPS</legend>
    <div id="g-tlsdurum" class="hint" style="margin-bottom:10px"></div>
    <div class="f"><label class="tip" title="Sertifika dosyası. Boş bırakırsan arayüz düz HTTP çalışır.">Sertifika</label>
      <div><input id="g-cert" placeholder="/etc/pve/local/pve-ssl.pem">
        <div class="eg">Proxmox'un kendi sertifikası: <code>/etc/pve/local/pve-ssl.pem</code> —
          tarayıcı uyarısı Proxmox arayüzüyle aynı olur.</div></div></div>
    <div class="f"><label class="tip" title="Sertifikanın özel anahtarı.">Anahtar</label>
      <div><input id="g-key" placeholder="/etc/pve/local/pve-ssl.key"></div></div>
    <div class="f"><label class="tip" title="Çerezin yalnızca HTTPS üzerinden gönderilmesi. TLS açıkken zaten zorunlu tutulur.">Güvenli çerez</label>
      <div><label class="cb"><input type="checkbox" id="g-cookiesec"> çerezi sadece HTTPS'te gönder</label></div></div>
    <div class="hint">Sertifika yüklenemezse servis <b>düz HTTP ile ayakta kalır</b> ve log'a sebep yazar —
      hatalı yol yüzünden arayüze erişimi kaybetmezsin. Değişiklik sonrası servisi yeniden başlat.</div>
  </fieldset>
  <fieldset><legend>Güncelleme</legend>
    <div id="g-guncel" class="hint" style="margin-bottom:10px"></div>
    <div class="f"><label class="tip" title="Günde bir kez yeni sürüm var mı diye bakar.">Kontrol et</label>
      <div><label class="cb"><input type="checkbox" id="g-upcheck"> günlük güncelleme kontrolü</label></div></div>
    <div class="f"><label class="tip" title="Yeni sürüm bulununca kendiliğinden kurar. Kapalıyken sadece bildirir.">Otomatik kur</label>
      <div><label class="cb"><input type="checkbox" id="g-upauto"> bulunca kendiliğinden kur</label>
        <div class="hint">Kurulum <b>yalnızca program dosyasını</b> değiştirir; planların ve ayarların
          korunur, ikisinin de yedeği alınır. Çalışan bir yedek varken güncelleme yapılmaz.</div></div></div>
    <div class="f"><label class="tip" title="Yeni sürümün indirileceği adres.">Kaynak</label>
      <div><input id="g-upurl"></div></div>
    <div class="btns" style="margin-top:8px">
      <button class="sm" onclick="upKontrol()">↻ Şimdi kontrol et</button>
      <button class="sm primary" id="g-upbtn" onclick="upKur()" style="display:none">⬇ Güncellemeyi kur</button>
      <button class="sm warn" onclick="upGeri()">↶ Önceki sürüme dön</button>
    </div>
  </fieldset>
  <fieldset><legend>Erişim kısıtlaması</legend>
    <div class="f"><label class="tip" title="Arayüze yalnızca bu ağlardan erişilebilir. Boş bırakırsan kısıtlama olmaz.">İzinli ağlar</label>
      <div><input id="g-nets" placeholder="10.212.134.0/24">
        <div class="errmsg" id="err-nets"></div>
        <div class="eg">Virgülle ayır. Örnek: <code>10.212.134.0/24</code> (VPN ağın) ·
          <code>192.168.2.0/24, 127.0.0.1/32</code></div>
        <div class="hint">Firewall kurmaya gerek yok — kontrol uygulamanın içinde. <b>SSH ve Proxmox
          arayüzü bu ayardan etkilenmez</b>, yanlış yazarsan config dosyasından geri alabilirsin.
          Boş liste = herkese açık.</div></div></div>
  </fieldset>
  <fieldset><legend>Gelişmiş</legend>
    <div class="f"><label class="tip" title="Klasör seçici yalnızca bu köklerin altını gösterir.">Gezinme kökleri</label>
      <div><input id="g-roots"><div class="hint">Virgülle ayır. Örnek: <code>/var/lib/vz, /mnt/pve, /mnt</code></div></div></div>
    <div class="f"><label class="tip" title="Dosya adı deseni. Grup 1=set, 2=tip, 3=id, 4=tarih.">Dosya adı kalıbı</label>
      <div><input id="g-re"><div class="errmsg" id="err-gre"></div>
        <div class="hint">Regex. Varsayılan vzdump adlarını tanır — değiştirmen normalde gerekmez.</div></div></div>
    <div class="f"><label class="tip" title="Plan başına saklanacak çalışma geçmişi kaydı.">Geçmiş kaydı</label>
      <div><input type="number" min="1" id="g-hist"><div class="errmsg" id="err-ghist"></div></div></div>
    <div class="f"><label class="tip" title="Arayüzde gösterilecek log satırı sayısı.">Log satırı</label>
      <div><input type="number" min="10" id="g-logn"><div class="errmsg" id="err-glogn"></div></div></div>
    <div class="f"><label class="tip" title="rclone çıktısından bellekte tutulan son satır sayısı.">rclone tampon satırı</label>
      <div><input type="number" min="1" id="g-tail"><div class="errmsg" id="err-gtail"></div></div></div>
    <div class="f"><label class="tip" title="state.json'a yazılacak azami yedek/çöp satırı. Toplamlar tam kalır.">Durum satır sınırı</label>
      <div><input type="number" min="1" id="g-rows"><div class="errmsg" id="err-grows"></div></div></div>
    <div class="f"><label class="tip" title="Log dosyası bu boyutu aşınca döndürülür.">Log boyutu (MB)</label>
      <div><input type="number" min="0" step="0.5" id="g-logmb"><div class="errmsg" id="err-glogmb"></div></div></div>
    <div class="f"><label class="tip" title="Saklanacak eski log dosyası sayısı.">Saklanan log</label>
      <div><input type="number" min="1" id="g-logkeep"><div class="errmsg" id="err-glogkeep"></div></div></div>
    <div class="f"><label class="tip" title="0 = sınırsız. Uzun yüklemelerde 0 bırak.">rclone zaman aşımı (dk)</label>
      <div><input type="number" min="0" id="g-tmo"><div class="errmsg" id="err-gtmo"></div></div></div>
    <div class="f"><label class="tip" title="Kapsamlı silme yetmezse hesap geneli cleanup çalışsın mı.">Hesap geneli cleanup</label>
      <div><label class="cb"><input type="checkbox" id="g-cleanup"> son çare olarak rclone cleanup çalıştır</label>
        <div class="hint">⚠ <code>rclone cleanup</code> yol argümanı almaz, <b>Drive'daki tüm çöpü</b> siler.
          Hesap izni "sadece kendi dosyaları" ise etkisi bu araca ait dosyalarla sınırlı kalır.</div></div></div>
  </fieldset>
  <div class="mbtns"><button onclick="closeM('m-set')">Vazgeç</button>
    <button class="primary" onclick="saveSettings()">Kaydet</button></div>
</div></div>

<div id="hesap-ekle-panel" style="display:none">
  <fieldset><legend>Yeni Google hesabı yetkilendir</legend>
    <div class="f"><label class="tip" title="Plan hedefinde görünecek kısa ad.">Hesap adı</label>
      <div><input id="a-name" placeholder="ortak-hesap"><div class="errmsg" id="err-aname"></div>
        <div class="eg">Sadece harf, rakam, <code>-</code> ve <code>_</code>. Örnek: <code>kisisel</code>, <code>ortak-hesap</code></div></div></div>
    <div class="tabs" style="margin:4px 0 10px">
      <button id="a-tab1" class="on" onclick="acctTab(1)">Tarayıcıyla yetkilendir</button>
      <button id="a-tab2" onclick="acctTab(2)">Hazır jetonu yapıştır</button>
    </div>
    <div id="a-m1">
      <div class="hint">Google onaydan sonra tarayıcıyı <b>senin bilgisayarındaki</b> 127.0.0.1 adresine yönlendirir.
        Bu yüzden önce aşağıdaki komutu kendi bilgisayarında bir terminalde çalıştır ve açık bırak, sonra "Başlat" de.</div>
      <div class="f" style="margin-top:8px"><label>Tünel komutu</label>
        <div><input id="a-tunnel" readonly onclick="this.select()">
          <div class="hint">Tıklayınca seçilir, kopyala-yapıştır yap.</div></div></div>
      <div class="btns"><button class="primary" onclick="authStart()">▶ Başlat</button>
        <button onclick="authCancel()">Vazgeç</button></div>
      <div id="a-authbox" style="display:none;margin-top:10px">
        <div class="hint">Bu adresi tarayıcında aç ve <b>hedef Google hesabıyla</b> giriş yap:</div>
        <div class="crumb" id="a-url" style="margin:6px 0"></div>
        <div class="small" id="a-wait">jeton bekleniyor…</div>
      </div>
    </div>
    <div id="a-m2" style="display:none">
      <div class="hint">Tarayıcısı olan herhangi bir bilgisayarda
        <code>rclone authorize "drive" --drive-scope drive.file</code> çalıştır, çıkan JSON'u yapıştır.
        Hesap sahibi bu adımı kendi bilgisayarında yapıp sana gönderebilir — şifresini paylaşması gerekmez.</div>
      <div class="f" style="margin-top:8px"><label>Jeton (JSON)</label>
        <div><textarea id="a-token" rows="4" placeholder='{"access_token":"...","refresh_token":"...","expiry":"..."}'></textarea>
          <div class="errmsg" id="err-atoken"></div></div></div>
      <div class="btns"><button class="primary" onclick="acctPaste()">Ekle</button></div>
    </div>
  </fieldset>
</div>
<div class="flash" id="flash"></div>

<script>
"use strict";
/** Arka ucun /api/status ile dondurdugu veri yapilari. */
/** pve-gdrive-backup web arayuzu. tsc ile derlenip pve_gdrive.py icine gomulur. */
let S = null;
let sel = null;
let cur = "";
let REM = [];
let SMTP = [];
let EDIT = null;
let authTimer = 0;
let refTimer = 0;
let LOGSRC = "all";
let dirty = false;
let running = 0;
const WD = ["Pzt", "Sal", "Çar", "Per", "Cum", "Cmt", "Paz"];
function el(id) {
    const e = document.getElementById(id);
    if (!e)
        throw new Error("eleman bulunamadi: " + id);
    return e;
}
function fld(id) { return el(id); }
function val(id) { return fld(id).value; }
function setVal(id, v) { fld(id).value = v === null || v === undefined ? "" : String(v); }
function chk(id) { return el(id).checked; }
function setChk(id, v) { el(id).checked = !!v; }
function setHtml(id, h) { el(id).innerHTML = h; }
function setTxt(id, t) { el(id).textContent = t; }
function esc(s) {
    return String(s === null || s === undefined ? "" : s)
        .replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
}
function hb(n) {
    let v = Number(n) || 0;
    const u = ["B", "KB", "MB", "GB", "TB"];
    for (let i = 0; i < u.length; i++) {
        if (v < 1024)
            return v.toFixed(1) + " " + u[i];
        v /= 1024;
    }
    return v.toFixed(1) + " PB";
}
function fmtDur(sec) {
    const s = Math.max(0, Math.round(sec));
    const h = Math.floor(s / 3600), m = Math.floor((s % 3600) / 60), r = s % 60;
    return (h ? h + "s " : "") + (h || m ? m + "dk " : "") + r + "sn";
}
function flash(m, ok) {
    const f = el("flash");
    f.textContent = m;
    f.style.background = ok ? "#238636" : "#b03636";
    f.classList.add("show");
    window.setTimeout(() => f.classList.remove("show"), 3200);
}
function closeM(id) { el(id).classList.remove("show"); if (id === "m-edit")
    dirty = false; }
function openM(id) { el(id).classList.add("show"); }
/* ---------- API ---------- */
function csrf() { return S && S.csrf ? S.csrf : ""; }
async function api(url, opt) {
    const o = opt || {};
    o.headers = Object.assign({ "X-CSRF-Token": csrf() }, o.headers || {});
    const r = await fetch(url, o);
    if (r.status === 401) {
        location.reload();
        return { ok: false, msg: "oturum bitti" };
    }
    const t = await r.text();
    try {
        return JSON.parse(t);
    }
    catch {
        return { ok: false, msg: t.slice(0, 200) };
    }
}
/* ---------- F5 / yenileme korumasi ---------- */
window.addEventListener("beforeunload", (e) => {
    if (dirty) {
        e.preventDefault();
        e.returnValue = "";
        return "";
    }
    return undefined;
});
function markDirty() { dirty = true; }
try {
    sel = localStorage.getItem("pg_sel") || null;
    LOGSRC = localStorage.getItem("pg_log") || "all";
}
catch { /* localStorage kapali olabilir */ }
function remember() {
    try {
        localStorage.setItem("pg_sel", sel || "");
        localStorage.setItem("pg_log", LOGSRC);
    }
    catch { /* yok say */ }
}
/* ---------- dogrulama ---------- */
const RX = {
    time: /^([01]?\d|2[0-3]):[0-5]\d$/,
    folder: /^[^:*?"<>|]*$/,
    acct: /^[A-Za-z0-9_-]+$/,
    bw: /^(off|\d+(\.\d+)?[KMGT]?)$/i,
    bwsched: /^([01]?\d|2[0-3]):[0-5]\d,(off|\d+(\.\d+)?[KMGT]?)$/i,
    chunk: /^\d+(\.\d+)?[KMG]$/i,
    mail: /^[^@\s,;]+@[^@\s,;]+\.[^@\s,;]{2,}$/,
    host: /^[A-Za-z0-9]([A-Za-z0-9.-]*[A-Za-z0-9])?$/,
    ip: /^(\d{1,3}\.){3}\d{1,3}$|^localhost$/,
};
function errBox(id) {
    // Kutu adlari iki bicimde: e-name -> err-name, a-name -> err-aname. Ikisini de dene.
    return document.getElementById("err-" + id.replace("-", ""))
        || document.getElementById("err-" + id.replace(/^[a-z]-/, ""));
}
function bad(id, msg) {
    fld(id).classList.add("bad");
    const e = errBox(id);
    if (e) {
        e.textContent = msg;
        e.classList.add("show");
    }
    return false;
}
function good(id) {
    fld(id).classList.remove("bad");
    const e = errBox(id);
    if (e)
        e.classList.remove("show");
    return true;
}
function vTxt(id, msg) { return val(id).trim() ? good(id) : bad(id, msg); }
function vRx(id, rx, msg, optional) {
    const v = val(id).trim();
    if (!v)
        return optional ? good(id) : bad(id, msg);
    return rx.test(v) ? good(id) : bad(id, msg);
}
function vNum(id, min, max, msg) {
    const v = val(id).trim();
    if (v === "" || isNaN(Number(v)))
        return bad(id, msg);
    const n = Number(v);
    if (n < min || (max !== null && n > max))
        return bad(id, msg);
    return good(id);
}
/** "08:00,2M 19:00,off" biciminde saatlik hiz cizelgesini dogrular. */
function vBwSched(id) {
    const v = val(id).trim();
    if (!v)
        return good(id);
    const kotu = v.split(/\s+/).filter(Boolean).filter((x) => !RX.bwsched.test(x));
    if (kotu.length)
        return bad(id, "'" + kotu[0] + "' hatalı — SS:DD,hız olmalı (ör. 08:00,2M)");
    return good(id);
}
function bwPreset(v) { setVal("e-bwsch", v); good("e-bwsch"); markDirty(); }
/** "30M" -> bayt/sn. Alt/ust sinir karsilastirmasi icin. */
function bwBytes(t) {
    const s = String(t || "").trim();
    if (!s || s.toLowerCase() === "off")
        return 0;
    const m = s.match(/^([\d.]+)\s*([BKMGT]?)$/i);
    if (!m)
        return 0;
    const carp = { "": 1, B: 1, K: 1024, M: 1048576, G: 1073741824, T: 1099511627776 };
    return parseFloat(m[1]) * (carp[m[2].toUpperCase()] || 1);
}
function bwAutoToggle() {
    const acik = chk("e-bwauto");
    el("bwauto-box").style.display = acik ? "" : "none";
    fld("e-bw").disabled = acik;
    fld("e-bwsch").disabled = acik;
    markDirty();
}
async function loadIfaces(secili) {
    try {
        const j = await api("/api/ifaces");
        const list = j.ifaces || [];
        setHtml("e-bwif", '<option value="">(otomatik: ' + esc(j.default || "-") + ")</option>"
            + list.map((i) => '<option value="' + esc(i.name) + '">' + esc(i.name)
                + " — " + hb(i.tx) + " gönderilmiş" + (i.default ? " (varsayılan rota)" : "") + "</option>").join(""));
        setVal("e-bwif", secili);
        setTxt("e-bwifhint", "Proxmox'ta köprü (vmbr0) yalnızca host trafiğini görebilir; "
            + "VM ve CT trafiğini de saymak için fiziksel veya bond arayüzünü seç.");
    }
    catch { /* yok say */ }
}
function vMails(id, optional) {
    const v = val(id).trim();
    if (!v)
        return optional ? good(id) : bad(id, "alıcı adresi gerekli");
    const kotu = v.split(",").map((x) => x.trim()).filter(Boolean).filter((x) => !RX.mail.test(x));
    return kotu.length ? bad(id, "geçersiz adres: " + kotu[0]) : good(id);
}
/* ---------- plan kartlari ---------- */
function pillOf(p) {
    const s = p.state || {};
    if (p.running)
        return '<span class="pill run">● ÇALIŞIYOR</span>';
    if (!p.enabled)
        return '<span class="pill off">KAPALI</span>';
    if (s.status === "basarili")
        return '<span class="pill ok">✔ BAŞARILI</span>';
    if (s.status === "HATA")
        return '<span class="pill err">✖ HATA</span>';
    if (s.status === "atlandi")
        return '<span class="pill run">⏸ ATLANDI</span>';
    return '<span class="pill idle">' + esc(s.status || "—").toUpperCase() + "</span>";
}
function progOf(p) {
    const g = p.weekdays && p.weekdays.length
        ? p.weekdays.map((d) => WD[d - 1]).join(",") : "her gün";
    return g + " " + esc(p.run_at);
}
function etaSec(t) {
    const m = String(t).match(/(?:(\d+)h)?(?:(\d+)m)?(?:([\d.]+)s)?/);
    if (!m)
        return null;
    const v = (Number(m[1]) || 0) * 3600 + (Number(m[2]) || 0) * 60 + (Number(m[3]) || 0);
    return v || null;
}
function progBox(p) {
    if (!p.running)
        return "";
    const g = p.progress;
    const lbl = (g && g.phase_label) || "Çalışıyor";
    const pct = g && typeof g.pct === "number" ? g.pct : null;
    const st = g && g.started ? new Date(g.started * 1000) : null;
    const gecen = g && g.started ? Date.now() / 1000 - g.started : 0;
    let h = '<div class="card" style="border-color:#4d3d1a;background:#1a1710;margin-bottom:10px">'
        + '<div style="display:flex;justify-content:space-between;gap:8px;flex-wrap:wrap">'
        + "<b>● " + esc(lbl) + '</b><span class="small">' + (pct !== null ? pct + "%" : "") + "</span></div>"
        + '<div class="bar" style="background:#2b2415"><i style="width:' + (pct !== null ? pct : 5)
        + '%;background:linear-gradient(90deg,#d29922,#3fb950)"></i></div>'
        + '<table style="margin-top:8px"><tbody>'
        + '<tr><td class="small">Başlangıç</td><td class="r">' + (st ? st.toLocaleTimeString("tr-TR") : "—")
        + '</td><td class="small">Geçen</td><td class="r">' + fmtDur(gecen) + "</td></tr>";
    if (g && g.total) {
        h += '<tr><td class="small">Aktarılan</td><td class="r">'
            + esc(g.done_h || hb(g.done)) + " / " + esc(g.total_h || hb(g.total))
            + '</td><td class="small">Hız</td><td class="r">' + esc(g.speed || "—") + "</td></tr>";
        let eta = g.eta && g.eta !== "-" ? g.eta : null;
        let bitis = "—";
        if (eta) {
            const sn = etaSec(eta);
            if (sn !== null)
                bitis = new Date(Date.now() + sn * 1000).toLocaleTimeString("tr-TR");
        }
        else if ((g.speed_bps || 0) > 0 && (g.total || 0) > (g.done || 0)) {
            const sn2 = ((g.total || 0) - (g.done || 0)) / (g.speed_bps || 1);
            eta = fmtDur(sn2);
            bitis = new Date(Date.now() + sn2 * 1000).toLocaleTimeString("tr-TR");
        }
        h += '<tr><td class="small">Kalan süre</td><td class="r">' + esc(eta || "—")
            + '</td><td class="small">Tahmini bitiş</td><td class="r">' + bitis + "</td></tr>";
    }
    h += '</tbody></table><div class="hint">Bu iş sunucuda çalışıyor — sayfayı yenilesen de '
        + "kapatsan da devam eder.</div></div>";
    return h;
}
function planCard(p) {
    const s = p.state || {};
    return '<div class="plan' + (p.id === sel ? " sel" : "") + "\" onclick=\"pick('" + p.id + "')\">"
        + "<h3>" + esc(p.name) + pillOf(p) + "</h3>"
        + progBox(p)
        + '<div class="row"><span>Kaynak</span><b>' + esc(p.src_dir)
        + (p.src_exists ? ' <span class="small">(' + p.src_dumps + " dosya)</span>" : ' <span class="pill err">yok</span>')
        + "</b></div>"
        + '<div class="row"><span>Hedef</span><b>' + esc(p.remote) + "</b></div>"
        + '<div class="row"><span>Program</span><b>' + progOf(p) + "</b></div>"
        + '<div class="row"><span>Sonraki</span><b>' + esc(p.next_run || "-") + "</b></div>"
        + '<div class="row"><span>Saklama</span><b>' + p.keep_days + " gün · min " + p.keep_count
        + " set · çöp " + p.drive_trash_days + " gün</b></div>"
        + '<div class="row"><span>Son çalışma</span><b>' + esc(s.last_run || "-") + "</b></div>"
        + (p.weekly_report ? '<div class="row"><span>Haftalık rapor</span><b>' + esc(p.next_report || "-") + "</b></div>" : "")
        + (s.summary ? '<div class="small" style="margin-top:6px">' + esc(s.summary) + "</div>" : "")
        + '<div class="pbtns" onclick="event.stopPropagation()">'
        + "<button class=\"sm primary\" onclick=\"act('backup','" + p.id + "')\"" + (p.running ? " disabled" : "") + ">▶ Yedekle</button>"
        + "<button class=\"sm\" onclick=\"act('prune','" + p.id + "')\">🧹 Retention</button>"
        + "<button class=\"sm\" onclick=\"act('purgetrash','" + p.id + "')\">🗑 Çöpü Boşalt</button>"
        + "<button class=\"sm\" onclick=\"act('report','" + p.id + "')\">📊 Rapor</button>"
        + "<button class=\"sm\" onclick=\"act('testmail','" + p.id + "')\">✉ Test</button>"
        + "<button class=\"sm\" onclick=\"openEditor('" + p.id + "')\">✎ Düzenle</button>"
        + "<button class=\"sm warn\" onclick=\"delPlan('" + p.id + "')\">Sil</button></div></div>";
}
function pick(id) { sel = id; remember(); render(); }
function detail(p) {
    if (!p)
        return "";
    const s = p.state || {};
    const b = s.backups || [], t = s.trash || [], q = s.quota || {};
    const T = s.totals || { count: b.length, size: b.reduce((a, x) => a + (Number(x.size) || 0), 0) };
    const TT = s.trash_totals || { count: t.length, size: 0 };
    let age = "—";
    if (b[0] && b[0].mod) {
        const d = (Date.now() - new Date(b[0].mod).getTime()) / 3600000;
        age = d < 24 ? d.toFixed(1) + " saat" : (d / 24).toFixed(1) + " gün";
    }
    const used = Number(q.used) || 0, total = Number(q.total) || 0;
    const pct = total ? Math.min(100, (used / total) * 100) : 0;
    const cards = [
        ["Yedek dosyası", T.count], ["Toplam boyut", hb(T.size)], ["Son yedek yaşı", age],
        ["Çöpte bekleyen", TT.count], ["Saklama", p.keep_days + " gün"], ["Çöp süresi", p.drive_trash_days + " gün"]
    ];
    const h = (s.history || []).slice(0, 12);
    return '<div class="panel"><h2>' + esc(p.name) + ' <span class="small">'
        + esc(p.src_dir) + " → " + esc(p.remote) + "</span></h2>"
        + '<div class="grid">' + cards.map((c) => '<div class="card"><div class="k">' + c[0]
        + '</div><div class="v">' + c[1] + "</div></div>").join("") + "</div>"
        + '<h2>Google Drive kullanımı</h2><div class="bar"><i style="width:' + pct.toFixed(1) + '%"></i></div>'
        + '<div class="small" style="margin-top:8px">' + hb(used) + " / " + hb(total) + " (" + pct.toFixed(1)
        + "%) · çöp: " + hb(q.trashed || 0) + " · boş: " + hb(q.free || 0) + "</div></div>"
        + '<div class="cols"><div class="panel"><h2>Yedekler (Drive)</h2><table><thead><tr><th>Tarih</th>'
        + '<th>Misafir</th><th class="r">Boyut</th><th>Dosya</th></tr></thead><tbody>'
        + (b.slice(0, 40).map((x) => {
            const cl = String(x.guest).indexOf("lxc") === 0 ? "tag lxc" : "tag";
            return "<tr><td>" + esc((x.mod || "").slice(0, 19).replace("T", " ")) + '</td><td><span class="'
                + cl + '">' + esc(x.guest) + '</span></td><td class="r">' + hb(x.size)
                + '</td><td class="small">' + esc(x.name) + "</td></tr>";
        }).join("") || '<tr><td colspan=4 class="small">yedek yok</td></tr>')
        + '</tbody></table></div><div class="panel"><h2>Google çöp kutusu (' + TT.count
        + ') <span class="small">süre dolunca kalıcı silinir</span></h2>'
        + '<table><thead><tr><th>Dosya</th><th class="r">Boyut</th><th class="r">Kalan</th></tr></thead><tbody>'
        + (t.map((x) => '<tr><td class="small">' + esc(x.name) + '</td><td class="r">' + hb(x.size)
            + '</td><td class="r">' + (x.tracked ? x.remain_days + " gün" : "—") + "</td></tr>").join("")
            || '<tr><td colspan=3 class="small">boş</td></tr>')
        + "</tbody></table></div></div>"
        + '<div class="panel"><h2>Çalışma geçmişi</h2><table><thead><tr><th>Zaman</th><th>Durum</th>'
        + "<th>Tetik</th><th>Özet</th></tr></thead><tbody>"
        + (h.map((x) => "<tr><td>" + esc(x.time) + "</td><td>" + esc(x.status) + "</td><td>"
            + esc(x.trigger) + '</td><td class="small">' + esc(x.summary) + "</td></tr>").join("")
            || '<tr><td colspan=4 class="small">kayıt yok</td></tr>') + "</tbody></table></div>";
}
function render() {
    if (!S)
        return;
    const ps = S.plans || [];
    if (!sel || !ps.some((p) => p.id === sel))
        sel = ps.length ? ps[0].id : null;
    running = ps.filter((p) => p.running).length;
    const tls = S.tls;
    setHtml("tlsrozet", tls && tls.aktif
        ? '<span class="pill ok" title="Bağlantı şifreli">🔒 HTTPS</span>'
        : '<span class="pill err" title="Trafik şifresiz — yalnızca VPN içinde kullan">⚠ HTTP</span>');
    const g = S.guncelleme;
    setHtml("uprozet", g && g.yeni_var
        ? '<span class="pill run" title="Yeni sürüm var: ' + esc(g.uzak || "")
            + '" style="cursor:pointer" onclick="openSettings()">⬆ ' + esc(g.uzak || "") + " hazır</span>"
        : '<span class="small" title="Kurulu sürüm">v' + esc(S.surum || "?") + "</span>");
    setTxt("hinfo", ps.length + " plan" + (running ? " · " + running + " çalışıyor" : "")
        + (S.updated ? " · durum: " + S.updated : "") + (S.smtp_ready ? "" : " · mail profili yok"));
    setHtml("plans", ps.map(planCard).join("")
        || '<div class="card">Henüz plan yok. Sağ üstten "+ Yeni Plan" ile başla.</div>');
    hesapSerit();
    setHtml("detail", detail(ps.filter((p) => p.id === sel)[0]));
    const tabs = [["all", "Tümü"], ["system", "Sistem"]]
        .concat(ps.map((p) => [p.id, p.name]));
    setHtml("logtabs", tabs.map((t) => '<button class="' + (LOGSRC === t[0] ? "on" : "")
        + "\" onclick=\"setLog('" + t[0] + "')\">" + esc(t[1]) + "</button>").join(""));
}
function setLog(src) { LOGSRC = src; remember(); void loadLog(); }
/** Ana ekranda hesap kotalari. Dolmak uzere olan bir hesap yedegi bozar,
 *  bu yuzden plana girmeden gorunur olmali. Tiklayinca yonetim ekrani acilir. */
function hesapSerit() {
    const h = (S && S.hesaplar) || [];
    if (!h.length) {
        setHtml("hesapserit", "");
        return;
    }
    setHtml("hesapserit", h.map((x) => {
        const q = x.quota || {};
        if (!q.ok) {
            return '<div class="hesap hata" onclick="openAccounts()"><div class="ad"><b>'
                + esc(x.name) + '</b><span>hata</span></div><div class="alt">⚠ '
                + esc(q.error || "kota okunamadı") + "</div></div>";
        }
        const pct = x.pct === null || x.pct === undefined ? 0 : x.pct;
        const sinif = pct >= 90 ? " dolu" : (pct >= 75 ? " uyari" : "");
        return '<div class="hesap' + sinif + '" onclick="openAccounts()" title="Yönetmek için tıkla">'
            + '<div class="ad"><b>' + esc(x.name) + "</b><span>" + pct.toFixed(0) + "%</span></div>"
            + '<div class="mini"><i style="width:' + Math.min(100, pct) + '%"></i></div>'
            + '<div class="alt">' + hb(q.used) + " / " + hb(q.total)
            + (q.trashed ? " · çöp " + hb(q.trashed) : "") + "</div></div>";
    }).join(""));
}
async function loadLog() {
    try {
        const r = await fetch("/api/log?src=" + encodeURIComponent(LOGSRC));
        setTxt("log", await r.text());
    }
    catch { /* gecici ag hatasi */ }
    if (S)
        render();
}
async function refresh() {
    try {
        S = await api("/api/status");
    }
    catch {
        return;
    }
    if (S && S.login) {
        location.reload();
        return;
    }
    render();
    void loadLog();
    const base = ((S && S.settings && S.settings.ui_refresh_sec) || 5) * 1000;
    const iv = running ? Math.min(base, 2000) : base;
    window.clearInterval(refTimer);
    refTimer = window.setInterval(() => void refresh(), iv);
}
async function act(d, pid) {
    flash("çalışıyor…", true);
    try {
        const j = await api("/api/action?do=" + d + "&plan=" + encodeURIComponent(pid), { method: "POST" });
        flash(j.msg || "tamam", j.ok);
    }
    catch {
        flash("hata", false);
    }
    window.setTimeout(() => void refresh(), 900);
}
async function delPlan(pid) {
    if (!confirm("Plan silinsin mi? Drive'daki yedek dosyalarına dokunulmaz."))
        return;
    const j = await api("/api/plan/delete?plan=" + encodeURIComponent(pid), { method: "POST" });
    flash(j.msg || "", j.ok);
    void refresh();
}
async function logout() { await api("/logout", { method: "POST" }); location.reload(); }
/* ---------- plan sihirbazi ---------- */
const ADIMLAR = ["Plan", "Kaynak", "Hedef", "Saklama", "Zamanlama", "Aktarım", "Bildirim", "Özet"];
/** Her adimda dogrulanacak alanlar. Ozet adiminda hepsi bir kez daha kontrol edilir. */
const ADIM_ALANLARI = [
    ["e-name"], ["e-src"], ["e-acct", "e-folder"], ["e-kd", "e-kc", "e-td"],
    ["e-runat", "e-wvm", "e-mage"],
    ["e-bw", "e-bwsch", "e-tr", "e-ck", "e-chunk", "e-bwlink", "e-bwres", "e-bwmin", "e-bwmax", "e-bwint", "e-bwsm", "e-bwstep"],
    ["e-mail", "e-rmail", "e-rat", "e-rdays", "e-rstale", "e-rquota"], [],
];
let wAktif = 1;
let wSihirbaz = false;
function wGoster() {
    Array.prototype.slice.call(document.querySelectorAll(".wstep")).forEach((d) => {
        const n = Number(d.getAttribute("data-step"));
        d.style.display = !wSihirbaz || n === wAktif ? "" : "none";
    });
    el("w-adimlar").style.display = wSihirbaz ? "" : "none";
    if (wSihirbaz) {
        setHtml("w-adimlar", ADIMLAR.map((ad, i) => {
            const n = i + 1;
            const sinif = n === wAktif ? "on" : (n < wAktif ? "ok" : "");
            return '<span class="' + sinif + '">' + n + ". " + esc(ad) + "</span>";
        }).join(""));
    }
    const son = wAktif === ADIMLAR.length;
    el("w-geri").style.display = wSihirbaz && wAktif > 1 ? "" : "none";
    el("w-ileri").style.display = wSihirbaz && !son ? "" : "none";
    el("w-kaydet").style.display = !wSihirbaz || son ? "" : "none";
    if (wSihirbaz && son)
        wOzet();
}
/** Yalnizca verilen alanlari dogrular; digerlerini bozmadan birakir. */
function wAdimGecerli(adim) {
    const alanlar = ADIM_ALANLARI[adim - 1] || [];
    if (!alanlar.length)
        return true;
    const oncekiHatalar = alanlar.filter((id) => document.getElementById(id))
        .map((id) => [id, fld(id).classList.contains("bad")]);
    const tumu = validatePlan();
    if (tumu)
        return true;
    // Bu adimin alanlarindan biri hatali mi?
    const buAdimHatali = alanlar.some((id) => document.getElementById(id) && fld(id).classList.contains("bad"));
    if (!buAdimHatali) {
        // Hata baska adimda: bu adimin gorunumunu temizle, gecise izin ver
        oncekiHatalar.forEach(([id, vardi]) => { if (!vardi)
            good(id); });
        return true;
    }
    return false;
}
function wAdim(yon) {
    if (yon > 0 && !wAdimGecerli(wAktif)) {
        flash("bu adımda eksik veya hatalı alan var", false);
        return;
    }
    wAktif = Math.min(ADIMLAR.length, Math.max(1, wAktif + yon));
    wGoster();
    el("m-edit").scrollTop = 0;
}
function wSatir(baslik, deger, uyari) {
    return '<tr' + (uyari ? ' class="uyari"' : "") + "><td>" + esc(baslik) + "</td><td>"
        + esc(deger) + "</td></tr>";
}
function wOzet() {
    const wd = Array.prototype.slice.call(el("e-wd").querySelectorAll("input:checked"))
        .map((c) => WD[Number(c.value) - 1]);
    const bildirim = [chk("e-nsuc") ? "başarılı" : "", chk("e-nerr") ? "hata" : "",
        chk("e-nskip") ? "atlandı" : ""].filter(Boolean).join(", ") || "hiçbiri";
    const oto = chk("e-bwauto");
    const hiz = oto ? ("otomatik (" + val("e-bwmin") + " – " + (val("e-bwmax") || val("e-bw")) + ")")
        : (val("e-bwsch") ? "çizelge: " + val("e-bwsch") : val("e-bw"));
    let h = '<table class="ozet"><tbody>';
    h += wSatir("Plan adı", val("e-name"));
    h += wSatir("Durum", chk("e-enabled") ? "etkin" : "kapalı (zamanlayıcı atlar)", !chk("e-enabled"));
    h += wSatir("Kaynak", val("e-src"));
    h += wSatir("Hedef", (val("e-acct") || "?") + ":" + val("e-folder"));
    h += wSatir("Saklama", val("e-kd") + " gün · misafir başına en az " + val("e-kc") + " set");
    h += wSatir("Çöp süresi", val("e-td") + " gün sonra kalıcı silinir");
    h += wSatir("Program", (wd.length ? wd.join(",") : "her gün") + " saat " + val("e-runat"));
    h += wSatir("vzdump koruması", chk("e-wv")
        ? "bekle, en fazla " + val("e-wvm") + " dk" : "beklemeden çalış", !chk("e-wv"));
    h += wSatir("Hatada retention", chk("e-pof")
        ? "ÇALIŞIR — yükleme başarısızsa da siler" : "çalışmaz (güvenli)", chk("e-pof"));
    h += wSatir("Hız", hiz);
    h += wSatir("Mail", (val("e-mail") || "—") + "  ·  " + bildirim);
    h += wSatir("Haftalık rapor", chk("e-wr")
        ? WD[Number(val("e-rday")) - 1] + " " + val("e-rat") + " (" + val("e-rdays") + " günlük dönem)"
        : "kapalı");
    h += "</tbody></table>";
    const uyarilar = [];
    if (!val("e-acct"))
        uyarilar.push("Google hesabı seçilmedi — 3. adıma dön.");
    if (Number(val("e-kc")) === 0)
        uyarilar.push("Güvenlik tabanı 0: uzun süre yedeklenmeyen bir misafirin tüm yedekleri silinebilir.");
    if (chk("e-pof"))
        uyarilar.push("Hatada retention açık: yeni yedek çıkmadan eskiler silinebilir.");
    if (!val("e-mail") && (chk("e-nsuc") || chk("e-nerr") || chk("e-wr")))
        uyarilar.push("Bildirim seçili ama alıcı adresi boş.");
    if (uyarilar.length) {
        h += '<div class="hint" style="margin-top:10px;color:#ffd479">'
            + uyarilar.map((u) => "⚠ " + esc(u)).join("<br>") + "</div>";
    }
    setHtml("w-ozet", h);
}
/** Hesap ekleme paneli tek bir DOM parcasidir; sihirbaz ile modal arasinda tasinir. */
function hesapPaneliTasi(hedefId, gorunur) {
    const panel = el("hesap-ekle-panel");
    const yuva = document.getElementById(hedefId);
    if (yuva && panel.parentElement !== yuva)
        yuva.appendChild(panel);
    panel.style.display = gorunur ? "" : "none";
}
function wHesapEkle() {
    hesapPaneliTasi("w-hesap-yuvasi", true);
    acctTab(1);
    fld("a-name").focus();
}
const PRESETS = {
    gunluk: { keep_days: 14, keep_count: 3, drive_trash_days: 1, run_at: "03:00", weekdays: [],
        bwlimit: "30M", transfers: 2, checkers: 4, drive_chunk: "64M", min_age_min: 10,
        vzdump_wait_min: 60, weekly_report: true, report_day: 1, report_at: "09:00",
        report_days: 7, report_stale_days: 2, report_quota_warn: 90 },
    haftalik: { keep_days: 180, keep_count: 4, drive_trash_days: 7, run_at: "05:00", weekdays: [7],
        bwlimit: "10M", transfers: 1, checkers: 4, drive_chunk: "64M", min_age_min: 15,
        vzdump_wait_min: 120, weekly_report: true, report_day: 1, report_at: "09:00",
        report_days: 30, report_stale_days: 8, report_quota_warn: 85 },
    kritik: { keep_days: 30, keep_count: 7, drive_trash_days: 3, run_at: "02:00", weekdays: [],
        bwlimit: "50M", transfers: 3, checkers: 8, drive_chunk: "64M", min_age_min: 10,
        vzdump_wait_min: 180, weekly_report: true, report_day: 1, report_at: "08:00",
        report_days: 7, report_stale_days: 1, report_quota_warn: 80 },
    test: { keep_days: 2, keep_count: 1, drive_trash_days: 0.5, run_at: "22:00", weekdays: [],
        bwlimit: "5M", transfers: 1, checkers: 2, drive_chunk: "32M", min_age_min: 1,
        vzdump_wait_min: 5, weekly_report: false, report_day: 1, report_at: "09:00",
        report_days: 7, report_stale_days: 2, report_quota_warn: 90 },
};
function preset(k) {
    const v = PRESETS[k];
    if (!v)
        return;
    setVal("e-kd", v.keep_days);
    setVal("e-kc", v.keep_count);
    setVal("e-td", v.drive_trash_days);
    setVal("e-ck", v.checkers);
    setVal("e-chunk", v.drive_chunk);
    setVal("e-mage", v.min_age_min);
    setVal("e-wvm", v.vzdump_wait_min);
    setChk("e-wr", v.weekly_report);
    setVal("e-rday", v.report_day);
    setVal("e-rat", v.report_at);
    setVal("e-rdays", v.report_days);
    setVal("e-rstale", v.report_stale_days);
    setVal("e-rquota", v.report_quota_warn);
    Array.prototype.slice.call(el("e-wd").querySelectorAll("input")).forEach((c) => {
        c.checked = v.weekdays.indexOf(Number(c.value)) >= 0;
    });
    setVal("e-bwsch", "");
    good("e-bwsch");
    ramHint();
    markDirty();
    flash("senaryo yüklendi — kaydetmeden uygulanmaz", true);
}
function ramHint() {
    const c = String(val("e-chunk") || "").match(/^(\d+(?:\.\d+)?)([KMG])$/i);
    const t = Number(val("e-tr")) || 1;
    if (!c) {
        setTxt("e-ram", "RAM ≈ parça × transfer");
        return;
    }
    const carp = { K: 1 / 1024, M: 1, G: 1024 };
    const mb = parseFloat(c[1]) * carp[c[2].toUpperCase()];
    setTxt("e-ram", "Tahmini rclone RAM kullanımı: " + Math.round(mb * t) + " MB ("
        + c[0] + " × " + t + " transfer)");
}
function openEditor(pid) {
    const p = pid && S ? S.plans.filter((x) => x.id === pid)[0] : undefined;
    EDIT = pid || null;
    dirty = false;
    wSihirbaz = !pid; // yeni plan: sihirbaz, mevcut plan: tek sayfa form
    wAktif = 1;
    setTxt("ed-title", p ? "Plan: " + p.name : "🧭 Yeni plan sihirbazı");
    setTxt("ed-alt", p
        ? "Tüm ayarlar tek sayfada. Alan adlarının üstüne gelince açıklama çıkar."
        : "Adım adım ilerle. Hiçbir şey kaydedilmez, son adımda onaylarsın.");
    const d = {
        name: "", enabled: true, src_dir: "/var/lib/vz/dump", remote: "gdrive:proxmox-yedek",
        keep_days: 14, keep_count: 3, drive_trash_days: 1, run_at: "03:00", weekdays: [],
        bwlimit: "30M", bwlimit_schedule: "", bwlimit_upload_only: true,
        transfers: 2, checkers: 4, drive_chunk: "64M", rclone_extra: [],
        mail_to: "", smtp_profile: "", notify_success: true, notify_failure: true, notify_skipped: false,
        wait_for_vzdump: true, vzdump_wait_min: 60, min_age_min: 10,
        skip_patterns: ["*.dat", "*.tmp", "*.part"], prune_on_failure: false, weekly_report: true,
        report_day: 1, report_at: "09:00", report_days: 7, report_stale_days: 2,
        report_quota_warn: 90, report_mail_to: "",
    };
    const v = (p || d);
    // Ortak alanlar tablodan doldurulur (bkz. alanlar.ts); asagidakiler ozel durumlar.
    alanlariDoldur(v);
    const rp = String(v.remote || "gdrive:proxmox-yedek").split(":");
    setVal("e-folder", rp.slice(1).join(":"));
    void loadIfaces(v.bw_auto_iface || "");
    bwAutoToggle();
    setHtml("e-rday", WD.map((n, i) => '<option value="' + (i + 1) + '">' + n + "</option>").join(""));
    setVal("e-rday", v.report_day || 1);
    setHtml("e-wd", WD.map((n, i) => '<label><input type="checkbox" value="' + (i + 1) + '"'
        + ((v.weekdays || []).indexOf(i + 1) >= 0 ? " checked" : "") + ">" + n + "</label>").join(""));
    setTxt("e-srchint", p ? (p.src_exists ? p.src_dumps + " dosya bulundu" : "⚠ klasör bulunamadı") : "");
    void loadRemotes(rp[0]);
    loadSmtpSelect(v.smtp_profile);
    void loadStorages();
    ramHint();
    Array.prototype.slice.call(document.querySelectorAll("#m-edit input,#m-edit select"))
        .forEach((e) => { e.oninput = markDirty; e.onchange = markDirty; });
    fld("e-chunk").oninput = () => { ramHint(); markDirty(); };
    fld("e-tr").oninput = () => { ramHint(); markDirty(); };
    hesapPaneliTasi("w-hesap-yuvasi", false);
    wGoster();
    openM("m-edit");
}
function validatePlan() {
    // Alanlarin tamami tablodan dogrulanir (bkz. alanlar.ts).
    // Burada yalnizca birden fazla alani birlikte ilgilendiren kurallar kalir.
    let ok = alanlariDogrula();
    if (!val("e-acct"))
        ok = bad("e-acct", "önce bir Google hesabı ekle") && ok;
    else
        good("e-acct");
    ok = vRx("e-folder", RX.folder, 'klasör adında : * ? " < > | olamaz') && ok;
    if (!val("e-folder").trim())
        ok = bad("e-folder", "hedef klasör gerekli") && ok;
    if (chk("e-bwauto")) {
        const alt = bwBytes(val("e-bwmin")), ust = bwBytes(val("e-bwmax"));
        if (ust && alt && alt > ust)
            ok = bad("e-bwmin", "alt sınır üst sınırdan büyük olamaz") && ok;
    }
    if (Number(val("e-kc")) === 0 && Number(val("e-kd")) === 0) {
        ok = bad("e-kc", "ikisi birden 0 olamaz — hiç yedek kalmaz") && ok;
    }
    return ok;
}
async function savePlan() {
    if (!validatePlan()) {
        flash("form hatalı — kırmızı alanlara bak", false);
        return;
    }
    const wd = Array.prototype.slice.call(el("e-wd").querySelectorAll("input:checked"))
        .map((c) => Number(c.value));
    const body = {
        ...alanlariTopla(), // tum ortak alanlar (bkz. alanlar.ts)
        id: EDIT,
        remote: val("e-acct") + ":" + val("e-folder").trim().replace(/^\/+/, ""),
        weekdays: wd,
    };
    const j = await api("/api/plan/save", { method: "POST", body: JSON.stringify(body) });
    flash(j.msg || "", j.ok);
    if (j.ok) {
        dirty = false;
        wSihirbaz = false;
        closeM("m-edit");
        sel = j.id || sel;
        remember();
        void refresh();
    }
}
/* ---------- klasor gezgini ---------- */
async function loadStorages() {
    try {
        const j = await api("/api/storages");
        const s = j.storages || [];
        setHtml("e-stor", s.length ? "Proxmox depoları: " + s.map((x) => "<a href=\"#\" onclick=\"setSrc('" + x.path + "');return false\" style=\"color:#58a6ff\">"
            + esc(x.name) + " (" + x.dumps + ")</a>").join(" · ") : "");
    }
    catch { /* yok say */ }
}
function setSrc(path) { setVal("e-src", path); markDirty(); }
async function openBrowser() { await goDir(val("e-src") || ""); openM("m-browse"); }
async function goDir(p) {
    const j = await api("/api/browse?path=" + encodeURIComponent(p));
    cur = j.path;
    setTxt("b-path", j.path + (j.error ? "  ⚠ " + j.error : "  (" + j.dumps + " dosya)"));
    setHtml("b-stor", (j.roots || []).map((r) => "<button class=\"sm\" onclick=\"goDir('" + r + "')\">"
        + esc(r) + "</button>").join(""));
    let h = j.parent ? "<div onclick=\"goDir('" + j.parent + "')\"><span>⬆ üst klasör</span><span></span></div>" : "";
    h += (j.dirs || []).map((d) => "<div onclick=\"goDir('" + d.path + "')\"><span>📁 " + esc(d.name)
        + '</span><span class="small">' + (d.dumps ? d.dumps + " dosya" : "") + "</span></div>").join("");
    setHtml("b-list", h || '<div><span class="small">alt klasör yok</span><span></span></div>');
}
function pickHere() {
    setVal("e-src", cur);
    markDirty();
    closeM("m-browse");
    void api("/api/browse?path=" + encodeURIComponent(cur))
        .then((j) => setTxt("e-srchint", j.dumps + " dosya bulundu"));
}
/* ---------- Google hesaplari ---------- */
async function loadRemotes(selName) {
    try {
        const j = await api("/api/remotes");
        REM = j.remotes || [];
    }
    catch {
        REM = [];
    }
    setHtml("e-acct", REM.map((r) => '<option value="' + esc(r.name) + '">' + esc(r.name)
        + "</option>").join("") || '<option value="">(hesap yok)</option>');
    if (selName)
        setVal("e-acct", selName);
    setTxt("e-accthint", REM.length ? REM.length + " hesap tanımlı" : "Henüz hesap yok — 'Yönet' ile ekle.");
}
function openAccounts() {
    openM("m-acct");
    hesapPaneliTasi("hesap-ekle-yuvasi", true);
    acctTab(1);
    void renderAccounts();
    void api("/api/remote/auth/status").then((j) => {
        if (j.waiting && j.url) {
            el("a-authbox").style.display = "";
            setTxt("a-url", j.url);
            pollAuth();
        }
    });
}
function acctTab(n) {
    el("a-tab1").className = n === 1 ? "on" : "";
    el("a-tab2").className = n === 2 ? "on" : "";
    el("a-m1").style.display = n === 1 ? "" : "none";
    el("a-m2").style.display = n === 2 ? "" : "none";
}
async function renderAccounts() {
    const j = await api("/api/remotes?quota=1");
    REM = j.remotes || [];
    setHtml("a-list", REM.length ? REM.map((r) => {
        const q = r.quota || {};
        const line = q.ok ? hb(q.used) + " / " + hb(q.total) + "  ·  çöp " + hb(q.trashed || 0)
            + "  ·  boş " + hb(q.free || 0) : "⚠ " + esc(q.error || "kota okunamadı");
        return '<div class="card" style="margin-bottom:8px"><div style="display:flex;'
            + 'justify-content:space-between;gap:8px;align-items:center;flex-wrap:wrap">'
            + "<b>" + esc(r.name) + '</b> <span class="small">' + esc(r.type) + "</span>"
            + '<span style="flex:1"></span>'
            + "<button class=\"sm\" onclick=\"acctTest('" + r.name + "')\">Test</button>"
            + "<button class=\"sm warn\" onclick=\"acctDel('" + r.name + "')\">Sil</button></div>"
            + '<div class="small" style="margin-top:6px">' + line + "</div></div>";
    }).join("") : '<div class="small">Henüz hesap yok.</div>');
}
async function acctTest(n) {
    flash("kontrol ediliyor…", true);
    const j = await api("/api/remote/test?name=" + encodeURIComponent(n), { method: "POST" });
    flash(j.msg || "", j.ok);
}
async function acctDel(n) {
    if (!confirm("'" + n + "' kaldırılsın mı? Drive'daki dosyalara dokunulmaz."))
        return;
    const j = await api("/api/remote/delete?name=" + encodeURIComponent(n), { method: "POST" });
    flash(j.msg || "", j.ok);
    void renderAccounts();
    void loadRemotes();
}
async function acctPaste() {
    if (!RX.acct.test(val("a-name").trim())) {
        bad("a-name", "sadece harf, rakam, - ve _");
        return;
    }
    good("a-name");
    try {
        JSON.parse(val("a-token"));
    }
    catch {
        bad("a-token", "geçerli JSON değil");
        return;
    }
    good("a-token");
    const j = await api("/api/remote/add", { method: "POST",
        body: JSON.stringify({ name: val("a-name"), token: val("a-token") }) });
    flash(j.msg || "", j.ok);
    if (j.ok) {
        setVal("a-token", "");
        setVal("a-name", "");
        void renderAccounts();
        void loadRemotes(j.name);
        if (wSihirbaz) {
            hesapPaneliTasi("w-hesap-yuvasi", false);
            good("e-acct");
            markDirty();
        }
    }
}
async function authStart() {
    if (!RX.acct.test(val("a-name").trim())) {
        bad("a-name", "önce geçerli bir hesap adı yaz");
        return;
    }
    good("a-name");
    const j = await api("/api/remote/auth/start", { method: "POST" });
    setVal("a-tunnel", j.tunnel || "");
    if (!j.ok) {
        flash(j.msg || "başlatılamadı", false);
        return;
    }
    el("a-authbox").style.display = "";
    setTxt("a-url", j.url || "");
    flash("adresi tarayıcında aç", true);
    pollAuth();
}
function pollAuth() {
    window.clearInterval(authTimer);
    authTimer = window.setInterval(() => {
        void (async () => {
            const st = await api("/api/remote/auth/status");
            if (st.ready) {
                window.clearInterval(authTimer);
                setTxt("a-wait", "jeton alındı, hesap oluşturuluyor…");
                const j = await api("/api/remote/auth/finish", { method: "POST",
                    body: JSON.stringify({ name: val("a-name") }) });
                flash(j.msg || "", j.ok);
                el("a-authbox").style.display = "none";
                if (j.ok) {
                    setVal("a-name", "");
                    void renderAccounts();
                    void loadRemotes(j.name);
                    if (wSihirbaz) {
                        hesapPaneliTasi("w-hesap-yuvasi", false);
                        good("e-acct");
                        markDirty();
                    }
                }
            }
            else if (!st.waiting) {
                window.clearInterval(authTimer);
                setTxt("a-wait", "yetkilendirme sonlandı");
            }
        })();
    }, 2000);
}
async function authCancel() {
    window.clearInterval(authTimer);
    await api("/api/remote/auth/cancel", { method: "POST" });
    el("a-authbox").style.display = "none";
    flash("iptal edildi", true);
}
const SMTP_PRESETS = {
    gmail: { host: "smtp.gmail.com", port: 587, security: "starttls",
        hint: "Gmail hesap şifresi çalışmaz. Google Hesabı → Güvenlik → 2 Adımlı Doğrulama → Uygulama şifreleri'nden 16 haneli şifre üret." },
    outlook: { host: "smtp.office365.com", port: 587, security: "starttls",
        hint: "Microsoft 365 / Outlook. Kurumsal hesaplarda SMTP AUTH kapalı olabilir, yöneticiden açtırman gerekebilir." },
    yandex: { host: "smtp.yandex.com", port: 465, security: "ssl",
        hint: "Yandex'te 'Uygulama şifreleri' bölümünden şifre üret. Kullanıcı adı tam adres olmalı." },
    yahoo: { host: "smtp.mail.yahoo.com", port: 465, security: "ssl",
        hint: "Yahoo'da uygulama şifresi zorunlu, normal şifre kabul edilmez." },
    custom: { host: "", port: 587, security: "starttls",
        hint: "Sunucu, port ve güvenlik ayarını sağlayıcından öğren." },
};
function smtpPreset() {
    const v = SMTP_PRESETS[val("s-preset")];
    if (!v)
        return;
    if (v.host)
        setVal("s-host", v.host);
    setVal("s-port", v.port);
    setVal("s-sec", v.security);
    setTxt("s-presethint", v.hint);
}
function openSmtp() { openM("m-smtp"); smtpClear(); renderSmtp(); }
function loadSmtpSelect(selId) {
    SMTP = (S && S.smtp) || [];
    setHtml("e-smtp", SMTP.map((x) => '<option value="' + esc(x.id) + '">' + esc(x.name)
        + " (" + esc(x.user || x.host) + ")</option>").join("") || '<option value="">(profil yok)</option>');
    if (selId)
        setVal("e-smtp", selId);
    setTxt("e-smtphint", SMTP.length ? "" : "Mail profili yok — '✉ Yönet' ile ekle, yoksa mail gitmez.");
}
function renderSmtp() {
    SMTP = (S && S.smtp) || [];
    setHtml("s-list", SMTP.length ? SMTP.map((x) => '<div class="card" style="margin-bottom:8px"><div style="display:flex;justify-content:space-between;'
        + 'gap:8px;align-items:center;flex-wrap:wrap"><b>' + esc(x.name) + "</b>"
        + '<span style="flex:1"></span>'
        + "<button class=\"sm\" onclick=\"smtpEdit('" + x.id + "')\">Düzenle</button>"
        + "<button class=\"sm\" onclick=\"smtpTest('" + x.id + "')\">Test maili</button>"
        + "<button class=\"sm warn\" onclick=\"smtpDel('" + x.id + "')\">Sil</button></div>"
        + '<div class="small" style="margin-top:6px">' + esc(x.user || "-") + " · " + esc(x.host)
        + ":" + x.port + " · " + esc(x.security) + "</div></div>").join("")
        : '<div class="small">Henüz profil yok.</div>');
}
function smtpClear() {
    setVal("s-id", "");
    setVal("s-name", "");
    setVal("s-host", "smtp.gmail.com");
    setVal("s-port", 587);
    setVal("s-sec", "starttls");
    setVal("s-user", "");
    setVal("s-pass", "");
    setVal("s-from", "");
    setVal("s-preset", "");
    setTxt("s-presethint", "");
    setTxt("s-formtitle", "Yeni profil");
}
function smtpEdit(id) {
    const x = SMTP.filter((y) => y.id === id)[0];
    if (!x)
        return;
    setVal("s-id", x.id);
    setVal("s-name", x.name);
    setVal("s-host", x.host);
    setVal("s-port", x.port);
    setVal("s-sec", x.security);
    setVal("s-user", x.user);
    setVal("s-pass", "");
    setVal("s-from", x.from);
    setTxt("s-formtitle", "Düzenle: " + x.name);
}
async function smtpSave() {
    let ok = true;
    ok = vTxt("s-name", "profil adı gerekli") && ok;
    ok = vRx("s-host", RX.host, "geçerli bir sunucu adı yaz") && ok;
    ok = vNum("s-port", 1, 65535, "1-65535 arası port") && ok;
    ok = vMails("s-user", true) && ok;
    ok = vMails("s-from", true) && ok;
    if (!ok) {
        flash("form hatalı", false);
        return;
    }
    const b = {
        id: val("s-id"), name: val("s-name"), host: val("s-host"), port: Number(val("s-port")),
        security: val("s-sec"), user: val("s-user"), from: val("s-from"),
    };
    if (val("s-pass"))
        b.pass = val("s-pass");
    if (!val("s-id") && !val("s-pass")) {
        flash("yeni profil için şifre gerekli", false);
        return;
    }
    const j = await api("/api/smtp/save", { method: "POST", body: JSON.stringify(b) });
    flash(j.msg || "", j.ok);
    if (j.ok) {
        smtpClear();
        await refresh();
        renderSmtp();
        loadSmtpSelect();
    }
}
async function smtpDel(id) {
    if (!confirm("Profil silinsin mi?"))
        return;
    const j = await api("/api/smtp/delete?id=" + encodeURIComponent(id), { method: "POST" });
    flash(j.msg || "", j.ok);
    await refresh();
    renderSmtp();
    loadSmtpSelect();
}
async function smtpTest(id) {
    const to = prompt("Test maili hangi adrese gitsin?\n(boş bırakırsan gönderen adresine gider)", "");
    if (to === null)
        return;
    flash("gönderiliyor…", true);
    const j = await api("/api/smtp/test?id=" + encodeURIComponent(id) + "&to=" + encodeURIComponent(to), { method: "POST" });
    flash(j.msg || "", j.ok);
}
/* ---------- genel ayarlar ---------- */
function openSettings() {
    const s = S ? S.settings : null;
    if (!s)
        return;
    setVal("g-bind", s.ui_bind);
    setVal("g-port", s.ui_port);
    setVal("g-user", s.ui_user);
    setVal("g-pass", "");
    setVal("g-refresh", s.ui_refresh_sec);
    setVal("g-roots", (s.browse_roots || []).join(", "));
    setVal("g-re", s.dump_regex);
    setVal("g-hist", s.history_max);
    setVal("g-logn", s.log_tail_lines);
    setVal("g-tail", s.rclone_tail_lines);
    setVal("g-rows", s.snapshot_max_rows);
    setVal("g-logmb", s.log_max_mb);
    setVal("g-logkeep", s.log_keep);
    setVal("g-tmo", s.rclone_timeout_min);
    setChk("g-cleanup", s.allow_account_cleanup);
    setVal("g-cert", s.ssl_cert || "");
    setVal("g-key", s.ssl_key || "");
    setVal("g-nets", (s.allow_networks || []).join(", "));
    setChk("g-upcheck", s.update_check !== false);
    setChk("g-upauto", !!s.update_auto);
    setVal("g-upurl", s.update_url || "");
    upDurum();
    setChk("g-cookiesec", !!s.cookie_secure);
    const t = S && S.tls;
    const c = t && t.sertifika;
    setHtml("g-tlsdurum", t && t.aktif
        ? '<span style="color:#7ee2a8">🔒 TLS açık.</span> Sertifika: <b>' + esc(c ? c.konu : "-")
            + "</b> · veren: " + esc(c ? c.veren : "-") + " · bitiş: " + esc(c ? c.bitis : "-")
        : '<span style="color:#ff9b9b">⚠ TLS kapalı</span> — arayüz düz HTTP çalışıyor.');
    openM("m-set");
}
async function saveSettings() {
    let ok = true;
    ok = vRx("g-bind", RX.ip, "IP adresi yaz (0.0.0.0 veya 127.0.0.1)") && ok;
    ok = vNum("g-port", 1, 65535, "1-65535") && ok;
    ok = vTxt("g-user", "kullanıcı adı gerekli") && ok;
    ok = vNum("g-refresh", 1, 3600, "1-3600 sn") && ok;
    ok = vNum("g-hist", 1, 1000, "1-1000") && ok;
    ok = vNum("g-logn", 10, 5000, "10-5000") && ok;
    ok = vNum("g-tail", 1, 1000, "1-1000") && ok;
    ok = vNum("g-rows", 1, 10000, "1-10000") && ok;
    ok = vNum("g-logmb", 0, 1000, "0-1000 MB") && ok;
    ok = vNum("g-logkeep", 1, 20, "1-20") && ok;
    ok = vNum("g-tmo", 0, 1440, "0-1440 dk") && ok;
    ok = vTxt("g-re", "kalıp boş olamaz") && ok;
    const netler = val("g-nets").split(",").map((x) => x.trim()).filter(Boolean);
    const kotuNet = netler.filter((x) => !/^\d{1,3}(\.\d{1,3}){3}(\/\d{1,2})?$/.test(x)
        && !/^[0-9a-fA-F:]+(\/\d{1,3})?$/.test(x));
    if (kotuNet.length)
        ok = bad("g-nets", "geçersiz ağ: " + kotuNet[0]) && ok;
    else
        good("g-nets");
    if (!ok) {
        flash("form hatalı", false);
        return;
    }
    const b = {
        ui_bind: val("g-bind"), ui_port: Number(val("g-port")), ui_user: val("g-user"),
        ui_refresh_sec: Number(val("g-refresh")), dump_regex: val("g-re"),
        history_max: Number(val("g-hist")), log_tail_lines: Number(val("g-logn")),
        rclone_tail_lines: Number(val("g-tail")), snapshot_max_rows: Number(val("g-rows")),
        log_max_mb: Number(val("g-logmb")), log_keep: Number(val("g-logkeep")),
        rclone_timeout_min: Number(val("g-tmo")), allow_account_cleanup: chk("g-cleanup"),
        ssl_cert: val("g-cert"), ssl_key: val("g-key"), cookie_secure: chk("g-cookiesec"),
        allow_networks: val("g-nets").split(",").map((x) => x.trim()).filter(Boolean),
        update_check: chk("g-upcheck"), update_auto: chk("g-upauto"), update_url: val("g-upurl"),
        browse_roots: val("g-roots").split(",").map((x) => x.trim()).filter(Boolean),
    };
    if (val("g-pass"))
        b.ui_pass = val("g-pass");
    const j = await api("/api/settings/save", { method: "POST", body: JSON.stringify(b) });
    flash(j.msg || "", j.ok);
    if (j.ok) {
        closeM("m-set");
        void refresh();
    }
}
/* ---------- guncelleme ---------- */
function upDurum() {
    const g = S && S.guncelleme;
    const v = (S && S.surum) || "?";
    let h = "Kurulu sürüm: <b>v" + esc(v) + "</b>";
    if (g && g.hata)
        h += ' · <span style="color:#ff9b9b">kontrol hatası: ' + esc(g.hata) + "</span>";
    else if (g && g.yeni_var)
        h += ' · <span style="color:#ffd479">yeni sürüm hazır: <b>v'
            + esc(g.uzak || "") + "</b></span>";
    else if (g && g.uzak)
        h += " · güncel";
    setHtml("g-guncel", h);
    el("g-upbtn").style.display = g && g.yeni_var ? "" : "none";
}
async function upKontrol() {
    flash("kontrol ediliyor…", true);
    const j = await api("/api/update/check?force=1");
    await refresh();
    upDurum();
    flash(j.hata ? "hata: " + j.hata
        : (j.yeni_var ? "yeni sürüm var: v" + j.uzak : "güncel: v" + j.surum), !j.hata);
}
async function upKur() {
    if (!confirm("Güncelleme kurulacak.\n\nPlanların ve ayarların korunur, ikisinin de yedeği alınır.\n"
        + "Arayüz birkaç saniye yeniden başlar. Devam edilsin mi?"))
        return;
    flash("indiriliyor ve doğrulanıyor…", true);
    const j = await api("/api/update/apply", { method: "POST" });
    flash(j.msg || "", j.ok);
    if (j.ok)
        window.setTimeout(() => location.reload(), 6000);
}
async function upGeri() {
    if (!confirm("Önceki sürüme dönülecek. Devam edilsin mi?"))
        return;
    const j = await api("/api/update/rollback", { method: "POST" });
    flash(j.msg || "", j.ok);
    if (j.ok)
        window.setTimeout(() => location.reload(), 6000);
}
/* ---------- baslangic ---------- */
Array.prototype.slice.call(document.querySelectorAll(".mask")).forEach((m) => {
    m.addEventListener("click", (e) => {
        if (e.target !== m)
            return;
        if (m.id === "m-edit" && dirty
            && !confirm("Kaydedilmemiş değişiklikler var, kapatılsın mı?"))
            return;
        m.classList.remove("show");
        if (m.id === "m-edit")
            dirty = false;
    });
});
document.addEventListener("keydown", (e) => {
    if (e.key !== "Escape")
        return;
    Array.prototype.slice.call(document.querySelectorAll(".mask.show")).forEach((m) => {
        if (m.id === "m-edit" && dirty
            && !confirm("Kaydedilmemiş değişiklikler var, kapatılsın mı?"))
            return;
        m.classList.remove("show");
    });
});
void refresh();
/**
 * Plan formu alan tablosu.
 *
 * Onceden openEditor / savePlan / validatePlan ayni alan listesini uc kez, uc farkli
 * bicimde tekrarliyordu; yeni bir alan eklerken uc yeri birden duzenlemek gerekiyordu.
 * Artik tek kaynak burasi: doldur(), topla() ve dogrula() bu tablodan turer.
 */
const PLAN_ALANLARI = [
    // 1. Plan
    { id: "e-name", anahtar: "name", tip: "metin", adim: 1, mesaj: "plan adı gerekli" },
    { id: "e-enabled", anahtar: "enabled", tip: "onay", adim: 0 },
    // 2. Kaynak
    { id: "e-src", anahtar: "src_dir", tip: "metin", adim: 2, mesaj: "kaynak klasör gerekli" },
    // 3. Hedef — remote iki alandan birlesir, ozel islenir (e-acct + e-folder)
    // 4. Saklama
    { id: "e-kd", anahtar: "keep_days", tip: "sayi", adim: 4, min: 0, max: 3650, mesaj: "0-3650 arası gün" },
    { id: "e-kc", anahtar: "keep_count", tip: "sayi", adim: 4, min: 0, max: 999, mesaj: "0-999 arası adet" },
    { id: "e-td", anahtar: "drive_trash_days", tip: "sayi", adim: 4, min: 0, max: 365, mesaj: "0-365 arası gün" },
    // 5. Zamanlama ve cakisma
    { id: "e-runat", anahtar: "run_at", tip: "saat", adim: 5, mesaj: "SS:DD biçiminde saat (ör. 03:00)" },
    { id: "e-wv", anahtar: "wait_for_vzdump", tip: "onay", adim: 0 },
    { id: "e-wvm", anahtar: "vzdump_wait_min", tip: "sayi", adim: 5, min: 0, max: 1440, mesaj: "0-1440 dakika" },
    { id: "e-mage", anahtar: "min_age_min", tip: "sayi", adim: 5, min: 0, max: 1440, mesaj: "0-1440 dakika" },
    { id: "e-skip", anahtar: "skip_patterns", tip: "liste", adim: 0 },
    { id: "e-pof", anahtar: "prune_on_failure", tip: "onay", adim: 0 },
    // 6. Aktarim
    { id: "e-bw", anahtar: "bwlimit", tip: "metin", adim: 6, rx: RX.bw, ops: true, mesaj: "ör. 30M, 2M veya off" },
    { id: "e-tr", anahtar: "transfers", tip: "sayi", adim: 6, min: 1, max: 64, mesaj: "1-64 arası" },
    { id: "e-ck", anahtar: "checkers", tip: "sayi", adim: 6, min: 1, max: 64, mesaj: "1-64 arası" },
    { id: "e-chunk", anahtar: "drive_chunk", tip: "metin", adim: 6, rx: RX.chunk, mesaj: "ör. 64M, 128M, 8M" },
    { id: "e-extra", anahtar: "rclone_extra", tip: "liste", adim: 0 },
    { id: "e-bwup", anahtar: "bwlimit_upload_only", tip: "onay", adim: 0 },
    { id: "e-bwauto", anahtar: "bwlimit_auto", tip: "onay", adim: 0 },
    { id: "e-bwif", anahtar: "bw_auto_iface", tip: "metin", adim: 0, ops: true },
    // 7. Bildirim
    { id: "e-smtp", anahtar: "smtp_profile", tip: "metin", adim: 0, ops: true },
    { id: "e-nsuc", anahtar: "notify_success", tip: "onay", adim: 0 },
    { id: "e-nerr", anahtar: "notify_failure", tip: "onay", adim: 0 },
    { id: "e-nskip", anahtar: "notify_skipped", tip: "onay", adim: 0 },
    { id: "e-wr", anahtar: "weekly_report", tip: "onay", adim: 0 },
    { id: "e-rday", anahtar: "report_day", tip: "sayi", adim: 0, min: 1, max: 7 },
    { id: "e-mail", anahtar: "mail_to", tip: "metin", adim: 7, ops: true,
        ozelDogrula: (id) => vMails(id, !(chk("e-nsuc") || chk("e-nerr") || chk("e-nskip") || chk("e-wr"))) },
    { id: "e-rmail", anahtar: "report_mail_to", tip: "metin", adim: 7, ops: true,
        ozelDogrula: (id) => vMails(id, true) },
    // Haftalik rapor alanlari: yalnizca rapor acikken dogrulanir
    { id: "e-rat", anahtar: "report_at", tip: "saat", adim: 7, vars: "09:00",
        kosul: () => chk("e-wr"), mesaj: "SS:DD biçiminde saat" },
    { id: "e-rdays", anahtar: "report_days", tip: "sayi", adim: 7, min: 1, max: 365, vars: 7,
        kosul: () => chk("e-wr"), mesaj: "1-365 gün" },
    { id: "e-rstale", anahtar: "report_stale_days", tip: "sayi", adim: 7, min: 0, max: 365, vars: 2,
        kosul: () => chk("e-wr"), mesaj: "0-365 gün" },
    { id: "e-rquota", anahtar: "report_quota_warn", tip: "sayi", adim: 7, min: 0, max: 100, vars: 90,
        kosul: () => chk("e-wr"), mesaj: "0-100 arası yüzde" },
    // Bant genisligi cizelgesi ve otomatik mod: yalnizca ilgiliyken dogrulanir
    { id: "e-bwsch", anahtar: "bwlimit_schedule", tip: "metin", adim: 6, ops: true, vars: "",
        ozelDogrula: (id) => vBwSched(id) },
    { id: "e-bwlink", anahtar: "bw_auto_link", tip: "metin", adim: 6, rx: RX.bw, vars: "100M",
        kosul: () => chk("e-bwauto"), mesaj: "ör. 12M, 100M" },
    { id: "e-bwres", anahtar: "bw_auto_reserve_pct", tip: "sayi", adim: 6, min: 0, max: 95, vars: 30,
        kosul: () => chk("e-bwauto"), mesaj: "0-95 arası yüzde" },
    { id: "e-bwmin", anahtar: "bw_auto_min", tip: "metin", adim: 6, rx: RX.bw, vars: "1M",
        kosul: () => chk("e-bwauto"), mesaj: "ör. 512K, 1M" },
    { id: "e-bwmax", anahtar: "bw_auto_max", tip: "metin", adim: 6, rx: RX.bw, ops: true, vars: "",
        kosul: () => chk("e-bwauto"), mesaj: "ör. 30M veya boş" },
    { id: "e-bwint", anahtar: "bw_auto_interval_sec", tip: "sayi", adim: 6, min: 2, max: 3600, vars: 10,
        kosul: () => chk("e-bwauto"), mesaj: "2-3600 sn" },
    { id: "e-bwsm", anahtar: "bw_auto_smooth", tip: "sayi", adim: 6, min: 0.05, max: 1, vars: 0.4,
        kosul: () => chk("e-bwauto"), mesaj: "0.05 - 1 arası" },
    { id: "e-bwstep", anahtar: "bw_auto_step_pct", tip: "sayi", adim: 6, min: 1, max: 90, vars: 25,
        kosul: () => chk("e-bwauto"), mesaj: "1-90 arası yüzde" },
];
/** Formu bir plandan (veya varsayilanlardan) doldurur. */
function alanlariDoldur(v) {
    var _a;
    for (const a of PLAN_ALANLARI) {
        if (!document.getElementById(a.id))
            continue;
        const ham = v[a.anahtar];
        const d = ham === undefined || ham === null || ham === "" ? ((_a = a.vars) !== null && _a !== void 0 ? _a : ham) : ham;
        if (a.tip === "onay")
            setChk(a.id, ham !== false && ham !== undefined ? Boolean(ham) : false);
        else if (a.tip === "liste")
            setVal(a.id, Array.isArray(d) ? d.join(" ") : "");
        else
            setVal(a.id, d === undefined || d === null ? "" : d);
    }
}
/** Form degerlerini plan nesnesine toplar. */
function alanlariTopla() {
    const o = {};
    for (const a of PLAN_ALANLARI) {
        if (!document.getElementById(a.id))
            continue;
        if (a.tip === "onay")
            o[a.anahtar] = chk(a.id);
        else if (a.tip === "sayi")
            o[a.anahtar] = Number(val(a.id));
        else if (a.tip === "liste")
            o[a.anahtar] = val(a.id).split(a.ayirac || /\s+/).filter(Boolean);
        else
            o[a.anahtar] = val(a.id);
    }
    return o;
}
/** Tabloya gore dogrular. adim verilirse yalnizca o adimin alanlari kontrol edilir. */
function alanlariDogrula(adim) {
    var _a, _b;
    let ok = true;
    for (const a of PLAN_ALANLARI) {
        if (!document.getElementById(a.id))
            continue;
        if (adim !== undefined && a.adim !== adim)
            continue;
        if (a.adim === 0)
            continue;
        if (a.kosul && !a.kosul()) {
            good(a.id);
            continue;
        }
        if (a.ozelDogrula) {
            ok = a.ozelDogrula(a.id) && ok;
            continue;
        }
        if (a.tip === "sayi")
            ok = vNum(a.id, (_a = a.min) !== null && _a !== void 0 ? _a : 0, (_b = a.max) !== null && _b !== void 0 ? _b : null, a.mesaj || "geçersiz sayı") && ok;
        else if (a.tip === "saat")
            ok = vRx(a.id, RX.time, a.mesaj || "SS:DD", a.ops) && ok;
        else if (a.rx)
            ok = vRx(a.id, a.rx, a.mesaj || "geçersiz değer", a.ops) && ok;
        else if (!a.ops)
            ok = vTxt(a.id, a.mesaj || "bu alan gerekli") && ok;
    }
    return ok;
}

</script></body></html>
'''
# --- UI BUNDLE END ---

def main():
    a = sys.argv[1:]; cmd = a[0] if a else "help"
    def opt(name, dflt=None):
        return a[a.index(name) + 1] if name in a and a.index(name) + 1 < len(a) else dflt
    pid = opt("--plan"); trig = opt("--trigger", "manuel")
    if cmd == "init": init_conf()
    elif cmd == "tick":
        ran = do_tick(); print("calistirilan plan:", ", ".join(ran) if ran else "yok")
    elif cmd == "run":
        targets = [pid] if pid else [p["id"] for p in cfg().get("plans", []) if p.get("enabled", True)]
        if not targets: print("calisacak plan yok")
        for t in targets: do_run(t, trig)
    elif cmd == "serve": serve()
    elif cmd == "snapshot":
        for p in cfg().get("plans", []):
            if pid and p["id"] != pid: continue
            put_pstate(p["id"], update_snapshot(p)); print("ok:", p["id"])
    elif cmd == "status": print(json.dumps(public_status(), ensure_ascii=False, indent=2))
    elif cmd in ("version", "--version", "-V"): print(SURUM)
    elif cmd == "update":
        if "--check" in a: print(json.dumps(guncelleme_kontrol(zorla=True), ensure_ascii=False, indent=2))
        elif "--rollback" in a: print(guncelleme_geri_al()["msg"])
        else: print(guncelleme_uygula(zorla="--force" in a)["msg"])
    elif cmd == "plans":
        for p in cfg().get("plans", []):
            nr = next_run(p)
            print(f"{p['id']:20} {'ETKIN' if p['enabled'] else 'KAPALI':6} {p['src_dir']} -> {p['remote']} "
                  f"| {p['keep_days']}g/min{p['keep_count']}/cop{p['drive_trash_days']}g "
                  f"| {p['run_at']} | sonraki: {nr.strftime(TS_FMT) if nr else '-'}")
    elif cmd == "prune":
        for p in cfg().get("plans", []):
            if pid and p["id"] != pid: continue
            print(p["id"], do_prune(p), "dosya cope tasindi")
    elif cmd in ("purgetrash", "emptytrash"):
        for p in cfg().get("plans", []):
            if pid and p["id"] != pid: continue
            print(p["id"], do_purge_trash(p), "dosya kalici silindi")
    else: print(__doc__)

if __name__ == "__main__": main()
