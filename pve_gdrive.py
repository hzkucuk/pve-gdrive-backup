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
  python3 pve_gdrive.py saglik            # zamanlayici yasiyor mu (cikis kodu 0/1)
  python3 pve_gdrive.py oturumlar         # kayitli "beni hatirla" oturumlari
  python3 pve_gdrive.py version           # surum
  python3 pve_gdrive.py butunluk          # betik degismis mi (--sabitle: referansi yenile)
  python3 pve_gdrive.py plans             # planlari listeler
  python3 pve_gdrive.py aglar             # izinli aglari gosterir/duzenler (kurtarma)
  python3 pve_gdrive.py disa-aktar        # plan/mail ayarlarini JSON olarak yazar
  python3 pve_gdrive.py ice-aktar         # stdin'den ayar yukler
"""
import os, sys, json, time, base64, subprocess, smtplib, re, fcntl, hmac, threading, fnmatch
import hashlib, secrets, random, ssl, ipaddress, shutil, tempfile, io
import urllib.request, urllib.parse, html as _html
from collections import deque
from datetime import datetime, timedelta
from email.message import EmailMessage
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, parse_qs, unquote

SURUM = "1.6.0"
CONFIG_PATH = os.environ.get("PVE_GDRIVE_CONF", "/etc/pve-gdrive.conf")
LOCK_DIR    = "/tmp"

# Plan disi, kurulum geneli ayarlar
GLOBAL_DEFAULTS = {
    "log_file": "/var/log/pve-gdrive.log",
    "state_file": "/var/lib/pve-gdrive/state.json",
    "dil": "tr",                  # arayuz ve mail dili: tr | en
    "ui_bind": "0.0.0.0",
    "ui_port": 8787,
    "ui_user": "admin",
    "ui_pass": "degistir-beni",   # ilk acilista pbkdf2 ile hash'lenip uzerine yazilir
    "api_token": "",              # otomasyon icin: Authorization: Bearer <token>
    # --- oturum ve giris guvenligi ---
    "remember_enabled": True,     # giris ekraninda "beni hatirla" secenegi
    "remember_days": 30,          # hatirlanan oturumun omru (gun)
    # hatirlanan oturum adrese nasil baglansin: ip | ag | yok (bkz. ayni_kaynak)
    "session_ip_bind": "ip",
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
    # Lax | Strict | None. Strict en siki ama disaridan gelen baglantida
    # oturumu "yokmus" gibi gosterir; varsayilan Lax.
    "cookie_samesite": "Lax",
    "trust_proxy_header": False,  # X-Forwarded-For'a guvenilsin mi (nginx arkasinda true)
    # --- otomatik guncelleme ---
    "update_check": True,         # gunde bir yeni surum var mi diye bak
    "update_auto": False,         # bulununca kendiliginden kur (varsayilan: sadece bildir)
    "update_url": "https://raw.githubusercontent.com/hzkucuk/pve-gdrive-backup/main/pve_gdrive.py",
    # Guncelleme indirilen betigi ROOT olarak calisan dosyanin uzerine yazar.
    # Bu yuzden adres serbest birakilamaz: arayuze giren biri adresi kendi
    # sunucusuna cevirip root kod calistirabilirdi. Yalnizca bu hostlar ve
    # yalnizca https kabul edilir. Kendi deponu kullanacaksan buraya ekle.
    "update_izinli_hostlar": ["raw.githubusercontent.com", "github.com",
                              "objects.githubusercontent.com", "codeload.github.com"],
    # Doluysa indirilen dosyanin sha256'si bununla ayni olmak zorunda.
    # Ekstra guvence: adres ele gecse bile eslesmeyen dosya kurulmaz.
    "update_sha256": "",
    "update_backup_keep": 5,      # saklanacak eski surum sayisi
    "quota_cache_min": 15,        # hesap kotasi kac dakika onbellekte tutulsun
    "oneri_pay_pct": 60,          # saklama onerisi bos alanin en fazla bu yuzdesini kullanir
    # Zamanlayici: systemd timer yerine surecin kendi icinde calissin mi.
    # null = otomatik: systemd timer yoksa surec ici zamanlayici acilir
    "debug": False,               # ayrintili hata izleri loga yazilsin mi
    "internal_scheduler": None,
    "scheduler_interval_sec": 300,
    # Arayuze yalnizca bu aglardan erisilebilir. Bos liste = kisitlama yok.
    # Firewall kurmaya gerek kalmaz; yanlis yazarsan config'ten geri alinir,
    # SSH ve Proxmox arayuzu bu ayardan hic etkilenmez.
    "allow_networks": [],
    # Sunucunun KENDI yerel agi her zaman izinlidir; yanlis bir kisitlama yuzunden
    # arayuze hic erisemez duruma dusulmez. Kapatmak icin false yap.
    "lan_hep_acik": True,
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
    # --- servis izleme ---
    # --- telegram ---
    "telegram_enabled": False,
    "telegram_token": "",         # BotFather'dan alinan jeton (SIR)
    "telegram_chat_id": "",       # kisi ya da grup id'si
    "failure_mail": True,         # systemd birimi cokunce mail at
    "failure_mail_to": "",        # bos ise ilk planin alicisi kullanilir
    "failure_smtp_profile": "",
    "failure_mail_lines": 40,     # maile eklenecek gunluk satiri
    "tick_uyari_dk": 20,          # bu kadar dakikadir tick gelmediyse uyar
    "butunluk_mail": True,        # betik beklenmedik degisirse mail at
    # --- host yapilandirma yedegi ---
    # vzdump diskleri alir, host yapilandirmasini almaz. Bu kucuk arsiv
    # "diskleri nereye geri yukleyecegim" sorusunun cevabidir.
    "host_config_enabled": True,
    "host_config_paths": [
        "/etc/pve",                 # storage.cfg, user.cfg, VM/CT tanimlari, guvenlik duvari
        "/etc/network/interfaces",  # ag yapilandirmasi /etc/pve'de DEGIL
        "/etc/hosts", "/etc/resolv.conf",
        "/etc/fstab",               # /mnt/pve/... baglamalari burada
        "/etc/vzdump.conf",
        "/etc/apt/sources.list", "/etc/apt/sources.list.d",
        "/etc/systemd/system/pve-gdrive-ui.service",
        "/etc/systemd/system/pve-gdrive-tick.timer",
    ],
    # Ozel anahtarlar Drive'a cikmaz. authorized_keys/known_hosts ACIK anahtar
    # dosyalaridir; sizmalari erisim vermez, geri yuklemede SSH'i kurtarir.
    "host_config_exclude": [
        "*.key", "*.pem", "*.srl", "shadow.cfg", "*.pyc", "__pycache__/*",
        "etc/pve/local/*",          # nodes/<ad> dizinine sembolik baglanti
        # pmxcfs'in urettigi calisma-zamani dosyalari: yapilandirma degil,
        # her acilista yeniden olusur. Arsivde gurultu yapmasinlar.
        ".vmlist", ".rrd", ".members", ".clusterlog", ".debug", ".version",
    ],
    # priv/ varsayilan olarak tamamen disarida; yalnizca bunlar gecer.
    # Ikisi de ACIK anahtar dosyasidir, sizmalari erisim vermez.
    "host_config_priv_allow": ["authorized_keys", "known_hosts"],
    "host_config_json": True,       # pvesh ile REST agacinin okunabilir goruntusu
    "host_config_keep_count": 30,   # arsivler kucuk, gun sinirindan bagimsiz taban
    "host_config_pvesh": [
        "/version", "/cluster/resources", "/cluster/options", "/cluster/backup",
        "/storage", "/nodes/{node}/network", "/nodes/{node}/status",
        "/nodes/{node}/disks/list", "/access/users", "/access/roles", "/access/acl",
    ],
    # --- canli olay akisi (SSE) ---
    # rclone.conf her degisiklikten once kopyalanir; kac kopya tutulacagi
    "rclone_conf_yedek_tut": 20,
    "sse_enabled": True,        # kapatilirsa arayuz eski yoklama moduna doner
    "sse_watch_ms": 1000,       # diskteki degisikligin ne siklikta taranacagi
    "sse_heartbeat_sec": 20,    # ters vekil baglantiyi kesmesin diye bos sinyal
    # Kopan baglanti ancak bir yazma denemesinde anlasilir. Kisa araliklarla
    # yorum satiri yazilir (EventSource yok sayar) ki kapanan sekme birakilan
    # is parcacigini ve akis kotasini uzun sure tutmasin.
    "sse_ping_sec": 5,
    "sse_max_clients": 16,      # es zamanli acik akis siniri (her biri bir is parcacigi)
    "purge_batch": 50,          # cop temizliginde tek rclone cagrisinda silinecek dosya sayisi
    "purge_timeout_min": 30,    # cop temizligi rclone cagrisi icin zaman asimi (dakika)
    "log_max_mb": 5,            # log dosyasi bu boyutu asinca dondurulur
    "log_keep": 2,              # saklanacak eski log dosyasi sayisi
    "dump_regex": r"^(vzdump-(qemu|lxc)-(\d+)-(\d{4}_\d{2}_\d{2}-\d{2}_\d{2}_\d{2}))",
    "plans": []
}

# Her planin kendi ayarlari - GUN cinsinden olan her sey burada, parametrik
PLAN_DEFAULTS = {
    # Host yapilandirma yedegi: plan bazinda kapatilabilir, yollari/dislama
    # listesi verilmezse GLOBAL_DEFAULTS'taki liste kullanilir (bkz. hostconf_ayar).
    "host_config_enabled": True, "host_config_json": True, "host_config_keep_count": 30,
    # Birincil hedef calismazsa sirayla denenecek yedek hedefler ("hesap:klasor").
    "yedek_hedefler": [],
    "telegram": True,             # bu plan Telegram'a bildirim gondersin mi
    "telegram_chat_id": "",       # bos = genel ayardaki sohbet
    "id": "",
    "name": "Yeni plan",
    "enabled": True,
    "src_dir": "/var/lib/vz/dump",
    "remote": "gdrive:proxmox-yedek",
    "keep_days": 14,          # Drive'da normal duracagi gun sayisi
    "keep_count": 3,          # VM/CT basina her kosulda korunacak en yeni set (gun sinirindan bagimsiz)
    "drive_trash_days": 1,    # Google cop kutusunda bekleyecegi gun sayisi
    "run_at": "03:00",        # gunun saati
    "weekdays": [],           # bos = her gun, yoksa 1=Pzt .. 7=Paz
    "bwlimit": "30M",         # sabit sinir; "off" = sinirsiz
    "bwlimit_schedule": "",   # saat cizelgesi, or. "08:00,2M 19:00,30M 23:00,off"
    "bwlimit_upload_only": True,  # sinir yalnizca yuklemeye uygulansin
    # --- otomatik bant genisligi: hattaki diger trafige gore kendini ayarlar ---
    "bwlimit_auto": False,
    # Hat kapasitesi nasil belirlensin: "ogren" (fiilen olculen en yuksek
    # surekli hiz) veya "manuel" (asagidaki bw_auto_link). Arayuzun link hizi
    # kullanilmaz: 1 Gbit LAN'in arkasinda 60 Mbit ISS olabilir.
    "bw_auto_link_mode": "ogren",
    "bw_auto_link": "100M",       # yalnizca manuel kipte: toplam YUKLEME kapasiten
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
    q["host_config_enabled"] = bool(q.get("host_config_enabled",
                                          GLOBAL_DEFAULTS["host_config_enabled"]))
    q["host_config_json"] = bool(q.get("host_config_json", GLOBAL_DEFAULTS["host_config_json"]))
    try: q["host_config_keep_count"] = max(0, int(q.get("host_config_keep_count")
                                                  or GLOBAL_DEFAULTS["host_config_keep_count"]))
    except Exception: q["host_config_keep_count"] = GLOBAL_DEFAULTS["host_config_keep_count"]
    for k in ("host_config_paths", "host_config_exclude"):
        if k in q and not isinstance(q[k], list): q.pop(k)
    q["id"] = str(q.get("id") or "").strip() or slug(q.get("name") or "plan")
    for k in ("keep_days", "keep_count", "transfers", "checkers"):
        try: q[k] = max(0, int(q[k]))
        except Exception: q[k] = PLAN_DEFAULTS[k]
    if not isinstance(q.get("rclone_extra"), list): q["rclone_extra"] = []
    yh, gorulen = [], {str(q.get("remote") or "").strip()}
    for h in (q.get("yedek_hedefler") if isinstance(q.get("yedek_hedefler"), list) else []):
        h = str(h or "").strip()
        # Ayni hedef iki kez yazilirsa ikincisi anlamsiz; birincil hedefin
        # tekrari ise tehlikeli (basarisizlik ayni yere iki kez denenir).
        if h and ":" in h and h not in gorulen: yh.append(h); gorulen.add(h)
    q["yedek_hedefler"] = yh
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
    if str(q.get("bw_auto_link_mode")) not in ("ogren", "manuel"):
        q["bw_auto_link_mode"] = PLAN_DEFAULTS["bw_auto_link_mode"]
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

# --- dil: sunucu tarafi metinler (login sayfasi ve mailler) ---
EN_METIN = {
    "Giriş yap": "Sign in", "Kullanıcı": "Username", "Şifre": "Password",
    "Doğrulama kodu": "Verification code", "resimdeki 5 karakter": "the 5 characters shown",
    "Beni hatırla": "Remember me", "Yeni kod": "New code",
    "Devam etmek için giriş yap": "Sign in to continue",
    "Kullanıcı adı veya şifre hatalı.": "Incorrect username or password.",
    "Doğrulama kodu hatalı.": "Verification code is incorrect.",
    "İşaretlemezsen oturum hareketsiz kalınca sona erer.":
        "If unchecked, the session ends after a period of inactivity.",
    "Çok fazla hatalı denemede adresin geçici olarak kilitlenir.":
        "Too many failed attempts temporarily locks your address.",
    "OZET": "SUMMARY", "YAPILANDIRMA": "CONFIGURATION", "DRIVE DURUMU": "DRIVE STATUS",
    "EN YENI YEDEKLER": "NEWEST BACKUPS", "COP KUTUSUNDA BEKLEYEN": "WAITING IN TRASH",
    "VM/CT BAZINDA SON YEDEK": "LAST BACKUP PER VM/CT", "UYARILAR": "WARNINGS",
  "Bu mail otomatik olarak gonderildi.": "This message was sent automatically.",
    "HAFTALIK YEDEK RAPORU": "WEEKLY BACKUP REPORT", "CALISMA": "RUNS", "DRIVE": "DRIVE",
    "Zaman": "Time", "Tetikleyen": "Triggered by", "Sure": "Duration",
    "Yuklenen": "Uploaded", "Cope tasinan": "Moved to trash",
    "Kalici silinen": "Permanently deleted", "Cope giden": "Moved to trash",
    "Toplam": "Total", "Son calisma": "Last run", "Son ozet": "Last summary",
    "Program": "Schedule", "Sonraki": "Next", "Saklama": "Retention",
    "Cop suresi": "Trash period", "Hiz siniri": "Speed limit", "Yedek": "Backups",
    "Copte bekleyen": "Waiting in trash", "Kota": "Quota", "Kaynak": "Source",
    "Hedef": "Target", "Plan": "Plan", "Donem": "Period", "Log": "Log",
    "Sonraki rapor": "Next report", "Uretim zamani": "Generated at",
    "Yok.": "None.", "basarili": "successful", "hata": "failed", "atlandi": "skipped",
    "her gun": "every day", "gun": "days", "dosya": "files", "set": "sets",
    "es zamanli": "concurrent", "gun once": "days ago", "kaldi": "left",
    "izlenmiyor": "not tracked", "kapali": "disabled",
    "VM/CT basina en az": "at least", "gunden eski setler": "sets older than",
    "copte": "in trash", "bekleyenler": "waiting",
    "Haftalik rapor": "Weekly report", "SAKLAMA SURESINDEN ESKI": "OLDER THAN RETENTION",
    "(Drive listelenemedi)": "(could not list Drive)",
    "Kaynakta olup Drive'da olmayan": "In source but not on Drive",
    "Su VM/CT'ler henuz Drive'a cikmadi": "These VM/CTs have not reached Drive yet",
    "Yukleme basarisiz oldugu icin RETENTION CALISTIRILMADI.":
        "RETENTION WAS NOT RUN because the upload failed.",
    "Hicbir yedek silinmedi; sorun giderilince kendiliginden devam eder.":
        "No backups were deleted; it resumes automatically once fixed.",
    "Calisma HATA ile bitti - log dosyasina bak.": "The run FAILED - check the log file.",
    "Retention atlandi, eski yedekler birikmeye devam ediyor.":
        "Retention was skipped; old backups keep accumulating.",
    "Son calisma HATA ile bitti.": "The last run FAILED.",
    "Saklama suresinden eski yedegi olanlar":
        "VM/CTs whose newest backup is older than the retention period",
    "zamanlanmis": "scheduled", "manuel": "manual", "sn": "s", "saat": "at",
    "bos": "free", "dk": "min", "hazir": "ready", "yok": "none",
    "gunden eski setler": "days old or older", "copte": "in trash",
    "bekleyenler": "waiting", "uyari": "warning",
    "TEST": "TEST", "Bu bir test mailidir.": "This is a test mail.",
    "Profil": "Profile", "Sunucu": "Server", "Gonderen": "Sender",
}

def dil_tr(): return str(cfg().get("dil") or "tr").lower() != "en"

_EN_SIRALI = None

def metni_cevir(metin):
    """Uretilmis bir metin blogunu (mail govdesi, login sayfasi) Ingilizceye cevirir.

    50 cagri noktasini tek tek sarmak yerine tek gecis yapilir: anahtarlar uzundan
    kisaya uygulanir, tek sozcukluk anahtarlar sozcuk siniriyla eslesir ki
    'gun' -> 'days' donusumu 'gunluk' icinde patlamasin."""
    global _EN_SIRALI
    if dil_tr() or not metin: return metin
    if _EN_SIRALI is None:
        _EN_SIRALI = sorted(EN_METIN.items(), key=lambda kv: -len(kv[0]))
    for tr, en in _EN_SIRALI:
        if " " in tr or not tr.isalpha():
            metin = metin.replace(tr, en)
        else:
            metin = re.sub(r"(?<![0-9A-Za-zğüşıöçĞÜŞİÖÇ])" + re.escape(tr)
                           + r"(?![0-9A-Za-zğüşıöçĞÜŞİÖÇ])", en, metin)
    # Turkce "%12.3" -> Ingilizce "12.3%"
    metin = re.sub(r"%(\d+(?:\.\d+)?)", r"\1%", metin)
    return hizala(metin)

RE_ETIKET = re.compile(r"^(  )([A-Za-z][A-Za-z /]{0,24}?)\s*: (.*)$")

def hizala(metin, genislik=19):
    """Ceviri sonrasi 'Etiket : deger' satirlarinin iki noktasini hizalar.
    Ingilizce etiketler farkli uzunlukta oldugu icin hizalama kayiyordu."""
    cikti = []
    for satir in metin.split("\n"):
        m = RE_ETIKET.match(satir)
        cikti.append(f"  {m.group(2).strip():<{genislik}}: {m.group(3)}" if m else satir)
    return "\n".join(cikti)

def M(s):
    """Sunucu tarafi metni gecerli dile cevirir; karsiligi yoksa Turkce kalir."""
    return s if dil_tr() else EN_METIN.get(s, s)

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


# ---------- SERVIS IZLEME ----------
# Arac kendini izlemiyordu: pve-gdrive-ui coker ya da tick timer durursa
# kimse haberdar olmuyor, yedek alinmadigi ancak haftalik raporda (o da
# gidebilirse) fark ediliyordu. Iki katman:
#   1) systemd OnFailure= -> birim cokunce hemen mail
#   2) her tick "yasiyorum" damgasi birakir; damga eskirse arayuz ve rapor uyarir

# ---------- BUTUNLUK ----------
# "Bu betigi kimse degistiremez di mi?" sorusunun tam cevabi: dosya izinleri
# yalnizca root'a acik, ama root olan degistirebilir. O yuzden ikinci katman:
# betigin ozeti saklanir ve her tick'te karsilastirilir. Beklenmedik bir
# degisiklik sessiz kalmaz.

def betik_ozeti(yol=None):
    try:
        with open(yol or betik_yolu(), "rb") as f:
            return hashlib.sha256(f.read()).hexdigest()
    except Exception as e:
        yut("betik_ozeti", e); return ""

def butunluk_sabitle(sebep="elle"):
    oz = betik_ozeti()
    if not oz: return {"ok": False, "msg": "betik okunamadi"}
    put_state_root({"betik_sha256": oz, "betik_sha_zaman": now_str(),
                    "betik_sha_sebep": sebep, "betik_yolu": betik_yolu()})
    log(f"butunluk referansi guncellendi ({sebep}): {oz[:16]}…")
    return {"ok": True, "msg": f"referans alindi: {oz[:16]}…", "sha256": oz}

def butunluk_kontrol():
    """(durum, mesaj). durum: iyi | DEGISTI | referans-yok | okunamadi"""
    st = read_state()
    beklenen = str(st.get("betik_sha256") or "")
    simdiki = betik_ozeti()
    if not simdiki: return "okunamadi", M("Betik okunamadi.")
    if not beklenen: return "referans-yok", M("Butunluk referansi henuz alinmadi.")
    if simdiki == beklenen: return "iyi", ""
    return "DEGISTI", (M("Calisan betik degismis. Beklenen {b}…, bulunan {s}…. "
                         "Guncelleme yaptiysan normaldir; yapmadiysan INCELE.")
                       .replace("{b}", beklenen[:12]).replace("{s}", simdiki[:12]))

def butunluk_izle():
    """Her tick'te calisir. Degisiklik yalnizca BIR KEZ bildirilir; her turda
    mail yagmasin diye bildirilen ozet kaydedilir."""
    durum, mesaj = butunluk_kontrol()
    if durum == "referans-yok":
        butunluk_sabitle("ilk calisma"); return
    if durum != "DEGISTI": return
    st = read_state()
    simdiki = betik_ozeti()
    if st.get("betik_sha_bildirilen") == simdiki: return      # zaten haber verildi
    put_state_root({"betik_sha_bildirilen": simdiki})
    log(f"GUVENLIK: {mesaj}")
    C = cfg()
    alici = C.get("failure_mail_to") or next(
        (p.get("mail_to") for p in C.get("plans", []) if p.get("mail_to")), "")
    if C.get("butunluk_mail", True) and alici:
        govde = "\n".join([
            "[HATA] Calisan betik degismis", "=" * 58, "",
            "OZET",
            f"  Dosya        : {betik_yolu()}",
            f"  Beklenen     : {st.get('betik_sha256', '')}",
            f"  Bulunan      : {simdiki}",
            f"  Referans     : {st.get('betik_sha_zaman', '-')} ({st.get('betik_sha_sebep', '-')})",
            f"  Sunucu       : {os.uname().nodename}",
            f"  Zaman        : {now_str()}", "",
            "UYARILAR",
            "  ! Guncelleme yaptiysan bu normaldir; referansi yenile:",
            "      pve-gdrive butunluk --sabitle",
            "  ! Guncelleme YAPMADIYSAN dosyayi ve erisim kayitlarini incele.", "",
            f"Log: {C.get('log_file')}",
        ])
        send_mail(alici, f"[Proxmox Yedek] BUTUNLUK UYARISI - {os.uname().nodename}",
                  metni_cevir(govde), C.get("failure_smtp_profile"), durum="HATA")
    tg_gonder(tg_metin("BUTUNLUK UYARISI", [
        f"Sunucu  : {os.uname().nodename}", f"Dosya   : {betik_yolu()}",
        f"Beklenen: {st.get('betik_sha256', '')[:24]}…",
        f"Bulunan : {simdiki[:24]}…", "",
        "Guncelleme yaptiysan normaldir.",
        "Yapmadiysan dosyayi ve erisim kayitlarini incele."], "HATA"))

def tick_damgasi_yaz():
    put_state_root({"last_tick": now_str(), "last_tick_epoch": int(time.time())})

def tick_yasi_dk():
    """Son tick'in uzerinden kac dakika gecti? Hic calismadiysa None."""
    try:
        e = float(read_state().get("last_tick_epoch") or 0)
    except Exception as ex:
        yut("tick_yasi_dk", ex); return None
    if not e: return None
    return max(0.0, (time.time() - e) / 60.0)

def tick_sagligi():
    """(durum, mesaj). durum: iyi | gecikmis | bilinmiyor"""
    esik = float(cfg().get("tick_uyari_dk") or 20)
    yas = tick_yasi_dk()
    if yas is None:
        return "bilinmiyor", M("Zamanlayici henuz hic calismadi.")
    if yas > esik:
        return "gecikmis", (M("Zamanlayici {d} dakikadir calismadi (esik {e} dk). "
                              "pve-gdrive-tick.timer duruyor olabilir.")
                            .replace("{d}", str(int(yas))).replace("{e}", str(int(esik))))
    return "iyi", ""

def birim_bildir(birim):
    """systemd OnFailure= bunu cagirir. Coken birimin son gunlugunu maile koyar."""
    C = cfg()
    if not C.get("failure_mail", True):
        log(f"birim hatasi bildirimi kapali: {birim}"); return 1
    satirlar = []
    try:
        r = subprocess.run(["journalctl", "-u", birim, "-n",
                            str(int(C.get("failure_mail_lines") or 40)),
                            "--no-pager", "-o", "short-iso"],
                           capture_output=True, text=True, timeout=30)
        satirlar = (r.stdout or r.stderr or "").strip().split("\n")
    except Exception as e:
        satirlar = [f"journalctl okunamadi: {e}"]
    try:
        d = subprocess.run(["systemctl", "show", birim, "-p",
                            "Result,ExecMainStatus,NRestarts,ActiveState"],
                           capture_output=True, text=True, timeout=15).stdout.strip()
    except Exception as e:
        d = f"systemctl okunamadi: {e}"

    govde = "\n".join([
        f"[HATA] Servis birimi coktu - {birim}",
        "=" * 58, "",
        "OZET",
        f"  Birim        : {birim}",
        f"  Zaman        : {now_str()}",
        f"  Sunucu       : {os.uname().nodename}",
        "",
        "SYSTEMD DURUMU",
    ] + [f"  {x}" for x in d.split("\n") if x.strip()] + [
        "",
        "SON GUNLUK",
    ] + [f"  {x}" for x in satirlar[-int(C.get("failure_mail_lines") or 40):]] + [
        "",
        "UYARILAR",
        "  ! Bu birim calismadigi surece yedek alinmiyor olabilir.",
        "",
        f"Log: {C.get('log_file')}",
    ])
    # Alici: acikca verilmisse o, yoksa ilk planin adresi
    alici = C.get("failure_mail_to") or ""
    profil = C.get("failure_smtp_profile") or ""
    if not alici:
        for pl in C.get("plans", []):
            if pl.get("mail_to"):
                alici = pl["mail_to"]; profil = profil or pl.get("smtp_profile"); break
    if not alici:
        log(f"birim hatasi ({birim}) - bildirilecek mail adresi yok"); return 1
    konu = f"[Proxmox Yedek] SERVIS HATASI - {birim}"
    ok = send_mail(alici, konu, metni_cevir(govde), profil, durum="HATA")
    tg_gonder(tg_metin(f"SERVIS HATASI — {birim}",
                       [f"Sunucu: {os.uname().nodename}", f"Zaman : {now_str()}", "",
                        *satirlar[-8:]], "HATA"))
    log(f"birim hatasi bildirildi: {birim} -> {alici}" if ok
        else f"birim hatasi bildirilemedi: {birim}")
    return 0 if ok else 1

# ---------- HOST YAPILANDIRMA YEDEGI ----------
# vzdump yalnizca disk yedegi alir. Host olursa elinde diskler olur ama onlari
# NEREYE geri yukleyecegini anlatan hicbir sey olmaz: storage.cfg yok, ag
# yapilandirmasi yok, hangi CT hangi koprude belli degil. /etc/pve 37 KB;
# gunluk 53 GB'in yaninda bedava.
#
# Ozel anahtarlar disarida birakilir (varsayilan): /etc/pve/priv altindaki
# kume CA anahtari ve authkey.key sifresiz Drive'a cikmamali. authorized_keys
# ve known_hosts ACIK anahtar dosyalaridir, sizmasi erisim vermez ve geri
# yuklemede SSH erisimini kurtarir - bu yuzden dahildir.

RE_HOSTCONF = re.compile(r"^pve-config-(.+?)-(\d{4}_\d{2}_\d{2}-\d{2}_\d{2}_\d{2})"
                         r"\.(tar\.gz|json)$")

def hostconf_ayar(p, anahtar):
    """Plan degeri yoksa genel varsayilana duser."""
    v = p.get(anahtar)
    if v in (None, "", []): return GLOBAL_DEFAULTS.get(anahtar)
    return v

def _hostconf_dahil(rel, disla, priv_izin):
    """Dislama kurali iki katmanli.

    1) priv/ ICI VARSAYILAN OLARAK YASAK. Yalnizca acikca izin verilen dosyalar
       (acik anahtar listeleri) gecer. Boylece Proxmox yarin oraya yeni bir sir
       koyarsa kural guncellenmese bile disarida kalir - guvenlikte varsayilan
       "izin ver" olmamali.
    2) Ayrica desen listesi uygulanir; desenler yolun herhangi bir sonekiyle
       eslesebilir, boylece kok dizin farkli olsa da kural tutar.
    """
    parcalar = rel.split("/")
    if "priv" in parcalar:
        return os.path.basename(rel) in priv_izin and parcalar[-2:-1] == ["priv"]
    ad = os.path.basename(rel)
    for k in disla:
        if fnmatch.fnmatch(ad, k) or fnmatch.fnmatch(rel, k) or fnmatch.fnmatch(rel, "*/" + k):
            return False
    return True

def hostconf_dosyalari(p):
    """Arsive girecek (mutlak_yol, arsiv_ici_yol) ciftleri. Okunamayan atlanir."""
    disla = hostconf_ayar(p, "host_config_exclude") or []
    priv_izin = set(hostconf_ayar(p, "host_config_priv_allow") or [])
    cikti, atlanan = [], []
    for kok in hostconf_ayar(p, "host_config_paths") or []:
        if not os.path.exists(kok): continue
        if os.path.isfile(kok):
            rel = kok.lstrip("/")
            if _hostconf_dahil(rel, disla, priv_izin): cikti.append((kok, rel))
            else: atlanan.append(rel)
            continue
        for dizin, altlar, adlar in os.walk(kok):
            # priv dizinine girmeyi engelleme: icinde izinli acik anahtar olabilir
            tutulan = []
            for d in altlar:
                tam_d = os.path.join(dizin, d)
                if d == "priv" or _hostconf_dahil(os.path.join(tam_d, "x").lstrip("/"),
                                                  disla, priv_izin):
                    tutulan.append(d)
                else:
                    # Atlanan dizin de rapora girsin: arsivi acan kisi neyin
                    # bilerek disarida birakildigini gormeli.
                    atlanan.append(tam_d.lstrip("/") + "/  (dizin)")
            altlar[:] = tutulan
            for ad in adlar:
                tam = os.path.join(dizin, ad); rel = tam.lstrip("/")
                if not os.path.isfile(tam): continue
                if not _hostconf_dahil(rel, disla, priv_izin): atlanan.append(rel); continue
                cikti.append((tam, rel))
    return sorted(cikti), sorted(atlanan)

def hostconf_json(p):
    """pvesh ile REST agacinin anlik goruntusu. Geri yukleme araci degil;
    'ne vardi, ne degisti' sorusunu gozle cevaplamak icin."""
    if not shutil.which("pvesh"): return None
    dugum = os.uname().nodename
    cikti = {"_uretim": now_str(), "_dugum": dugum, "_surum_araci": SURUM}
    for uc in hostconf_ayar(p, "host_config_pvesh") or []:
        yol = uc.replace("{node}", dugum)
        try:
            r = subprocess.run(["pvesh", "get", yol, "--output-format", "json"],
                               capture_output=True, text=True, timeout=30)
            cikti[yol] = json.loads(r.stdout) if r.returncode == 0 and r.stdout.strip() \
                         else {"_hata": (r.stderr or "bos yanit").strip()[:200]}
        except Exception as e:
            cikti[yol] = {"_hata": str(e)[:200]}
    return cikti

def hostconf_uret(p, hedef_dizin):
    """Yapilandirma arsivini ve JSON goruntusunu uretir. Uretilen yollari doner."""
    import tarfile
    dugum = os.uname().nodename
    damga = datetime.now().strftime(DT_FMT)
    uretilen = []

    dosyalar, atlanan = hostconf_dosyalari(p)
    if dosyalar:
        tar_yol = os.path.join(hedef_dizin, f"pve-config-{dugum}-{damga}.tar.gz")
        with tarfile.open(tar_yol, "w:gz") as t:
            for tam, rel in dosyalar:
                try: t.add(tam, arcname=rel, recursive=False)
                except Exception as e: yut("hostconf_tar", e)
            # Neyin neden disarida kaldigi arsivin icinde yazili olsun
            not_metni = ("Bu arsiv pve-gdrive-backup tarafindan uretildi.\n"
                         f"Uretim: {now_str()}  Dugum: {dugum}\n"
                         f"Dosya sayisi: {len(dosyalar)}\n\n"
                         "DISARIDA BIRAKILANLAR (ozel anahtarlar bilerek alinmaz):\n"
                         + ("".join(f"  {x}\n" for x in atlanan) or "  yok\n")
                         + "\nGeri yukleme icin: docs/GERI-YUKLEME.md\n")
            bilgi = tarfile.TarInfo("OKUBENI.txt")
            ham = not_metni.encode("utf-8")
            bilgi.size = len(ham); bilgi.mtime = int(time.time())
            t.addfile(bilgi, io.BytesIO(ham))
        os.chmod(tar_yol, 0o600)
        uretilen.append(tar_yol)

    if hostconf_ayar(p, "host_config_json"):
        veri = hostconf_json(p)
        if veri is not None:
            js_yol = os.path.join(hedef_dizin, f"pve-config-{dugum}-{damga}.json")
            with open(js_yol, "w", encoding="utf-8") as f:
                json.dump(veri, f, ensure_ascii=False, indent=1)
            os.chmod(js_yol, 0o600)
            uretilen.append(js_yol)
    return uretilen, len(dosyalar), atlanan

def hostconf_yukle(p):
    """Yapilandirmayi uretip Drive'a yukler. (yuklenen, dosya_sayisi, hata) doner.
    Bu adimin hatasi asla yedeklemeyi durdurmaz - ek bir guvence, on kosul degil."""
    if not p.get("host_config_enabled", GLOBAL_DEFAULTS["host_config_enabled"]):
        return 0, 0, ""
    gecici = tempfile.mkdtemp(prefix="pgd-conf-", dir=os.path.dirname(cfg()["state_file"]))
    try:
        uretilen, n, atlanan = hostconf_uret(p, gecici)
        if not uretilen:
            return 0, 0, "yapilandirma dosyasi bulunamadi"
        yuklendi = 0
        for yol in uretilen:
            ad = os.path.basename(yol)
            rc, _, e = rclone(["copyto", yol, f"{p['remote']}/{ad}"])
            if rc == 0:
                yuklendi += 1
                log(f"host yapilandirmasi yuklendi: {ad} ({n} dosya, "
                    f"{os.path.getsize(yol)} bayt)", p["id"])
            else:
                log(f"host yapilandirmasi yuklenemedi {ad}: {(e or '').strip()[:200]}", p["id"])
        if atlanan:
            log(f"yapilandirmada {len(atlanan)} gizli dosya bilerek atlandi", p["id"])
        return yuklendi, n, ""
    except Exception as e:
        log(f"host yapilandirma yedegi HATA: {e}", p["id"])
        return 0, 0, str(e)
    finally:
        shutil.rmtree(gecici, ignore_errors=True)

def hostconf_prune(p, files=None):
    """Yapilandirma arsivleri vzdump kalibina uymaz, collect_sets onlari gormez.
    Kendi saklama kurallari var: gun siniri ayni, taban ayri (kucukler, cok tutulur)."""
    keep_days = int(p["keep_days"])
    taban = int(hostconf_ayar(p, "host_config_keep_count") or 0)
    cutoff = time.time() - keep_days * 86400
    kayitlar = []
    for f in (lsjson(p["remote"]) if files is None else files):
        if f.get("IsDir"): continue
        m = RE_HOSTCONF.match(f.get("Name", ""))
        if not m: continue
        try: epoch = datetime.strptime(m.group(2), DT_FMT).timestamp()
        except Exception: continue
        kayitlar.append({"name": f["Name"], "epoch": epoch, "size": f.get("Size", 0)})
    kayitlar.sort(key=lambda x: x["epoch"], reverse=True)
    tasinan = 0
    for i, k in enumerate(kayitlar):
        if i < taban: continue
        if k["epoch"] >= cutoff: continue
        rc, _, e = rclone(["deletefile", f"{p['remote']}/{k['name']}"])
        if rc == 0:
            tasinan += 1
            log(f"eski yapilandirma copune tasindi: {k['name']}", p["id"])
        else:
            log(f"yapilandirma silinemedi {k['name']}: {(e or '').strip()[:120]}", p["id"])
    return tasinan

# ---------- OLAY YAYINI (SSE) ----------
# Arayuz onceden birkac saniyede bir /api/status cekiyordu: degisiklik ile
# ekranda gorunmesi arasinda o kadar gecikme vardi ve hicbir sey olmasa bile
# istek gidiyordu. Artik sunucu degisikligi kendisi itiyor.
#
# WebSocket yerine SSE: sunucu saf stdlib http.server, WebSocket el sikismasini
# ve cerceve cozmeyi elle yazmak gerekirdi. Ihtiyac tek yonlu (sunucu -> tarayici),
# EventSource kendiliginden yeniden baglaniyor ve ters vekilden sorunsuz geciyor.

class OlayYayini:
    """Abone basina sinirli kuyruk. Yavas bir istemci bellegi sisiremez:
    kuyrugu dolan abonenin en eski olayi dusurulur."""

    def __init__(self, kuyruk_max=200, abone_max=16):
        self.kilit = threading.Lock()
        self.aboneler = {}          # id -> {"q": deque, "olay": Event}
        self.sira = 0
        self.kuyruk_max = kuyruk_max
        self.abone_max = abone_max

    def abone_ol(self):
        with self.kilit:
            if len(self.aboneler) >= self.abone_max: return None
            self.sira += 1
            no = self.sira
            self.aboneler[no] = {"q": deque(maxlen=self.kuyruk_max), "olay": threading.Event()}
            return no

    def ayril(self, no):
        with self.kilit: self.aboneler.pop(no, None)

    def yayinla(self, tur, veri):
        paket = (tur, veri)
        with self.kilit:
            for a in self.aboneler.values():
                a["q"].append(paket); a["olay"].set()

    def bekle(self, no, zaman_asimi):
        """Sirada olay varsa hemen, yoksa zaman_asimi kadar bekleyip dondurur."""
        with self.kilit:
            a = self.aboneler.get(no)
            if not a: return None
            if a["q"]: return a["q"].popleft()
            a["olay"].clear()
        a["olay"].wait(zaman_asimi)
        with self.kilit:
            a = self.aboneler.get(no)
            if not a: return None
            return a["q"].popleft() if a["q"] else None

    def abone_sayisi(self):
        with self.kilit: return len(self.aboneler)

OLAY = OlayYayini()

def olay_yolla(tur, veri):
    """Yayin hicbir zaman cagiran isi bozmaz; hata yutulur."""
    try: OLAY.yayinla(tur, veri)
    except Exception as e: yut("olay_yolla", e)

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

def put_state_root(patch):
    """Plan disi, kok duzeyindeki durum alanlari (ornegin son tick damgasi)."""
    st = read_state(); st.update(patch); st["updated"] = now_str()
    write_state(st)

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
RCLONE_CONF = os.environ.get("RCLONE_CONFIG") or "/var/lib/pve-gdrive/rclone.conf"

def rclone_ortam():
    """rclone her zaman AYNI yapilandirmayi gorsun.

    Servis birimi ProtectHome kullandigi icin /root altindaki varsayilan config
    servise gorunmuyordu: hesaplar 'yok' saniliyor, yeni hesap gecici bir ad
    alanina yazilip kayboluyordu. Config yolu artik acikca verilir."""
    ort = dict(os.environ)
    if os.path.exists(RCLONE_CONF) or os.environ.get("RCLONE_CONFIG"):
        ort["RCLONE_CONFIG"] = RCLONE_CONF
    return ort

def rclone(args, timeout=None):
    """Kisa ciktili komutlar icin (lsjson, about, delete). Ciktiyi tam yakalar."""
    try:
        r = subprocess.run(["rclone"] + args, capture_output=True, text=True,
                           timeout=timeout, env=rclone_ortam())
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
                              stderr=subprocess.STDOUT, text=True, bufsize=1,
                              env=rclone_ortam())
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
    return [f"  Saklama      : {p['keep_days']} gun, VM/CT basina en az {p['keep_count']} set",
            f"  Cop suresi   : {p['drive_trash_days']} gun"]

def _bolum_misafirler(p, gs, esik_gun=None):
    """(satirlar, eski_misafirler). gs None ise Drive listelenememis demektir."""
    if gs is None: return ["  (Drive listelenemedi)"], []
    esik = int(esik_gun if esik_gun is not None else p["keep_days"])
    simdi = time.time(); satirlar = []; eski = []
    for g in gs:
        # Negatif yas olmasin: dosya adindaki tarih ileri olabilir (saat farki,
        # elle kopyalanmis dosya). "-0.8 gun once" anlamsiz bir cikti olurdu.
        yas = max(0.0, (simdi - g["last"]) / 86400) if g["last"] else None
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
    """VM/CT basina son yedek zamani ve set sayisi. Raporun en degerli kismi:
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

def kaynak_analiz(src_dir):
    """Kaynak klasoru olcer: gunluk uretim, set sayisi, misafir dagilimi.

    Saklama suresini tahminle degil olcumle secebilmek icin. Proxmox her gun
    tam yedek uretir; gunluk ortalama = toplam / farkli tarih sayisi."""
    try: dosyalar = os.listdir(src_dir)
    except Exception as e:
        yut("kaynak_analiz", e)
        return {"ok": False, "hata": f"klasor okunamadi: {src_dir}"}
    re_d = dump_re()
    gun = {}; misafir = {}; gun_set = {}; toplam = 0; n = 0
    for f in dosyalar:
        m = re_d.match(f)
        if not m: continue
        try: b = os.path.getsize(os.path.join(src_dir, f))
        except Exception as e:
            yut("kaynak_analiz", e); continue
        tarih = m.group(4).split("-")[0]          # 2026_08_07
        ad = f"{m.group(2)}-{m.group(3)}"
        gun[tarih] = gun.get(tarih, 0) + b
        misafir[ad] = misafir.get(ad, 0) + b
        gun_set.setdefault(tarih, set()).add(ad)
        toplam += b; n += 1
    if not gun:
        return {"ok": False, "hata": "bu klasorde taninan vzdump dosyasi yok"}
    set_sayisi = len(gun)
    gunluk = toplam / set_sayisi
    return {"ok": True, "dosya": n, "set_sayisi": set_sayisi, "toplam": toplam,
            "gunluk": gunluk,
            "gunler": [{"tarih": t.replace("_", "-"), "boyut": gun[t],
                        "misafir": len(gun_set[t])} for t in sorted(gun)],
            "misafirler": sorted(
                [{"ad": k, "toplam": v, "set_basina": v / set_sayisi,
                  "pay": round(v / toplam * 100, 1)} for k, v in misafir.items()],
                key=lambda x: -x["toplam"])}

def saklama_projeksiyon(analiz, kota, keep_days, drive_trash_days=1):
    """Verilen saklama suresi icin gereken alan ve kotaya orani.

    Kota bilinmiyorsa 'sigar' None doner: bilinmeyeni 'sigmaz' diye raporlamak
    yanlis alarm uretir, bu bilgi eksikliginden daha kotudur."""
    if not analiz.get("ok"): return None
    gunluk = analiz["gunluk"]
    # Drive'da keep_days gun + copte drive_trash_days gun daha bekler
    gereken = gunluk * (float(keep_days) + float(drive_trash_days))
    bos = float(kota.get("free") or 0)
    toplam = float(kota.get("total") or 0)
    kullanilan = float(kota.get("used") or 0)
    kota_var = bool(toplam) and kota.get("ok") is not False
    return {"gereken": gereken, "bos": bos, "kota_var": kota_var,
            "sonra_kullanilan": kullanilan + gereken,
            "sonra_pct": round((kullanilan + gereken) / toplam * 100, 1) if toplam else None,
            "sigar": (gereken < bos) if kota_var else None,
            "ilk_yukleme": min(analiz["toplam"], gereken)}

def saklama_oneri(analiz, kota, drive_trash_days=1, pay_pct=None):
    """Bos alanin guvenli bir kismina sigan en uzun saklama suresi."""
    if not analiz.get("ok"): return None
    pay = float(pay_pct if pay_pct is not None else (cfg().get("oneri_pay_pct") or 60)) / 100.0
    gunluk = analiz["gunluk"]
    if gunluk <= 0: return None
    butce = float(kota.get("free") or 0) * pay
    gun = int(butce / gunluk - float(drive_trash_days))
    return max(1, min(365, gun))

def local_guests(p):
    """Kaynak klasordeki VM/CT'ler - Drive'a hic cikmamis olan var mi diye."""
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
    L += ["VM/CT BAZINDA SON YEDEK"] + misafir_satirlari
    for g in eski:
        uyari.append(f"{g} icin en yeni yedek saklama suresinden eski - yedegi aliniyor mu?")
    if gs is not None:
        eksik = sorted(local_guests(p) - {g["guest"] for g in gs})
        if eksik:
            L.append(f"  Kaynakta olup Drive'da olmayan: {', '.join(eksik)}")
            uyari.append("Su VM/CT'ler henuz Drive'a cikmadi: " + ", ".join(eksik))
    L.append("")

    tick_durum, tick_mesaj = tick_sagligi()
    if tick_durum != "iyi": uyari.append(tick_mesaj)
    bt_durum, bt_mesaj = butunluk_kontrol()
    if bt_durum == "DEGISTI": uyari.append(bt_mesaj)
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
    konu = (f"[Proxmox Backup] Weekly report - {p['name']}" if not dil_tr()
            else f"[Proxmox Yedek] Haftalik rapor - {p['name']}")
    konu += (f" ({n} " + ("warning" if not dil_tr() else "uyari") + ")") if n else ""
    ok = send_mail(to, konu, metni_cevir(body), p.get("smtp_profile"),
                   durum="HATA" if n else "basarili")
    tg_gonder(tg_metin(f"Haftalik rapor — {p['name']}",
                       [x for x in body.split("\n")[:22]],
                       "HATA" if n else "basarili"), p)
    if ok:
        put_pstate(p["id"], {"last_report": now_str(), "last_report_warn": n})
        log(f"haftalik rapor gonderildi ({n} uyari) -> {to}", p["id"])
    return ok

# ---------- CEKIRDEK ----------
# ---------- COKLU HEDEF ----------
# Bir plan birden fazla hedefe sahip olabilir: birincisi calismazsa sonraki
# denenir. Kritik kural, projenin en onemli guvenlik kuralinin uzantisidir:
# RETENTION YALNIZCA YUKLEMENIN GERCEKTEN BASARILI OLDUGU HEDEFTE CALISIR.
# Aksi halde yedek hesaba dustugumuz gun, birincideki eski yedekler silinir.

def plan_hedefleri(p):
    """Sirasiyla denenecek hedefler. Birincisi p['remote'], sonrakiler yedek."""
    liste = [str(p.get("remote") or "").strip()]
    for h in (p.get("yedek_hedefler") or []):
        h = str(h or "").strip()
        if h and h not in liste: liste.append(h)
    return [h for h in liste if h]

def plan_hedefle(p, hedef):
    """Aktif hedefe gore plan kopyasi.

    Alt fonksiyonlarin tamami p['remote'] okuyor. Her birine ayri hedef
    parametresi eklemek yerine plani kopyalayip remote'u degistiriyoruz:
    boylece 'yanlislikla baska hedefe dokunma' ihtimali kalmiyor."""
    q = dict(p); q["remote"] = hedef
    return q

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

SANAL_ONEK = ("tap", "veth", "fwbr", "fwln", "fwpr", "docker", "lo", "vnet")

def kopru_mu(iface):
    return os.path.isdir(f"/sys/class/net/{iface}/bridge")

def kopru_uplink(iface):
    """Bir koprunun disariya cikan uyesini bulur.

    Neden gerekli: Proxmox'ta varsayilan rota vmbr0 gibi bir kopruden gecer.
    Koprunun sayaclari VM'ler ARASI yerel trafigi de sayar - o trafik hic
    internete cikmaz, bizim yukleme hizimizla yarismaz. Koprunun altindaki
    bond/fiziksel arayuz ise tam dogru olcumu verir: VM'lerin internet trafigi
    dahil, VM<->VM trafigi haric.
    """
    try: uyeler = sorted(os.listdir(f"/sys/class/net/{iface}/brif"))
    except Exception: return ""
    aday = [u for u in uyeler if not u.startswith(SANAL_ONEK)]
    if not aday: return ""
    # bond once: birden fazla fiziksel bagi tek noktada topluyor
    for u in aday:
        if os.path.isdir(f"/sys/class/net/{u}/bonding"): return u
    for u in aday:
        if os.path.exists(f"/sys/class/net/{u}/device"): return u
    return aday[0]

def wan_iface(secili=""):
    """Olculecek arayuz. Kullanici secmediyse varsayilan rotanin arayuzu,
    o bir kopruyse altindaki uplink. (arayuz, nasil_secildi) doner."""
    if secili: return secili, "elle secildi"
    ana = default_iface()
    if not ana: return "", "bulunamadi"
    if kopru_mu(ana):
        alt = kopru_uplink(ana)
        if alt: return alt, f"{ana} koprusunun uplink'i"
        return ana, f"{ana} koprusu (uplink uyesi bulunamadi)"
    return ana, "varsayilan rota"

def iface_link_mbit(iface):
    """Arayuzun bildirdigi bag hizi (Mbit). Koprulerde uydurmadir; yalnizca
    bilgi amacli gosterilir, hicbir hesapta kullanilmaz."""
    try:
        with open(f"/sys/class/net/{iface}/speed") as f: return int(f.read().strip())
    except Exception: return 0

def tx_bytes(iface):
    return net_ifaces().get(iface, (0, 0))[1]

def bw_ogrenilen_oku(pid):
    """Daha once olculmus en yuksek surekli yukleme hizi (bayt/sn)."""
    try: return float(pstate(read_state(), pid).get("bw_olculen") or 0)
    except Exception as e:
        yut("bw_ogrenilen_oku", e); return 0.0

def bw_ogrenilen_yaz(pid, deger):
    put_pstate(pid, {"bw_olculen": int(deger), "bw_olculen_zaman": now_str()})

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
    iface, nasil = wan_iface(p.get("bw_auto_iface"))
    if not iface:
        log("otomatik bant genisligi: ag arayuzu bulunamadi, sabit sinir kullanilacak", pid); return
    aralik = max(2, int(p.get("bw_auto_interval_sec") or 10))
    taban = bw_bytes(p.get("bw_auto_min")) or bw_bytes("1M")

    # Hat kapasitesi: elle girilen deger bir TAHMIN. Arayuzun link hizi da
    # internet yukleme hizini gostermez (1 Gbit LAN'in arkasinda 60 Mbit ISS
    # olabilir). Ogrenme kipinde daha once fiilen olculmus en yuksek surekli
    # hizi taban aliriz - olculmus bir alt sinir, uydurulmus bir ust sinirdan
    # iyidir.
    elle = bw_bytes(p.get("bw_auto_link"))
    ogrenilen = bw_ogrenilen_oku(pid)
    kip = str(p.get("bw_auto_link_mode") or "ogren")
    if kip == "manuel" or not ogrenilen:
        link = elle or ogrenilen or bw_bytes("100M")
        kaynak = "elle" if elle and kip == "manuel" else ("olculen" if ogrenilen else "varsayilan")
    else:
        link = ogrenilen
        kaynak = "olculen"
    tavan = bw_bytes(p.get("bw_auto_max")) or bw_bytes(p.get("bwlimit")) or link
    pay = max(0.0, 1.0 - float(p.get("bw_auto_reserve_pct") or 0) / 100.0)
    log(f"otomatik bant genisligi acik: arayuz={iface} ({nasil}) "
        f"hat={bw_str(link)} ({kaynak}) taban={bw_str(taban)} tavan={bw_str(tavan)} "
        f"pay=%{int(pay*100)}", pid)
    alfa = float(p.get("bw_auto_smooth") or 0.4)
    adim = float(p.get("bw_auto_step_pct") or 25) / 100.0
    onceki = tx_bytes(iface); son_hedef = 0
    onceki_done = float((get_progress(pid) or {}).get("done") or 0)
    diger_ema = None
    tepe = 0.0            # bu kosuda gorulen en yuksek kendi hizimiz
    tepe_ornek = 0        # kac olcumde tepeye yakin kalindi (tek sicrama yanıltmasin)
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
        # Hat kapasitesini ogren: yalnizca kendi sinirimiza dayanmadigimiz
        # anlarda olcum anlamli. Sinira dayaniyorsak gordugumuz hiz hattin
        # degil, kendi kisitimizin sonucudur.
        if kip != "manuel" and bizim > tepe and (not son_hedef or bizim < son_hedef * 0.95):
            tepe = bizim; tepe_ornek = 1
        elif tepe and bizim >= tepe * 0.85:
            tepe_ornek += 1

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

    # Kosu bitti: ogrenilen kapasiteyi kalici yaz. Tek bir sicrama yeterli
    # degil (>=2 olcum) ve yalnizca oncekinden anlamli olcude buyukse
    # guncellenir; boylece yavas bir gun kapasiteyi kalici dusurmez.
    if kip != "manuel" and tepe > 0 and tepe_ornek >= 2:
        eski = bw_ogrenilen_oku(pid)
        if tepe > eski * 1.05 or not eski:
            bw_ogrenilen_yaz(pid, tepe)
            log(f"olculen yukleme kapasitesi guncellendi: {bw_str(int(tepe))}/sn"
                + (f" (onceki {bw_str(int(eski))})" if eski else ""), pid)

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

# ---------- HTML MAIL SABLONU ----------
# Outlook (Word motoru) flexbox/grid/float bilmez: her sey tablo + satir ici stil.
# Duz metin govde kaynak olarak kalir, HTML ondan uretilir -> tek yerde bakim.
MAIL_RENK = {
    "basarili": ("#0f7b4f", "#e6f4ec", "#0f7b4f"),
    "HATA":     ("#b3261e", "#fdeceb", "#b3261e"),
    "atlandi":  ("#8a5a00", "#fdf3e0", "#8a5a00"),
    "_":        ("#1b4d7a", "#e8f0f8", "#1b4d7a"),
}
MAIL_YAZI = ("-apple-system,'Segoe UI',Roboto,Helvetica,Arial,sans-serif")
MAIL_MONO = ("'SFMono-Regular',Consolas,'Liberation Mono',Menlo,monospace")
RE_MAIL_ETIKET = re.compile(r"^[A-Za-z\u00c7\u011e\u0130\u00d6\u015e\u00dc\u00e7\u011f\u0131\u00f6\u015f\u00fc]"
                            r"[A-Za-z\u00c7\u011e\u0130\u00d6\u015e\u00dc\u00e7\u011f\u0131\u00f6\u015f\u00fc0-9 ./_()%-]*$")

def _mail_coz(satir):
    """Bir metin satirini (tip, sol, sag) olarak siniflar. HTML uretimi buna gore dallanir."""
    if not satir.strip(): return ("bosluk", "", "")
    govde = satir.lstrip(); girinti = len(satir) - len(govde); cikti = govde.rstrip()
    if len(set(cikti)) == 1 and cikti[0] in "=-_" and len(cikti) > 8: return ("cizgi", "", "")
    if cikti.startswith("! "): return ("uyari", cikti[2:], "")
    if ":" in cikti:
        anahtar, _, deger = cikti.partition(":")
        if RE_MAIL_ETIKET.match(anahtar.strip()):
            return ("satir", anahtar.strip(), deger.strip())
    if girinti == 0 and cikti == cikti.upper() and len(cikti) > 2: return ("baslik", cikti, "")
    return ("mono" if girinti else "metin", cikti, "")

def _mail_kutu(ic, renk):
    """Bolum kartlari. Outlook yuvarlak kose bilmez ama border-radius'u yok sayip devam eder."""
    return ('<table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0"'
            ' style="border-collapse:collapse;margin:0 0 14px 0;background:#ffffff;'
            f'border:1px solid #e2e8f0;border-left:3px solid {renk};border-radius:4px">'
            f"<tr><td style=\"padding:14px 18px\">{ic}</td></tr></table>")

def mail_html(govde, durum=None, konu=""):
    """Duz metin mail govdesini Outlook dostu HTML'e cevirir."""
    E = _html.escape
    ana, arka, kenar = MAIL_RENK.get(durum or "_", MAIL_RENK["_"])
    satirlar = govde.split("\n")
    baslik = satirlar[0].strip() if satirlar else konu
    for onek in ("[OK] ", "[HATA] ", "[ATLANDI] ", "[?] "):
        if baslik.startswith(onek): baslik = baslik[len(onek):]

    parcalar, tampon, bolum_adi = [], [], ""
    def bolumu_kapat():
        if not tampon: return
        ic = ""
        if bolum_adi:
            ic += (f'<div style="font:600 11px/1.4 {MAIL_YAZI};letter-spacing:.08em;'
                   f'text-transform:uppercase;color:{ana};padding-bottom:10px">{E(bolum_adi)}</div>')
        ic += ('<table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0"'
               ' style="border-collapse:collapse">' + "".join(tampon) + "</table>")
        parcalar.append(_mail_kutu(ic, kenar))
        tampon.clear()

    for ham in satirlar[1:]:
        tip, sol, sag = _mail_coz(ham)
        if tip == "baslik":
            bolumu_kapat(); bolum_adi = sol; continue
        if tip in ("bosluk", "cizgi"): continue
        if tip == "uyari":
            tampon.append('<tr><td colspan="2" style="padding:7px 10px;background:#fdeceb;'
                          'border-left:3px solid #b3261e;border-radius:3px;'
                          f'font:400 13px/1.5 {MAIL_YAZI};color:#7f1d1a">&#9888; {E(sol)}</td></tr>')
        elif tip == "satir":
            tampon.append(f'<tr><td style="padding:5px 12px 5px 0;font:400 13px/1.5 {MAIL_YAZI};'
                          'color:#64748b;white-space:nowrap;vertical-align:top;width:42%">'
                          f'{E(sol)}</td><td style="padding:5px 0;font:600 13px/1.5 {MAIL_YAZI};'
                          f'color:#1e293b;vertical-align:top">{E(sag) or "&#8211;"}</td></tr>')
        elif tip == "mono":
            tampon.append('<tr><td colspan="2" style="padding:3px 0;'
                          f'font:400 12px/1.5 {MAIL_MONO};color:#334155;white-space:nowrap">'
                          f'{E(sol)}</td></tr>')
        else:
            tampon.append('<tr><td colspan="2" style="padding:4px 0;'
                          f'font:400 13px/1.5 {MAIL_YAZI};color:#475569">{E(sol)}</td></tr>')
    bolumu_kapat()

    rozet = ""
    if durum:
        rozet = (f'<span style="display:inline-block;background:{arka};color:{ana};'
                 f'font:600 11px/1 {MAIL_YAZI};letter-spacing:.06em;text-transform:uppercase;'
                 f'padding:6px 10px;border-radius:3px">{E(M(durum))}</span>')
    return (
        '<!DOCTYPE html><html><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width,initial-scale=1">'
        '<meta name="x-apple-disable-message-reformatting">'
        '<!--[if mso]><style>table,td,div,p{font-family:Arial,sans-serif !important}</style><![endif]-->'
        f"<title>{E(konu or baslik)}</title></head>"
        '<body style="margin:0;padding:0;background:#eef2f6;-webkit-text-size-adjust:100%">'
        '<table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0"'
        ' style="border-collapse:collapse;background:#eef2f6"><tr>'
        '<td align="center" style="padding:24px 12px">'
        '<!--[if mso]><table role="presentation" width="600" cellpadding="0" cellspacing="0"'
        ' border="0"><tr><td><![endif]-->'
        '<table role="presentation" width="600" cellpadding="0" cellspacing="0" border="0"'
        ' style="border-collapse:collapse;width:100%;max-width:600px;text-align:left">'
        f'<tr><td style="padding:0 0 16px 0;border-top:3px solid {ana};background:#ffffff;'
        'border:1px solid #e2e8f0;border-radius:4px">'
        f'<div style="padding:16px 18px 0 18px">{rozet}</div>'
        f'<div style="padding:10px 18px 0 18px;font:600 19px/1.35 {MAIL_YAZI};color:#0f172a">'
        f"{E(baslik)}</div>"
        f'<div style="padding:6px 18px 14px 18px;font:400 12px/1.5 {MAIL_YAZI};color:#64748b">'
        f'Proxmox &#8594; Google Drive &#183; {E(now_str())}</div></td></tr>'
        '<tr><td style="height:14px;line-height:14px">&nbsp;</td></tr>'
        f"<tr><td>{''.join(parcalar)}</td></tr>"
        f'<tr><td style="padding:4px 2px 0 2px;font:400 11px/1.6 {MAIL_YAZI};color:#94a3b8">'
        f"pve-gdrive-backup v{SURUM} &#183; {E(M('Bu mail otomatik olarak gonderildi.'))}"
        "</td></tr></table>"
        '<!--[if mso]></td></tr></table><![endif]-->'
        "</td></tr></table></body></html>")

def send_mail(to, subject, body, profile=None, durum=None):
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
    try:
        msg.add_alternative(mail_html(body, durum, subject), subtype="html")
    except Exception as e:
        yut("mail_html", e)  # HTML uretilemezse duz metin yine gider
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

# ---------- TELEGRAM ----------
# Mail bazen gec gelir ya da spam'e duser; anlik bildirim icin Telegram.
# Ek bagimlilik yok: Bot API duz bir HTTPS cagrisi.
TG_API = "https://api.telegram.org"
RE_TG_TOKEN = re.compile(r"^\d{5,}:[A-Za-z0-9_-]{20,}$")

def tg_ayar(p=None):
    """(token, chat_id, acik). Plan kendi chat'ini verebilir; vermezse genel ayar."""
    C = cfg()
    token = str(C.get("telegram_token") or "").strip()
    chat = str((p or {}).get("telegram_chat_id") or C.get("telegram_chat_id") or "").strip()
    acik = bool(C.get("telegram_enabled")) and bool(token) and bool(chat)
    if p is not None and not p.get("telegram", True): acik = False
    return token, chat, acik

def tg_kisalt(metin, sinir=3900):
    """Telegram mesaj siniri 4096 karakter; ortadan kirpip iki ucu korur."""
    if len(metin) <= sinir: return metin
    bas = sinir // 2
    return metin[:bas] + "\n…\n" + metin[-(sinir - bas):]

def tg_gonder(metin, p=None, sessiz=False):
    """Telegram'a mesaj yollar. Hata ASLA cagiran isi bozmaz."""
    token, chat, acik = tg_ayar(p)
    if not acik: return False
    veri = urllib.parse.urlencode({
        "chat_id": chat, "text": tg_kisalt(metin),
        "parse_mode": "HTML", "disable_web_page_preview": "true",
        "disable_notification": "true" if sessiz else "false",
    }).encode()
    try:
        istek = urllib.request.Request(f"{TG_API}/bot{token}/sendMessage", data=veri,
                                       headers={"User-Agent": f"pve-gdrive/{SURUM}"})
        with urllib.request.urlopen(istek, timeout=20) as y:
            ok = json.loads(y.read().decode("utf-8", "replace")).get("ok", False)
        if ok: log("telegram bildirimi gonderildi", (p or {}).get("id"))
        else: log("telegram bildirimi reddedildi (ok=false)", (p or {}).get("id"))
        return bool(ok)
    except Exception as e:
        # Jeton loga DUSMEZ: hata metninde gecebilir, o yuzden maskeleriz
        log(f"telegram HATA: {str(e).replace(token, '<jeton>')[:200]}", (p or {}).get("id"))
        return False

def tg_test(chat=None):
    token, varsayilan, _ = tg_ayar()
    if not token: return {"ok": False, "msg": "Telegram jetonu girilmemis"}
    if not RE_TG_TOKEN.match(token):
        return {"ok": False, "msg": "jeton bicimi hatali (ornek: 123456789:AAE...)"}
    hedef = str(chat or varsayilan or "").strip()
    if not hedef: return {"ok": False, "msg": "sohbet (chat id) girilmemis"}
    veri = urllib.parse.urlencode({
        "chat_id": hedef,
        "text": (f"<b>pve-gdrive-backup</b> test\n{os.uname().nodename} · v{SURUM}\n"
                 f"{now_str()}\n\nBildirimler buraya dusecek."),
        "parse_mode": "HTML"}).encode()
    try:
        istek = urllib.request.Request(f"{TG_API}/bot{token}/sendMessage", data=veri)
        with urllib.request.urlopen(istek, timeout=20) as y:
            c = json.loads(y.read().decode("utf-8", "replace"))
        if c.get("ok"):
            log(f"telegram testi basarili -> {hedef}")
            return {"ok": True, "msg": "test mesaji gonderildi"}
        return {"ok": False, "msg": "Telegram reddetti: "
                + str(c.get("description") or "")[:150]}
    except Exception as e:
        return {"ok": False, "msg": str(e).replace(token, "<jeton>")[:180]}

def tg_metin(baslik, satirlar, durum=None):
    simge = {"basarili": "✅", "HATA": "🛑", "atlandi": "⏸"}.get(durum, "ℹ️")
    govde = "\n".join(f"{_html.escape(str(x))}" for x in satirlar if str(x).strip())
    return f"{simge} <b>{_html.escape(baslik)}</b>\n<pre>{govde}</pre>"

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
    konu = f"[Proxmox Backup] {p['name']} - {M(status)}" if not dil_tr() else \
           f"[Proxmox Yedek] {p['name']} - {status}"
    konu += (f" ({uyari} " + ("warning" if not dil_tr() else "uyari") + ")") if uyari else ""
    send_mail(p.get("mail_to", ""), konu, metni_cevir(body), p.get("smtp_profile"), durum=status)
    # Telegram: mail kadar detay degil, bakinca durumu anlatan ozet
    tg_gonder(tg_metin(f"{p['name']} — {status}", [
        f"Zaman   : {now_str()}",
        f"Yuklenen: {detay.get('uploaded', 0)} dosya",
        f"Cope    : {detay.get('moved', 0)} · kalici silinen: {detay.get('purged', 0)}",
        f"Sure    : {detay.get('dur', '-')} sn",
        (f"Hedef   : {detay.get('aktif')}" if detay.get("yedege_dustu") else ""),
        ("! Birincil hedef calismadi, yedege yazildi" if detay.get("yedege_dustu") else ""),
        ("! RETENTION ATLANDI - hicbir yedek silinmedi" if detay.get("skipped") else ""),
        f"{uyari} uyari" if uyari else "",
    ], status), p)

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
    if detay.get("konf"):
        L += [f"  Host yapilandirmasi: {detay.get('konf_n', 0)} dosya arsivlendi ve yuklendi", ""]
    elif p.get("host_config_enabled", GLOBAL_DEFAULTS["host_config_enabled"]):
        L += ["  ! Host yapilandirmasi bu calismada yedeklenemedi.", ""]
    dn = detay.get("denemeler") or []
    if len(dn) > 1 or detay.get("yedege_dustu"):
        L += ["HEDEFLER"]
        for i, d in enumerate(dn):
            L.append(f"  {i + 1}. {d['hedef']:38} {'BASARILI' if d['ok'] else 'basarisiz'}")
        if detay.get("yedege_dustu"):
            L += ["  ! Birincil hedef calismadi, yedek hedefe yazildi.",
                  "    Eski yedekler yalnizca yazilan hedefte temizlendi; "
                  "digerlerine dokunulmadi.", ""]
        else:
            L.append("")
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
        L += ["VM/CT BAZINDA SON YEDEK"] + misafir_satirlari + [""]
    bl = snap.get("backups", [])[:10]
    if bl:
        L.append("EN YENI YEDEKLER")
        for b in bl:
            L.append(f"  {str(b.get('mod', ''))[:19].replace('T', ' ')}  {b['guest']:9}"
                     f"  {human(b['size']):>9}  {b['name']}")
        L.append("")
    L += _bolum_cop(snap)

    uyari = []
    if detay.get("yedege_dustu"):
        uyari.append(f"Birincil hedef calismadi, yedek hedef kullanildi: {detay.get('aktif')}")
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
    """(basarili_mi, yuklenen_dosya, atlanan_yarim, aktif_hedef, denemeler)

    Hedefler sirayla denenir; ilk basarili olan kazanir. Basarisiz hedeflere
    HICBIR SEY yapilmaz - ne silme, ne temizlik."""
    yarim = inprogress(p)
    if yarim:
        log(f"yazilmakta olan {len(yarim)} dosya atlanacak: {', '.join(yarim[:3])}"
            + (" ..." if len(yarim) > 3 else ""), pid)
    hedefler = plan_hedefleri(p)
    denemeler = []
    for i, hedef in enumerate(hedefler):
        if i:
            log(f"yedek hedefe geciliyor ({i}/{len(hedefler) - 1}): {hedef}", pid)
            set_progress(pid, {"phase": "yedek-hedef",
                               "phase_label": f"Yedek hedefe geçildi: {hedef}",
                               "hedef": hedef})
        else:
            set_progress(pid, {"hedef": hedef})
        # Kopyalama oncesi "kac dosya vardi" listelemesi yapilmaz: yuklenen sayisi
        # rclone'un kendi ozetinden gelir, bir tam uzak listeleme tasarruf edilir.
        ok, uploaded = do_copy(plan_hedefle(p, hedef))
        denemeler.append({"hedef": hedef, "ok": bool(ok), "yuklenen": uploaded})
        if ok:
            if i: log(f"yedek hedefe yazildi: {hedef} (birincil hedef basarisizdi)", pid)
            return True, uploaded, yarim, hedef, denemeler
        log(f"hedef basarisiz: {hedef}", pid)
    if len(hedefler) > 1:
        log(f"TUM hedefler basarisiz ({len(hedefler)} deneme) - hicbir yerde "
            f"silme yapilmayacak", pid)
    return False, 0, yarim, None, denemeler

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
    moved += hostconf_prune(p, files)      # arsivler vzdump kalibina uymaz, ayri kural
    set_progress(pid, {"phase": "cop", "phase_label": "Çöp kutusu temizleniyor"})
    purged = do_purge_trash(p)
    return moved, purged, False

def _asama_bitir(p, pid, trigger, started, ok, uploaded, moved, purged, atlandi, yarim,
                 konf=0, konf_n=0, aktif=None, denemeler=None):
    set_progress(pid, {"phase": "ozet", "phase_label": "Durum güncelleniyor"})
    snap = update_snapshot(p)
    dur = int(time.time() - started)
    status = "basarili" if ok else "HATA"
    denemeler = denemeler or []
    yedege_dustu = bool(aktif and denemeler and denemeler[0]["hedef"] != aktif)
    summary = (f"yuklenen:{uploaded} | copene:{moved} | kalici-silinen:{purged} | sure:{dur}s"
               + (f" | yapilandirma:{konf_n} dosya" if konf else "")
               + (f" | YEDEK HEDEF: {aktif}" if yedege_dustu else "")
               + (" | RETENTION ATLANDI" if atlandi else ""))
    log("OZET: " + status + " | " + summary, pid)
    patch = {"last_run": now_str(), "status": status, "summary": summary,
             "last_trigger": trigger, "last_duration": dur,
             "aktif_hedef": aktif, "hedef_denemeleri": denemeler,
             "history": _gecmise_ekle(pid, {"time": now_str(), "status": status,
                                            "summary": summary, "trigger": trigger})}
    patch.update(snap)
    put_pstate(pid, patch)
    maybe_report(p, status, summary, snap,
                 {"trigger": trigger, "dur": dur, "uploaded": uploaded, "moved": moved,
                  "purged": purged, "skipped": atlandi, "yarim": yarim,
                  "konf": konf, "konf_n": konf_n,
                  "aktif": aktif, "denemeler": denemeler, "yedege_dustu": yedege_dustu})

def _asama_hata(p, pid, e):
    log(f"BEKLENMEDIK HATA: {e}", pid)
    put_pstate(pid, {"last_run": now_str(), "status": "HATA", "summary": str(e)})
    if p.get("notify_failure", True):
        send_mail(p.get("mail_to", ""), f"[Proxmox Yedek] {p['name']} - HATA",
                  f"Plan: {p['name']} ({pid})\nIstisna: {e}\nZaman: {now_str()}",
                  p.get("smtp_profile"), durum="HATA")

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
        ok, uploaded, yarim, aktif, denemeler = _asama_kopyala(p, pid)
        # Bundan sonraki HER SEY yalnizca basarili hedefe uygulanir.
        # aktif None ise hicbiri calismadi: silme yapan asamalar zaten atlanir.
        ph = plan_hedefle(p, aktif) if aktif else p
        set_progress(pid, {"phase": "yapilandirma",
                           "phase_label": "Host yapılandırması yedekleniyor"})
        konf, konf_n, _ = hostconf_yukle(ph) if ok else (0, 0, "")
        moved, purged, atlandi = _asama_retention(ph, pid, ok)
        _asama_bitir(ph, pid, trigger, started, ok, uploaded, moved, purged, atlandi, yarim,
                     konf, konf_n, aktif, denemeler)
    except Exception as e:
        _asama_hata(p, pid, e)
    finally:
        clear_progress(pid)
        fcntl.flock(lock, fcntl.LOCK_UN); lock.close()

def do_tick():
    """systemd timer bunu sik araliklarla cagirir; vakti gelen planlari calistirir."""
    tick_damgasi_yaz()      # "yasiyorum": timer durursa arayuz ve rapor fark etsin
    try: butunluk_izle()    # betik beklenmedik sekilde degistiyse haber ver
    except Exception as e: yut("butunluk_izle", e)
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
# ---------- SAGLAYICILAR ----------
# rclone'un "authorize" ile web akisi destekledigi ve tek jetonla
# yapilandirilabilen saglayicilar. Liste, hedef kurulumdaki rclone 1.60.1
# uzerinde tek tek denenerek cikarildi (bkz. docs/OZELLIKLER.md).
#
# DURUST NOT: gercek bir hesapla ucdan uca yalnizca Google Drive dogrulandi.
# Digerlerinin OAuth akisi calisiyor ama yukleme/saklama davranisi test
# edilmedi; "denenmedi" olarak isaretli ve arayuzde de oyle gorunuyor.
SAGLAYICILAR = {
    "drive":    {"ad": "Google Drive", "simge": "🇬",
                 "auth": ["--drive-scope", "drive.file"],
                 "olustur": ["scope=drive.file"], "dogrulandi": True,
                 "not": "Kapsam drive.file: yalnizca bu aracin olusturdugu "
                        "dosyalari gorur, Drive'inin gerisine erisemez."},
    "dropbox":  {"ad": "Dropbox", "simge": "📦", "auth": [], "olustur": [],
                 "dogrulandi": False, "not": ""},
    "onedrive": {"ad": "OneDrive", "simge": "☁", "auth": [], "olustur": [],
                 "dogrulandi": False,
                 "not": "Bazi kurumsal hesaplarda drive_id/drive_type de gerekir; "
                        "gerekirse hesabi rclone config ile elle kur."},
    "box":      {"ad": "Box", "simge": "🗄", "auth": [], "olustur": [],
                 "dogrulandi": False, "not": ""},
    "pcloud":   {"ad": "pCloud", "simge": "🌥", "auth": [], "olustur": [],
                 "dogrulandi": False, "not": ""},
    "yandex":   {"ad": "Yandex Disk", "simge": "🅨", "auth": [], "olustur": [],
                 "dogrulandi": False, "not": ""},
    "sharefile": {"ad": "Citrix ShareFile", "simge": "📁", "auth": [], "olustur": [],
                  "dogrulandi": False, "not": ""},
    "hidrive":  {"ad": "HiDrive", "simge": "💾", "auth": [], "olustur": [],
                 "dogrulandi": False, "not": ""},
}

def saglayici(tur):
    return SAGLAYICILAR.get(str(tur or "drive"), SAGLAYICILAR["drive"])

_SAGLAYICI_ONBELLEK = {"zaman": 0.0, "adlar": None}

def rclone_saglayici_adlari():
    """rclone'un tanidigi backend adlari (kume) ya da cozulemezse None.

    'rclone config providers' ~1 MB JSON uretir; her arayuz acilisinda
    ayristirmak israf, bu yuzden onbellege alinir. Cikti bicimi surumler
    arasinda degisebilir: cozemezsek None doneriz ve cagiran taraf HICBIR
    saglayiciyi gizlemez. Tespit hatasi calisan bir saglayiciyi saklamamali -
    once bu kural yanlisti ve Google Drive dahil hepsi 'tanimiyor' gorunuyordu."""
    simdi = time.time()
    if _SAGLAYICI_ONBELLEK["adlar"] is not None and simdi - _SAGLAYICI_ONBELLEK["zaman"] < 3600:
        return _SAGLAYICI_ONBELLEK["adlar"]
    adlar = None
    try:
        rc, out, _ = rclone(["config", "providers"], timeout=30)
        if rc == 0 and out.strip():
            veri = json.loads(out)
            if isinstance(veri, list):
                adlar = {str(x.get("Name", "")).lower() for x in veri if isinstance(x, dict)}
                adlar.discard("")
    except Exception as e:
        yut("rclone_saglayici_adlari", e)
    if not adlar: adlar = None
    _SAGLAYICI_ONBELLEK.update(zaman=simdi, adlar=adlar)
    return adlar

def saglayici_listesi():
    """Arayuz icin saglayici listesi. 'kurulu' yalnizca rclone'un backend
    listesi GUVENILIR bicimde okunabildiginde False olabilir."""
    adlar = rclone_saglayici_adlari()
    liste = []
    for tur, v in SAGLAYICILAR.items():
        liste.append({"tur": tur, "ad": v["ad"], "simge": v["simge"],
                      "dogrulandi": v["dogrulandi"], "not": v["not"],
                      "kurulu": True if adlar is None else (tur in adlar)})
    return liste

def auth_out_yolu():
    """OAuth ciktisi (JETON ICERIR) icin dosya yolu.

    Onceden /tmp altindaydi: dunyaya okunabilir olusuyor ve /tmp dunyaya
    yazilabilir oldugu icin onceden yerlestirilmis bir sembolik baglanti
    root olarak baska bir dosyaya yazdirabiliyordu. Artik yalnizca root'un
    girebildigi durum dizininde, 0600 ve O_NOFOLLOW ile aciliyor."""
    return os.path.join(os.path.dirname(cfg().get("state_file",
                        "/var/lib/pve-gdrive/state.json")), "auth.out")

def auth_out_ac():
    yol = auth_out_yolu()
    os.makedirs(os.path.dirname(yol), exist_ok=True)
    try: os.remove(yol)
    except FileNotFoundError: pass
    except Exception as e: yut("auth_out_ac", e)
    fd = os.open(yol, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    return os.fdopen(fd, "w")

# Geriye donuk ad: eski /tmp dosyasi varsa temizlenebilsin diye tutulur
AUTH_OUT_ESKI = "/tmp/pve-gdrive-auth.out"
_AUTH = {"proc": None, "url": None, "started": 0}

def rclone_remotes(force=False):
    """Yapilandirilmis rclone remote'lari. Her plan bunlardan birini hedef secer.
    force parametresi cagri yerinin niyetini belli eder: yazmadan sonra dogrulama."""
    _ = force
    rc, out, err = rclone(["listremotes", "--long"])
    res = []
    if rc != 0: return res
    for line in out.splitlines():
        if not line.strip(): continue
        parts = line.split(":", 1)
        if len(parts) != 2: continue
        res.append({"name": parts[0].strip(), "type": parts[1].strip()})
    return res

_KALICI_DAMGA = {"mtime": None}   # oturum dosyasinin son gorulen damgasi
_BILINMEYEN_CEREZ = {}   # jeton oneki -> son loglama zamani (teshis, spam olmasin)
_KOTA_ONBELLEK = {}   # hesap adi -> {"veri": {...}, "zaman": ts}

_KOTA_ISLER = set()

def _kota_arkaplan(name):
    """Kotayi arka planda tazeler. Olculdu: bir 'rclone about' cagrisi 34 sn
    surebiliyor; bunu /api/status icinde beklemek arayuzu kilitler."""
    try:
        veri = remote_quota(name)
        _KOTA_ONBELLEK[name] = {"veri": veri, "zaman": time.time()}
    except Exception as e:
        yut("_kota_arkaplan", e)
    finally:
        _KOTA_ISLER.discard(name)

def remote_quota_onbellekli(name, zorla=False, bekle=False):
    """Onbellekten aninda doner; suresi dolduysa tazelemeyi ARKA PLANA atar.

    bekle=True yalnizca kullanicinin acikca bekledigi yerlerde (hesap testi,
    kapasite planlayici) kullanilir. Arayuz asla kota sorgusunu beklemez."""
    ttl = float(cfg().get("quota_cache_min") or 15) * 60
    kayit = _KOTA_ONBELLEK.get(name)
    taze = kayit and (time.time() - kayit["zaman"]) < ttl
    if taze and not zorla:
        return kayit["veri"]
    if bekle or zorla:
        veri = remote_quota(name)
        _KOTA_ONBELLEK[name] = {"veri": veri, "zaman": time.time()}
        return veri
    if name not in _KOTA_ISLER:
        _KOTA_ISLER.add(name)
        threading.Thread(target=_kota_arkaplan, args=(name,), daemon=True).start()
    # Eski deger varsa onu goster (bayat ama dogru), yoksa "olculuyor"
    return kayit["veri"] if kayit else {"ok": None, "bekliyor": True}

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

def auth_start(tur="drive"):
    """rclone'un OAuth yardimcisini baslatir. Saglayici tarayiciyi
    127.0.0.1:53682'ye yonlendirdigi icin bu adres kullanicinin KENDI
    makinesinde acilabilir olmali; bu yuzden SSH tuneli komutu da dondurulur."""
    if tur not in SAGLAYICILAR:
        return {"ok": False, "url": None, "tunnel": "",
                "msg": f"desteklenmeyen saglayici: {tur}"}
    auth_stop()
    try: os.remove(auth_out_yolu())
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
    sg = saglayici(tur)
    f = auth_out_ac()
    _AUTH["tur"] = tur
    _AUTH["proc"] = subprocess.Popen(
        ["rclone", "authorize", tur] + list(sg["auth"]) + ["--auth-no-open-browser"],
        stdout=f, stderr=subprocess.STDOUT, start_new_session=True, env=rclone_ortam())
    _AUTH["started"] = time.time(); _AUTH["url"] = None
    hata = ""
    for _ in range(60):
        time.sleep(0.25)
        try: txt = open(auth_out_yolu()).read()
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
    try: txt = open(auth_out_yolu()).read()
    except Exception: txt = ""
    got = '"access_token"' in txt
    return {"ok": True, "ready": got, "url": _AUTH.get("url"),
            "waiting": bool(_AUTH.get("proc") and _AUTH["proc"].poll() is None)}

def auth_token():
    try: txt = open(auth_out_yolu()).read()
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

def rclone_conf_yedekle(sebep):
    """Yapilandirmayi degistiren her islemden ONCE zaman damgali kopya alir.

    2026-08-08: bir hesap eklendi, rclone rc=0 dondu, log 'eklendi' yazdi ve
    hesap dosyada yoktu. Hangi yazmanin dusurdugu geriye donuk kanitlanamadi
    cunku hicbir kopya yoktu. Artik var."""
    try:
        if not os.path.exists(RCLONE_CONF): return ""
        d = os.path.join(os.path.dirname(RCLONE_CONF), "rclone-yedek")
        os.makedirs(d, exist_ok=True); os.chmod(d, 0o700)
        hedef = os.path.join(d, f"rclone.conf.{datetime.now():%Y%m%d-%H%M%S}.{slug(sebep)}")
        shutil.copy2(RCLONE_CONF, hedef); os.chmod(hedef, 0o600)
        tut = int(cfg().get("rclone_conf_yedek_tut") or 20)
        for e in sorted(os.listdir(d), reverse=True)[tut:]:
            try: os.remove(os.path.join(d, e))
            except Exception as ex: yut("rclone_conf_yedekle", ex)
        return hedef
    except Exception as e:
        yut("rclone_conf_yedekle", e); return ""

def remote_var_mi(name):
    """Dosyaya gercekten yazildi mi? rclone'un cikis kodu yeterli kanit degil."""
    return any(r["name"] == name for r in rclone_remotes(force=True))

def remote_create(name, token, tur="drive"):
    if tur not in SAGLAYICILAR:
        return {"ok": False, "msg": f"desteklenmeyen saglayici: {tur}"}
    name = re.sub(r"[^A-Za-z0-9_-]", "", str(name or "")).strip()
    if not name: return {"ok": False, "msg": "gecersiz hesap adi"}
    if any(r["name"] == name for r in rclone_remotes()):
        return {"ok": False, "msg": f"'{name}' zaten var"}
    try: json.loads(token)
    except Exception as e:
        yut("remote_create", e)
        return {"ok": False, "msg": "jeton gecerli JSON degil"}
    yedek = rclone_conf_yedekle(f"ekle-{name}")
    # --non-interactive: rclone jetonu dogrulamak icin OAuth sunucusu acip asili kalmasin
    rc, out, err = rclone(["config", "create", name, tur]
                          + list(saglayici(tur)["olustur"])
                          + [f"token={token}", "--non-interactive"], timeout=60)
    if rc != 0: return {"ok": False, "msg": (err or out).strip()[:200]}
    # Cikis kodu 0 olmasi yazildigini KANITLAMAZ. Dosyadan geri okuyup dogrula:
    # aksi halde "eklendi" denir, hesap yoktur ve bu ancak yedek gununde anlasilir.
    if not remote_var_mi(name):
        log(f"UYARI: '{name}' eklendi gorundu ama yapilandirmada yok "
            f"(RCLONE_CONFIG={RCLONE_CONF})"
            + (f", yedek: {yedek}" if yedek else ""))
        return {"ok": False, "msg": f"'{name}' yazilamadi: rclone basarili dedi ama hesap "
                                    f"yapilandirmada gorunmuyor ({RCLONE_CONF}). Loga bak."}
    try: os.chmod(RCLONE_CONF, 0o600)
    except Exception as e: yut("remote_create", e)
    q = remote_quota(name)
    _KOTA_ONBELLEK.pop(name, None)
    log(f"rclone hesabi eklendi ve dogrulandi: {name} ({saglayici(tur)['ad']})")
    return {"ok": True, "msg": f"'{name}' eklendi" + (
        f" ({human(q.get('used'))}/{human(q.get('total'))} kullanimda)" if q.get("ok") else
        " ama kota okunamadi: " + str(q.get("error", ""))[:80]), "name": name}

def remote_delete(name):
    used = [p["name"] for p in cfg().get("plans", []) if p["remote"].split(":")[0] == name]
    if used: return {"ok": False, "msg": "su planlar kullaniyor: " + ", ".join(used)}
    yedek = rclone_conf_yedekle(f"sil-{name}")
    rc, out, err = rclone(["config", "delete", name])
    if rc != 0: return {"ok": False, "msg": (err or "").strip()[:200]}
    if remote_var_mi(name):
        return {"ok": False, "msg": f"'{name}' silinemedi: hala yapilandirmada gorunuyor"}
    _KOTA_ONBELLEK.pop(name, None)
    log(f"rclone hesabi silindi: {name} (Drive'daki dosyalara dokunulmadi)"
        + (f" | yedek: {yedek}" if yedek else ""))
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

def systemd_var_mi():
    """systemd ile mi yonetiliyoruz? Yeniden baslatma yontemi buna gore secilir."""
    if not os.path.exists("/run/systemd/system"): return False
    return os.path.exists("/usr/bin/systemctl") or os.path.exists("/bin/systemctl")

def ic_zamanlayici_acik():
    """Zamanlayiciyi surecin kendisi mi calistirsin.
    Varsayilan: systemd timer varsa hayir, yoksa evet."""
    v = cfg().get("internal_scheduler")
    return (not systemd_var_mi()) if v is None else bool(v)

def servisi_yeniden_baslat():
    """systemd varsa servisi yeniden baslatir; yoksa surecten cikar ve disaridaki
    denetleyicinin ayaga kaldirmasini bekler."""
    if not systemd_var_mi():
        log("guncelleme kuruldu, surec yeniden baslatiliyor")
        threading.Timer(2.0, lambda: os._exit(0)).start()
        return "süreç yeniden başlatılıyor"
    subprocess.Popen(["systemctl", "restart", "pve-gdrive-ui.service"], start_new_session=True)
    return "servis yeniden başlatılıyor"

def zamanlayici_dongusu(dur_bayragi):
    """systemd timer yoksa tick'i surecin kendisi calistirir."""
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
    """Guncellemenin uzerine yazacagi GERCEK dosya.

    realpath sart: kisa ad (/usr/local/bin/pve-gdrive) gercek dosyaya sembolik
    baglanti. abspath baglantiyi cozmedigi icin os.replace baglantinin KENDISINI
    duz dosyayla degistirir; systemd hala eski hedefi calistirir ve guncelleme
    'kuruldu' der ama hicbir sey degismez."""
    return os.path.realpath(getattr(sys.modules["__main__"], "__file__", __file__))

def yedek_dizini():
    """Guncelleme yedekleri. Icinde config kopyalari var: UI sifre hash'i ve
    SMTP parolalari duz metin. Dizin varsayilan umask ile 0755 olusuyordu."""
    d = os.path.join(os.path.dirname(cfg().get("state_file", "/var/lib/pve-gdrive/x")), "yedek")
    os.makedirs(d, exist_ok=True)
    try: os.chmod(d, 0o700)
    except Exception as e: yut("yedek_dizini", e)
    return d

def guncelleme_adresi_gecerli(url):
    """(gecerli_mi, sebep). Guncelleme adresi root olarak calisacak kodu
    belirledigi icin serbest olamaz: yalnizca https ve izinli host."""
    try:
        u = urlparse(str(url or ""))
    except Exception as e:
        yut("guncelleme_adresi_gecerli", e); return False, "adres cozulemedi"
    if u.scheme != "https":
        return False, "guncelleme adresi https olmali (indirilen dosya root olarak calisir)"
    host = (u.hostname or "").lower()
    izinli = [str(h).lower() for h in (cfg().get("update_izinli_hostlar") or [])]
    if izinli and host not in izinli:
        return False, (f"'{host}' izinli guncelleme sunucusu degil. "
                       f"Izinliler: {', '.join(izinli)}")
    return True, ""

def guncelleme_indir(url=None, zaman_asimi=30):
    """(kaynak_metin, surum, hata). Ag hatasi programi durdurmaz."""
    url = url or cfg().get("update_url")
    if not url: return None, None, "guncelleme adresi tanimli degil"
    ok, sebep = guncelleme_adresi_gecerli(url)
    if not ok:
        log(f"GUVENLIK: guncelleme adresi reddedildi ({url}): {sebep}")
        return None, None, sebep
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
    # Ozet sabitlenmisse eslesmeyen dosya KURULMAZ. Sozdizimi kontrolu dosyanin
    # calisabilir oldugunu gosterir, DOGRU dosya oldugunu degil.
    beklenen = str(cfg().get("update_sha256") or "").strip().lower()
    if beklenen:
        gelen = hashlib.sha256(ham.encode("utf-8")).hexdigest()
        if gelen != beklenen:
            log(f"GUVENLIK: guncelleme ozeti tutmadi (beklenen {beklenen[:16]}…, "
                f"gelen {gelen[:16]}…) - kurulmadi")
            return None, None, ("indirilen dosyanin sha256 ozeti ayarlardaki degerle "
                                "eslesmiyor, kurulmadi")
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
        try:
            ck = os.path.join(yd, f"config-{damga}.json")
            shutil.copy2(CONFIG_PATH, ck); os.chmod(ck, 0o600)
        except Exception as e: yut("guncelleme_uygula", e)
        # 3) Yerine koy (atomik) ve servisi yeniden baslat
        os.replace(gecici, hedef)
        # Mesru degisiklik alarm uretmesin: referans hemen yenilenir
        try: butunluk_sabitle(f"guncelleme {SURUM} -> {uzak}")
        except Exception as e: yut("guncelleme_butunluk", e)
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
        self._dusen = set()    # bilerek dusurulen kalici oturumlar (cikis / suresi doldu)
        self.captchalar = {}   # cid   -> {code, exp}
        self.hatalar = {}      # ip    -> {"n": int, "until": ts, "last": ts}

    def sifirla(self):
        """Yalnizca testler icin: depoyu bosaltir."""
        with self.kilit:
            self.oturumlar.clear(); self.captchalar.clear(); self.hatalar.clear()
            self._dusen = set()

    def kalicilari_yukle(self):
        """Servis yeniden baslarken 'beni hatirla' oturumlarini geri getirir.
        Onceden oturumlar yalnizca bellekteydi: her guncelleme herkesi cikis
        yaptiriyor, hatirlama calismiyor gorunuyordu."""
        try:
            with open(oturum_dosyasi(), "r", encoding="utf-8") as f:
                kayit = json.load(f)
        except FileNotFoundError:
            return 0
        except Exception as e:
            yut("kalicilari_yukle", e); return 0
        simdi = time.time(); n = suresi_dolmus = 0
        with self.kilit:
            for tok, v in (kayit.get("oturumlar") or {}).items():
                if not isinstance(v, dict): continue
                if v.get("bitis", 0) <= simdi: suresi_dolmus += 1; continue
                v["kalici"] = True
                self.oturumlar[tok] = v; n += 1
        if suresi_dolmus:
            log(f"{suresi_dolmus} hatirlanan oturumun suresi dolmustu, alinmadi")
        return n

    def kalicilari_yaz(self, sebep=""):
        """Kalici oturumlari diske yazar. DISKTEKILERLE BIRLESTIREREK.

        Onceden yalnizca kendi belleginikini yaziyordu: dosyayi yuklememis
        ikinci bir surec (tick, CLI, yeni acilan servis) yazdiginda oteki
        oturumlari SILIYORDU. Olculdu: ikinci surec kalici oturum acinca
        dosya 250 bayttan 243 bayta dustu, yani oncekinin yerine gecti.
        Artik dosya kilit altinda okunup birlestiriliyor."""
        yol = oturum_dosyasi()
        try:
            with self.kilit:
                benim = {t: v for t, v in self.oturumlar.items() if v.get("kalici")}
                dusenler = set(self._dusen)
                self._dusen = set()
            os.makedirs(os.path.dirname(yol), exist_ok=True)
            simdi = time.time()
            # Kilit dosyasi: iki surec ayni anda okuyup yazmasin
            kilit_yolu = yol + ".lock"
            with open(kilit_yolu, "a+") as kf:
                fcntl.flock(kf, fcntl.LOCK_EX)
                try:
                    try:
                        with open(yol, "r", encoding="utf-8") as f:
                            diskte = json.load(f).get("oturumlar") or {}
                    except FileNotFoundError:
                        diskte = {}
                    except Exception as e:
                        yut("kalicilari_yaz_oku", e); diskte = {}
                    birlesik = {t: v for t, v in diskte.items()
                                if isinstance(v, dict)
                                and v.get("bitis", 0) > simdi      # suresi dolmus gitsin
                                and t not in dusenler}             # cikis yapan gitsin
                    birlesik.update(benim)
                    gecici = yol + ".tmp"
                    with open(gecici, "w", encoding="utf-8") as f:
                        json.dump({"oturumlar": birlesik}, f)
                    os.chmod(gecici, 0o600)      # jeton dosyasi baskasina okunmasin
                    os.replace(gecici, yol)
                    log(f"oturum deposu yazildi: {len(birlesik)} kalici oturum"
                        + (f" ({sebep})" if sebep else ""))
                finally:
                    fcntl.flock(kf, fcntl.LOCK_UN)
        except Exception as e:
            # Sessiz yutma yok: hatirlama calismiyorsa sebebi logda gorunsun
            log(f"UYARI: oturum deposu yazilamadi ({yol}): {e}")

def oturum_dosyasi():
    return os.path.join(os.path.dirname(cfg().get("state_file",
                        "/var/lib/pve-gdrive/state.json")), "oturumlar.json")

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

_LAN_ONBELLEK = {"zaman": 0, "aglar": []}

def host_lan_aglari():
    """Sunucunun kendi IPv4 aglari. 60 sn onbelleklenir: her istekte 'ip addr'
    calistirmak gereksiz olur."""
    if time.time() - _LAN_ONBELLEK["zaman"] < 60:
        return _LAN_ONBELLEK["aglar"]
    out = []
    try:
        r = subprocess.run(["ip", "-4", "-o", "addr", "show", "scope", "global"],
                           capture_output=True, text=True, timeout=10)
        for satir in r.stdout.splitlines():
            p = satir.split()
            if len(p) < 4: continue
            try: out.append(ipaddress.ip_network(p[3], strict=False))
            except Exception as e: yut("host_lan_aglari", e)
    except Exception as e:
        yut("host_lan_aglari", e)
    # Loopback her zaman dahil: sunucunun kendi uzerindeki saglik kontrolu veya
    # yerel ters vekil sessizce 403 almasin. Buraya ancak host'ta shell'i olan
    # biri erisebilir, o da zaten root'tur.
    for lo in ("127.0.0.0/8", "::1/128"):
        try: out.append(ipaddress.ip_network(lo))
        except Exception as e: yut("host_lan_aglari", e)
    _LAN_ONBELLEK.update(zaman=time.time(), aglar=out)
    return out

def izinli_aglar():
    """Yapilandirilmis aglar + (acikken) sunucunun kendi yerel agi.

    Yerel ag her zaman eklenir: kurulumu VPN'den yapip sonra yerel agdan girmek
    isteyen kullanici kendini disarida birakmasin. lan_hep_acik ile kapatilabilir."""
    aglar = []
    for x in cfg().get("allow_networks") or []:
        try: aglar.append(ipaddress.ip_network(str(x).strip(), strict=False))
        except Exception as e:
            yut("izinli_aglar", e)
            log(f"UYARI: gecersiz ag tanimi yok sayildi: {x}")
    if aglar and cfg().get("lan_hep_acik", True):
        aglar += host_lan_aglari()
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
    kalici_dustu = False
    with _SEC_LOCK:
        for t in [t for t, v in SESSIONS.items()
                  # hatirlanan oturumda hareketsizlik siniri uygulanmaz, mutlak bitis gecerli
                  if (now > v.get("bitis", now + absmax))
                  or (not v.get("kalici") and (now - v["last"] > idle
                                               or now - v["created"] > absmax))]:
            _d = SESSIONS.pop(t, {})
            if _d.get("kalici"): kalici_dustu = True; DEPO._dusen.add(t)
        for c in [c for c, v in CAPTCHAS.items() if v["exp"] < now]:
            CAPTCHAS.pop(c, None)
        # bir saattir dokunulmamis ve kilitli olmayan deneme kayitlarini unut
        for i in [i for i, v in FAILS.items()
                  if v.get("until", 0) < now and now - v.get("last", 0) > 3600]:
            FAILS.pop(i, None)
    if kalici_dustu: DEPO.kalicilari_yaz("suresi doldu")

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
    if kalici: DEPO.kalicilari_yaz("yeni giris")
    return tok

def ayni_kaynak(eski, yeni, kip):
    """Oturumun tasinip tasinmadigini kip'e gore karara baglar.
    'ip'  : birebir ayni adres (en siki, varsayilan)
    'ag'  : ayni ag blogu (IPv4 /24, IPv6 /64) - VPN icinde adres degisebiliyorsa
    'yok' : adres kontrolu yapilmaz (yalnizca guvendigin agda)"""
    if kip == "yok": return True
    if eski == yeni: return True
    if kip != "ag": return False
    try:
        a, b = ipaddress.ip_address(eski), ipaddress.ip_address(yeni)
        if a.version != b.version: return False
        onek = 24 if a.version == 4 else 64
        return (ipaddress.ip_network(f"{a}/{onek}", strict=False)
                == ipaddress.ip_network(f"{b}/{onek}", strict=False))
    except Exception as e:
        yut("ayni_kaynak", e); return False

def kalici_diskten_tazele():
    """Oturum dosyasi degistiyse bellege al.

    Dosya yalnizca acilista okunuyordu: baska bir surecin yazdigi (ya da
    acilis ile giris arasinda olusan) gecerli bir oturum sunucu tarafindan
    taninmiyordu - olculdu, 401 donuyordu. Sadece dosyanin damgasi degistiginde
    okunur, her istekte degil."""
    try:
        yol = oturum_dosyasi()
        d = os.stat(yol).st_mtime_ns
    except FileNotFoundError:
        return
    except Exception as e:
        yut("kalici_diskten_tazele", e); return
    if _KALICI_DAMGA.get("mtime") == d: return
    _KALICI_DAMGA["mtime"] = d
    n = DEPO.kalicilari_yukle()
    if n: log(f"oturum deposu diskten tazelendi: {n} kalici oturum")

def get_session(tok, ip):
    gc_sessions()
    kip = str(cfg().get("session_ip_bind") or "ip")
    with _SEC_LOCK:
        v = SESSIONS.get(tok)
    if not v:
        kalici_diskten_tazele()          # belki baska surec yazmistir
        with _SEC_LOCK: v = SESSIONS.get(tok)
    with _SEC_LOCK:
        if not v: return None
        # Hatirlanan oturumda kip ayardan gelir; normal oturum her zaman birebir baglidir.
        if not ayni_kaynak(v["ip"], ip, kip if v.get("kalici") else "ip"):
            log(f"GUVENLIK: oturum baska adresten kullanilmak istendi "
                f"({v['ip']} -> {ip}), reddedildi")
            return None
        v["last"] = time.time()
        v["_tok"] = tok           # "bu oturum benim" karari icin
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
    tick_durum, tick_mesaj = tick_sagligi()
    bt_durum, bt_mesaj = butunluk_kontrol()
    # Jeton ASLA disari verilmez; yalnizca tanimli olup olmadigi
    _tg_var = bool(str(C.get("telegram_token") or "").strip())
    return {"plans": plans, "updated": st.get("updated"),
            "telegram_jeton_var": _tg_var,
            "saglik": {"tick": tick_durum, "tick_mesaj": tick_mesaj,
                       "butunluk": bt_durum, "butunluk_mesaj": bt_mesaj,
                       "tick_son": st.get("last_tick"),
                       "tick_yas_dk": round(tick_yasi_dk() or 0, 1)},
            "settings": {k: C.get(k) for k in
                         ("ui_bind", "ui_port", "ui_user", "smtp_host", "smtp_port", "smtp_user",
                          "mail_from", "browse_roots", "allow_account_cleanup", "history_max",
                          "log_tail_lines", "ui_refresh_sec", "rclone_timeout_min", "dump_regex",
                          "rclone_tail_lines", "snapshot_max_rows", "log_max_mb", "log_keep",
                          "stats_interval_sec", "purge_batch", "purge_timeout_min",
                          "ssl_cert", "ssl_key", "cookie_secure", "cookie_samesite",
                          "allow_networks", "lan_hep_acik",
                          "update_check", "update_auto", "update_url", "update_backup_keep",
                          "update_izinli_hostlar", "update_sha256", "debug",
                          "quota_cache_min", "dil",
                          "failure_mail", "failure_mail_to", "failure_smtp_profile",
                          "failure_mail_lines", "tick_uyari_dk", "butunluk_mail",
                          "telegram_enabled", "telegram_chat_id",
                          "sse_enabled", "sse_watch_ms", "sse_heartbeat_sec", "sse_max_clients",
                          "sse_ping_sec",
                          "remember_enabled", "remember_days", "session_ip_bind",
                          "session_timeout_min",
                          "log_file", "state_file")},
            "smtp": [{k: v for k, v in x.items() if k != "pass"} for x in smtp_profiles(C)],
            "smtp_ready": bool(smtp_profiles(C)),
            "tls": {"aktif": TLS_AKTIF, "sertifika": cert_bilgisi()},
            "hesaplar": hesap_ozeti(),
            "surum": SURUM,
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

    def _olay_akisi(self):
        """SSE akisi. Baglanti acik kaldigi surece bu is parcacigini tutar;
        bu yuzden es zamanli abone sayisi sinirli (sse_max_clients)."""
        C = cfg()
        if not C.get("sse_enabled", True):
            self._json({"ok": False, "msg": "canli akis kapali"}, 503); return
        OLAY.abone_max = int(C.get("sse_max_clients") or 16)
        no = OLAY.abone_ol()
        if no is None:
            self._json({"ok": False, "msg": "canli akis dolu, biraz sonra dene"}, 503); return
        kalp = max(1.0, float(C.get("sse_heartbeat_sec") or 20))
        ping = max(0.5, min(float(C.get("sse_ping_sec") or 5), kalp))
        try:
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream; charset=utf-8")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Connection", "keep-alive")
            # nginx onunde arabellek akisi durdurur; bu basligi anliyor
            self.send_header("X-Accel-Buffering", "no")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.end_headers()
            # Tarayici kopunca kac ms sonra denesin
            self._olay_yaz("retry", None, ham=f"retry: {int(kalp * 100)}\n\n")
            self._olay_yaz("durum", public_status())
            son = son_ping = time.time()
            while True:
                paket = OLAY.bekle(no, min(1.0, ping))
                simdi = time.time()
                if paket:
                    self._olay_yaz(paket[0], paket[1]); son = son_ping = simdi
                elif simdi - son >= kalp:
                    self._olay_yaz("kalp", {"t": now_str()}); son = son_ping = simdi
                elif simdi - son_ping >= ping:
                    # SSE yorum satiri: istemci yok sayar, kopmus baglanti burada belli olur
                    self._olay_yaz(None, None, ham=": ping\n\n"); son_ping = simdi
        except (BrokenPipeError, ConnectionResetError, OSError):
            pass            # tarayici sekmeyi kapatti, olagan
        except Exception as e:
            yut("_olay_akisi", e)
        finally:
            OLAY.ayril(no)

    def _olay_yaz(self, tur, veri, ham=None):
        if ham is None:
            ham = f"event: {tur}\ndata: {json.dumps(veri, ensure_ascii=False)}\n\n"
        self.wfile.write(ham.encode("utf-8"))
        self.wfile.flush()

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
        # SameSite: Strict, baska bir sayfadan (ornegin Proxmox arayuzundeki
        # baglantidan) gelen ust duzey gezinmede cerezi GONDERMEZ - kullanici
        # oturumu acik olmasina ragmen giris ekrani gorur ve "beni hatirla
        # calismiyor" sanir. Lax bu gezinmede gonderir, CSRF'ye acik
        # cross-site POST'ta gondermez; ustelik ayrica CSRF jetonu var.
        ss = str(C.get("cookie_samesite") or "Lax")
        if ss not in ("Lax", "Strict", "None"): ss = "Lax"
        parts = [f"pgs={'' if sil else tok}", "Path=/", "HttpOnly", f"SameSite={ss}"]
        if sil: parts.append("Max-Age=0")
        elif omur_sn: parts.append(f"Max-Age={int(omur_sn)}")   # tarayici kapaninca silinmesin
        if C.get("cookie_secure") or TLS_AKTIF: parts.append("Secure")
        return ("Set-Cookie", "; ".join(parts))

    def _session(self):
        m = re.search(r"pgs=([A-Za-z0-9_\-]+)", self.headers.get("Cookie", "") or "")
        if not m:
            return None
        o = get_session(m.group(1), client_ip(self))
        if o is None:
            # "Beni hatirla calismiyor" sikayetini ikiye ayirmak icin sart:
            # cerez HIC gelmiyor mu (tarayici saklamamis/gondermiyor), yoksa
            # geliyor da sunucu mu tanimiyor (oturum dusmus)? Cerez geldiyse
            # buraya duseriz. Ayni istemci icin dakikada bir yazilir.
            simdi = time.time()
            anahtar = m.group(1)[:8]
            if simdi - _BILINMEYEN_CEREZ.get(anahtar, 0) > 60:
                _BILINMEYEN_CEREZ[anahtar] = simdi
                if len(_BILINMEYEN_CEREZ) > 50: _BILINMEYEN_CEREZ.clear()
                log(f"TESHIS: tarayici oturum cerezi gonderdi ama sunucu tanimiyor "
                    f"(adres={client_ip(self)} host={self.headers.get('Host', '?')} "
                    f"jeton={anahtar}… yol={self.path[:40]}). "
                    f"Bellekte {len(SESSIONS)} oturum var.")
        return o

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
                   metni_cevir(LOGIN_HTML).replace("{{HATA}}", _html.escape(M(hata)))
                             .replace("{{ERRD}}", "block" if hata else "none")
                             .replace("{{CAPD}}", "block" if cap else "none")
                             .replace("{{DISABLED}}", "disabled" if kilit else "")
                             .replace("{{HATIRLA}}", "flex" if hatirla_acik else "none")
                             .replace("{{HATIRLAGUN}}", str(int(C.get("remember_days") or 30)))
                             .replace("{{CID}}", cid or "")
                             # Hangi surume ve hangi sunucuya girdigini sifreyi
                             # yazmadan once gor: birden fazla Proxmox varken
                             # karistirmamak, TLS'siz bir sayfaya parola
                             # girmemek icin.
                             .replace("{{SURUM}}", _html.escape(SURUM))
                             .replace("{{SUNUCU}}", _html.escape(os.uname().nodename))
                             .replace("{{TLS}}", "🔒 HTTPS" if TLS_AKTIF else "⚠ HTTP")
                             .replace("{{TLSSINIF}}", "acik" if TLS_AKTIF else "kapali")
                             .replace("{{TLSIPUCU}}", _html.escape(M(
                                 "Bağlantı şifreli" if TLS_AKTIF
                                 else "Trafik şifresiz — yalnızca VPN içinde kullan"))))

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
        elif p == "/api/events":
            self._olay_akisi()
        elif p == "/api/log":
            src = q.get("src", ["all"])[0] or "all"
            self._send(200, "text/plain; charset=utf-8", "\n".join(read_log(src)))
        elif p == "/api/browse":
            self._json(browse(unquote(q.get("path", [""])[0])))
        elif p == "/api/remotes":
            rs = rclone_remotes()
            if q.get("quota", [""])[0] == "1":
                zorla = q.get("force", [""])[0] == "1"
                for r in rs: r["quota"] = remote_quota_onbellekli(r["name"], zorla, bekle=True)
            self._json({"remotes": rs})
        elif p == "/api/remote/auth/status":
            self._json(auth_status())
        elif p == "/api/update/check":
            self._json(guncelleme_kontrol(zorla=q.get("force", [""])[0] == "1"))
        elif p == "/api/analiz":
            src = unquote(q.get("src", [""])[0])
            hesap = q.get("hesap", [""])[0]
            a = kaynak_analiz(src) if src else {"ok": False, "hata": "kaynak klasor secilmedi"}
            # Kapasite planlayicida kullanici zaten bekliyor: guncel kota alinir.
            kota = remote_quota_onbellekli(hesap, bekle=True) if hesap else {}
            cevap = {"analiz": a, "kota": kota}
            if a.get("ok") and kota.get("ok"):
                cevap["oneri"] = saklama_oneri(a, kota)
                cevap["oneri_pay_pct"] = int(cfg().get("oneri_pay_pct") or 60)
            self._json(cevap)
        elif p == "/api/disa-aktar":
            # Sirlar varsayilan olarak CIKMAZ; acikca istenirse ve loga yazilarak
            sirlar = q.get("sirlar", [""])[0] == "1"
            if sirlar: log(f"GUVENLIK: ayarlar SIRLARLA disa aktarildi ({client_ip(self)})")
            veri = json.dumps(disa_aktar(sirlar), ensure_ascii=False, indent=2)
            ad = f"pve-gdrive-ayarlar-{datetime.now():%Y%m%d-%H%M%S}.json"
            self._send(200, "application/json; charset=utf-8", veri,
                       [("Content-Disposition", f'attachment; filename="{ad}"')])
        elif p == "/api/oturumlar":
            self._json({"ok": True, "oturumlar": oturum_listesi((self.sess or {}).get("_tok")),
                        "ayarlar": {k: cfg().get(k) for k in
                                    ("remember_enabled", "remember_days",
                                     "session_ip_bind", "cookie_samesite")}})
        elif p == "/api/proxmox-link":
            _m, var, hata = proxmox_link_oku()
            self._json({"ok": not hata, "var": var, "url": proxmox_link_url(), "msg": hata})
        elif p == "/api/saglayicilar":
            self._json({"saglayicilar": saglayici_listesi()})
        elif p == "/api/ifaces":
            ifs = net_ifaces(); vars = default_iface()
            onerilen, nasil = wan_iface()
            self._json({"default": vars, "onerilen": onerilen, "onerilen_neden": nasil,
                        "ifaces": [{"name": k, "rx": v[0], "tx": v[1],
                                    "default": k == vars, "onerilen": k == onerilen,
                                    "kopru": kopru_mu(k),
                                    "hiz": iface_link_mbit(k)}
                                   for k, v in sorted(ifs.items())
                                   if not k.startswith(SANAL_ONEK)]})
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
                with _SEC_LOCK:
                    dusen = SESSIONS.pop(m.group(1), None)
                    if dusen and dusen.get("kalici"): DEPO._dusen.add(m.group(1))
                # Cikisin KIMDEN geldigi kaydedilir: kullanici mi tikladi, yoksa
                # baska bir sey mi tetikledi? Oturum sessizce kaybolmasin.
                log(f"cikis istegi: adres={client_ip(self)} "
                    f"referer={(self.headers.get('Referer') or '-')[:60]} "
                    f"csrf={'var' if self.headers.get('X-CSRF-Token') else 'YOK'} "
                    f"ua={(self.headers.get('User-Agent') or '-')[:50]}")
                if dusen and dusen.get("kalici"): DEPO.kalicilari_yaz("cikis")
            self._send(200, "application/json; charset=utf-8", '{"ok":true}', [self._cookie("", True)])
            return
        if not self._auth(need_csrf=True): return
        pid = q.get("plan", [""])[0]
        if path == "/api/plan/save":
            self._json(save_plan(self._body()))
        elif path == "/api/plan/delete":
            self._json(delete_plan(pid))
        elif path == "/api/ice-aktar":
            b2 = self._body()
            kip = "degistir" if b2.get("kip") == "degistir" else "ekle"
            try:
                veri = b2.get("veri")
                if isinstance(veri, str): veri = json.loads(veri)
            except Exception as e:
                self._json({"ok": False, "msg": f"dosya cozulemedi: {e}"}); return
            self._json(ice_aktar(veri, kip))
        elif path == "/api/telegram/test":
            self._json(tg_test(self._body().get("chat")))
        elif path == "/api/proxmox-link":
            self._json(proxmox_link_yaz(self._body().get("ekle", True)))
        elif path == "/api/oturum/kapat":
            b2 = self._body()
            benim = (self.sess or {}).get("_tok")
            if b2.get("hepsi"):
                n = 0
                with _SEC_LOCK:
                    hedef = [t for t, v in SESSIONS.items() if v.get("kalici") and t != benim]
                for t in hedef: n += oturum_kapat(t[:10], haric=benim)["ok"] and 1 or 0
                self._json({"ok": True, "msg": f"{n} oturum kapatildi (bu oturum haric)"})
            else:
                self._json(oturum_kapat(str(b2.get("onek") or "")[:16], haric=benim))
        elif path == "/api/remote/auth/start":
            self._json(auth_start(self._body().get("tur") or q.get("tur", ["drive"])[0]))
        elif path == "/api/remote/auth/finish":
            tok = auth_token()
            if not tok: self._json({"ok": False, "msg": "jeton henuz gelmedi"})
            else:
                b2 = self._body()
                # Tur, yetkilendirmeyi baslatan istekten tasinir: kullanici
                # arada baska bir tur secse bile jeton hangi saglayiciya aitse
                # hesap o turde olusur.
                r = remote_create(b2.get("name", ""), tok,
                                  _AUTH.get("tur") or b2.get("tur") or "drive")
                if r.get("ok"):
                    auth_stop()
                    try: os.remove(auth_out_yolu())
                    except Exception as e: yut("_post", e)
                self._json(r)
        elif path == "/api/remote/auth/cancel":
            auth_stop()
            try: os.remove(auth_out_yolu())
            except Exception as e: yut("_post", e)
            self._json({"ok": True, "msg": "iptal edildi"})
        elif path == "/api/remote/add":
            b = self._body()
            self._json(remote_create(b.get("name", ""), b.get("token", ""),
                                     b.get("tur") or "drive"))
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
    if data.get("dil") in ("tr", "en"): C["dil"] = data["dil"]
    if data.get("ui_pass"): C["ui_pass"] = str(data["ui_pass"])
    if data.get("smtp_pass"): C["smtp_pass"] = str(data["smtp_pass"])
    if "allow_account_cleanup" in data: C["allow_account_cleanup"] = bool(data["allow_account_cleanup"])
    if "cookie_secure" in data: C["cookie_secure"] = bool(data["cookie_secure"])
    if str(data.get("cookie_samesite", "")) in ("Lax", "Strict", "None"):
        C["cookie_samesite"] = data["cookie_samesite"]
    for k in ("failure_mail_to", "failure_smtp_profile", "telegram_chat_id"):
        if k in data: C[k] = str(data[k] or "")
    if "telegram_enabled" in data: C["telegram_enabled"] = bool(data["telegram_enabled"])
    if "telegram_token" in data:
        t = str(data["telegram_token"] or "").strip()
        # Bos gelirse mevcut jeton KORUNUR: arayuz jetonu hic geri gondermez,
        # yoksa her ayar kaydinda jeton silinirdi.
        if t and t != "********":
            if not RE_TG_TOKEN.match(t):
                return {"ok": False, "msg": "Telegram jetonu bicimi hatali "
                                            "(ornek: 123456789:AAE...)"}
            C["telegram_token"] = t
    for k in ("update_check", "update_auto", "debug", "remember_enabled", "lan_hep_acik",
              "sse_enabled", "failure_mail", "butunluk_mail"):
        if k in data: C[k] = bool(data[k])
    if str(data.get("session_ip_bind", "")) in ("ip", "ag", "yok"):
        C["session_ip_bind"] = data["session_ip_bind"]
    for k in ("remember_days", "session_timeout_min", "sse_watch_ms",
              "sse_heartbeat_sec", "sse_max_clients", "sse_ping_sec",
              "failure_mail_lines", "tick_uyari_dk"):
        if k in data:
            try: C[k] = max(1, int(data[k]))
            except Exception as e: yut("save_settings", e)
    if data.get("update_url"):
        yeni_url = str(data["update_url"])
        ok, sebep = guncelleme_adresi_gecerli(yeni_url)
        if not ok:
            log(f"GUVENLIK: guncelleme adresi degistirilmek istendi ama reddedildi "
                f"({yeni_url}): {sebep}")
            return {"ok": False, "msg": sebep}
        if yeni_url != C.get("update_url"):
            # Denetim izi: bu ayar root olarak ne calisacagini belirler
            log(f"GUVENLIK: guncelleme adresi degisti: {C.get('update_url')} -> {yeni_url}")
        C["update_url"] = yeni_url
    if "update_sha256" in data:
        oz = re.sub(r"[^0-9a-fA-F]", "", str(data["update_sha256"] or "")).lower()
        if oz and len(oz) != 64:
            return {"ok": False, "msg": "sha256 ozeti 64 onaltilik karakter olmali"}
        C["update_sha256"] = oz
    if isinstance(data.get("update_izinli_hostlar"), list):
        C["update_izinli_hostlar"] = [str(h).strip().lower()
                                      for h in data["update_izinli_hostlar"] if str(h).strip()]
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
    if do in ("backup", "prune", "purgetrash", "refresh", "testmail", "report",
              "hostconf") and not p:
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
    if do == "report":
        if not p.get("weekly_report", True):
            return {"ok": False, "msg": "bu planda haftalik rapor kapali"}
        if not (p.get("report_mail_to") or p.get("mail_to")):
            return {"ok": False, "msg": "rapor alicisi bos - plani duzenleyip mail adresi gir"}
        try:
            ok = send_report(p, trigger="manuel")
        except Exception as e:
            log(f"manuel rapor HATA: {e}", pid)
            return {"ok": False, "msg": f"rapor gonderilemedi: {e}"}
        return {"ok": ok, "msg": "rapor gonderildi" if ok else "rapor HATA (loga bak)"}
    if do == "hostconf":
        if is_running(pid): return {"ok": False, "msg": "yedek calisirken yapilamaz"}
        if not p.get("host_config_enabled", GLOBAL_DEFAULTS["host_config_enabled"]):
            return {"ok": False, "msg": "bu planda yapilandirma yedegi kapali"}
        y, n, hata = hostconf_yukle(p)
        if hata: return {"ok": False, "msg": f"yapilandirma yedegi HATA: {hata}"}
        return {"ok": bool(y), "msg": f"{n} yapilandirma dosyasi arsivlendi ve yuklendi"
                if y else "yapilandirma yuklenemedi (loga bak)"}
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
    # Host da yazilir: cerez HOST'A baglidir. Bir gun IP ile, ertesi gun sunucu
    # adiyla girilirse tarayici iki ayri cerez kavanozu kullanir ve oturum
    # "unutulmus" gorunur. Logdan hangi adresle gelindigi anlasilsin.
    log(f"giris basarili: {C.get('ui_user')} ({ip}) host={handler.headers.get('Host', '?')}"
        + (f" [hatirlaniyor, {int(C.get('remember_days') or 30)} gun]" if hatirla else ""))
    handler._send(302, "text/html; charset=utf-8", "",
                  [handler._cookie(tok, omur_sn=omur), ("Location", "/")])

def _izleyici_turu(onceki, yayin=True):
    """Diskteki degisiklikleri olaya cevirir. Yedegi calistiran surec ayri
    (pve-gdrive-tick), bu yuzden bellek uzerinden haberlesemiyoruz; tek ortak
    nokta dosya sistemi. Butun tarayicilar yerine tek bir dongu bakiyor.

    yayin=False iken taban cizgisi yine guncellenir ama olay uretilmez.
    Taban yalnizca abone varken tutulsaydi, baglanma ile ilk tur arasinda olan
    degisiklik sessizce tabana yazilir ve hicbir zaman bildirilmezdi."""
    C = cfg()
    yeni = dict(onceki)

    # 1) durum: state.json ya da yapilandirma degistiyse tam durumu it
    imza = []
    for yol in (C.get("state_file"), CONFIG_PATH):
        try: imza.append(os.stat(yol).st_mtime_ns)
        except Exception: imza.append(0)
    imza = tuple(imza)
    if imza != onceki.get("imza"):
        yeni["imza"] = imza
        if yayin and onceki.get("imza") is not None:   # ilk turda yayin yapma
            olay_yolla("durum", public_status())

    # 2) ilerleme: calisan planlarin ilerleme dosyalari
    ilerleme = {}
    for pl in C.get("plans", []):
        pr = get_progress(pl["id"])
        if pr: ilerleme[pl["id"]] = pr
    if ilerleme != onceki.get("ilerleme"):
        yeni["ilerleme"] = ilerleme
        if yayin and onceki.get("ilerleme") is not None:
            olay_yolla("ilerleme", ilerleme)
    # Calisma bitti: durum kesin degismistir, tazele
    if yayin and onceki.get("ilerleme") and not ilerleme:
        olay_yolla("durum", public_status())

    # 3) log: dosyanin yalnizca yeni kismi okunur, tamami degil
    lf = C.get("log_file")
    try:
        boyut = os.path.getsize(lf)
    except Exception:
        boyut = 0
    kaldigi = onceki.get("log_ofset")
    if kaldigi is None:
        yeni["log_ofset"] = boyut                # acilista gecmisi tekrar yollama
    elif boyut < kaldigi:
        yeni["log_ofset"] = boyut                # log dondu (rotate)
    elif boyut > kaldigi:
        try:
            with open(lf, "rb") as f:
                f.seek(kaldigi)
                ham = f.read(256 * 1024).decode("utf-8", "replace")
            tam = ham.rsplit("\n", 1)
            satirlar = [x for x in tam[0].split("\n") if x.strip()] if len(tam) > 1 else []
            yeni["log_ofset"] = kaldigi + len(ham.encode()) - len(tam[-1].encode()) \
                if len(tam) > 1 else kaldigi
            if yayin and satirlar: olay_yolla("log", {"satirlar": satirlar[-200:]})
        except Exception as e:
            yut("izleyici_log", e); yeni["log_ofset"] = boyut
    return yeni

def izleyici_dongusu(dur):
    aralik = max(0.2, float(cfg().get("sse_watch_ms") or 1000) / 1000.0)
    durum = {}
    while not dur.is_set():
        try:
            # Abone yokken de taban guncellenir (birkac stat cagrisi), ama
            # public_status() hesaplanmaz ve olay uretilmez.
            durum = _izleyici_turu(durum, yayin=bool(OLAY.abone_sayisi()))
        except Exception as e:
            yut("izleyici_dongusu", e)
        dur.wait(aralik)

def serve():
    global TLS_AKTIF
    C = cfg()
    ensure_hashed_pw()
    n = DEPO.kalicilari_yukle()
    if n: log(f"{n} hatirlanan oturum geri yuklendi")
    try: _KALICI_DAMGA["mtime"] = os.stat(oturum_dosyasi()).st_mtime_ns
    except Exception as e: yut("acilis_damga", e)
    # Eski surumler OAuth ciktisini /tmp altina, dunyaya okunabilir biraktiyordu.
    # Icinde jeton kalmis olabilir; acilista temizle.
    try:
        if os.path.exists(AUTH_OUT_ESKI):
            os.remove(AUTH_OUT_ESKI)
            log(f"GUVENLIK: eski OAuth cikti dosyasi silindi ({AUTH_OUT_ESKI}) - "
                f"dunyaya okunabilir bir konumda jeton kalmis olabilirdi")
    except Exception as e: yut("eski_auth_temizle", e)
    httpd = ThreadingHTTPServer((C["ui_bind"], int(C["ui_port"])), H)
    ctx = ssl_context()
    if ctx:
        httpd.socket = ctx.wrap_socket(httpd.socket, server_side=True)
        TLS_AKTIF = True
    sema = "https" if TLS_AKTIF else "http"
    log(f"web UI hazir -> {sema}://{C['ui_bind']}:{C['ui_port']}  (kullanici: {C['ui_user']})"
        + f" | surum {SURUM}")
    if ic_zamanlayici_acik():
        dur = threading.Event()
        threading.Thread(target=zamanlayici_dongusu, args=(dur,), daemon=True).start()
    if cfg().get("sse_enabled", True):
        threading.Thread(target=izleyici_dongusu, args=(threading.Event(),),
                         daemon=True).start()
        log(f"canli olay akisi acik (izleme {cfg().get('sse_watch_ms')} ms)")
    if TLS_AKTIF:
        b = cert_bilgisi() or {}
        log(f"TLS acik | sertifika: {b.get('konu')} | veren: {b.get('veren')} | bitis: {b.get('bitis')}")
    else:
        log("TLS kapali - arayuz duz HTTP. VPN disinda kullanma.")
    httpd.serve_forever()

def aglari_yonet(islem=None, deger=None):
    """Izinli ag listesini komut satirindan yonetir.

    Yanlis bir kisitlama yuzunden arayuze giremez duruma dusersen kurtarma yolu budur:
    sunucuda 'pve_gdrive.py aglar --ac' calistirip kisitlamayi kaldirirsin."""
    C = cfg()
    mevcut = list(C.get("allow_networks") or [])
    if islem is None:
        return {"ok": True, "aglar": mevcut}
    if islem == "ac":
        C["allow_networks"] = []
    elif islem == "ekle":
        try: ipaddress.ip_network(str(deger), strict=False)
        except Exception: return {"ok": False, "msg": f"gecersiz ag: {deger}"}
        if deger not in mevcut: mevcut.append(str(deger))
        C["allow_networks"] = mevcut
    elif islem == "cikar":
        C["allow_networks"] = [x for x in mevcut if x != deger]
    else:
        return {"ok": False, "msg": "bilinmeyen islem"}
    save_cfg(C)
    log(f"izinli aglar guncellendi: {C['allow_networks'] or '(kisitlama yok)'}")
    return {"ok": True, "aglar": C["allow_networks"]}

def disa_aktar(sirlar=False):
    """Planlari ve mail profillerini baska bir kuruluma tasimak icin JSON uretir.

    Varsayilan olarak SIR ICERMEZ: SMTP sifreleri, UI sifresi ve rclone jetonlari
    disari cikmaz. Hedef hostta hesaplar yeniden yetkilendirilir, mail sifreleri
    yeniden girilir. --sirlarla ile SMTP sifreleri de eklenir (dosyayi koru!)."""
    C = cfg()
    profiller = []
    for x in smtp_profiles(C):
        y = dict(x)
        if not sirlar: y["pass"] = ""
        profiller.append(y)
    return {
        "_aciklama": "pve-gdrive-backup ayar aktarimi. 'pve_gdrive.py ice-aktar < dosya' ile yukle.",
        "_surum": SURUM, "_zaman": now_str(), "_sirlar_dahil": bool(sirlar),
        "plans": [dict(p) for p in C.get("plans", [])],
        "smtp_profiles": profiller,
        "ayarlar": {k: C.get(k) for k in (
            "ui_port", "ui_user", "ui_refresh_sec", "browse_roots", "dump_regex",
            "history_max", "log_tail_lines", "rclone_tail_lines", "snapshot_max_rows",
            "log_max_mb", "log_keep", "rclone_timeout_min", "stats_interval_sec",
            "purge_batch", "quota_cache_min", "oneri_pay_pct", "allow_account_cleanup",
            "remember_enabled", "remember_days", "session_timeout_min",
            "update_check", "update_auto", "scheduler_interval_sec")},
    }

def proxmox_link_url():
    C = cfg()
    tls = bool(C.get("ssl_cert") and C.get("ssl_key")
               and os.path.exists(str(C.get("ssl_cert"))))
    return f"{'https' if tls else 'http'}://{local_ip() or os.uname().nodename}:{C.get('ui_port', 8787)}"

PVE_LINK_IM = "<!-- pve-gdrive -->"

def proxmox_link_oku():
    """Datacenter Notes'taki mevcut metin ve linkimiz orada mi."""
    if not shutil.which("pvesh"): return None, False, "pvesh yok (Proxmox host'u degil)"
    try:
        r = subprocess.run(["pvesh", "get", "/cluster/options", "--output-format", "json"],
                           capture_output=True, text=True, timeout=20)
        if r.returncode != 0:
            return None, False, (r.stderr or "pvesh okunamadi").strip()[:200]
        return json.loads(r.stdout or "{}").get("description", "") or "", \
               PVE_LINK_IM in (json.loads(r.stdout or "{}").get("description") or ""), ""
    except Exception as e:
        return None, False, str(e)[:200]

def proxmox_link_yaz(ekle=True):
    """Proxmox Datacenter Notes'a arayuz linki ekler/kaldirir.

    Proxmox'un hicbir dosyasina dokunulmaz, yalnizca not alani guncellenir -
    surum yukseltmelerinde kaybolmaz ve geri alinmasi tek tiktir."""
    mevcut, var, hata = proxmox_link_oku()
    if mevcut is None: return {"ok": False, "msg": hata}
    satirlar = [x for x in (mevcut or "").split("\n") if PVE_LINK_IM not in x]
    if ekle:
        url = proxmox_link_url()
        satirlar.append(f"{PVE_LINK_IM} [🗄️ Google Drive Yedek]({url})")
    yeni = "\n".join(x for x in satirlar if x.strip())
    try:
        r = subprocess.run(["pvesh", "set", "/cluster/options", "--description", yeni],
                           capture_output=True, text=True, timeout=20)
        if r.returncode != 0:
            return {"ok": False, "msg": (r.stderr or "yazilamadi").strip()[:200]}
    except Exception as e:
        return {"ok": False, "msg": str(e)[:200]}
    log(f"Proxmox Notes linki {'eklendi' if ekle else 'kaldirildi'}")
    return {"ok": True, "msg": ("Datacenter → Notes'a eklendi: " + proxmox_link_url())
            if ekle else "Datacenter → Notes'tan kaldirildi", "var": ekle}

def oturum_listesi(su_anki=None):
    """Acik 'beni hatirla' oturumlari. Jeton DISARI VERILMEZ; yalnizca kisa
    onek gosterilir ve islemler bu onekle yapilir."""
    simdi = time.time()
    out = []
    with _SEC_LOCK:
        kayit = dict(SESSIONS)
    for t, v in kayit.items():
        if not v.get("kalici"): continue
        out.append({"onek": t[:10], "kullanici": v.get("user"), "adres": v.get("ip"),
                    "olusma": datetime.fromtimestamp(v.get("created", 0)).strftime(TS_FMT)
                              if v.get("created") else "-",
                    "kalan_gun": round(max(0.0, (v.get("bitis", 0) - simdi) / 86400), 1),
                    "bu_mu": bool(su_anki and t == su_anki)})
    return sorted(out, key=lambda x: x["kalan_gun"], reverse=True)

def oturum_kapat(onek, haric=None):
    """Onekle eslesen kalici oturumu dusurur. haric verilirse o oturuma dokunulmaz
    (kullanici kendi oturumunu yanlislikla kapatmasin diye cagiran taraf verir)."""
    dusen = 0
    with _SEC_LOCK:
        for t in [t for t, v in SESSIONS.items()
                  if v.get("kalici") and t.startswith(onek) and t != haric]:
            SESSIONS.pop(t, None); DEPO._dusen.add(t); dusen += 1
    if dusen: DEPO.kalicilari_yaz("arayuzden kapatildi")
    return {"ok": bool(dusen),
            "msg": f"{dusen} oturum kapatildi" if dusen else "eslesen oturum yok"}

def ice_aktar(veri, plan_kipi="ekle"):
    """Disa aktarilmis ayarlari yukler.

    plan_kipi: 'ekle' (mevcutlarin uzerine ekler, ayni id varsa yenisi atlanir)
               'degistir' (mevcut planlarin yerine gecer)
    UI sifresi, TLS yollari ve izinli aglar ASLA aktarilmaz: bunlar hosta ozeldir.
    Planlar KAPALI gelir; hesap ve klasor dogrulanmadan yedek baslamasin."""
    if not isinstance(veri, dict): return {"ok": False, "msg": "gecersiz dosya"}
    C = cfg()
    mevcut = {p["id"] for p in C.get("plans", [])}
    gelen = [norm_plan({**p, "enabled": False}) for p in (veri.get("plans") or [])]
    if plan_kipi == "degistir":
        C["plans"] = gelen; eklenen = len(gelen); atlanan = 0
    else:
        yeni = [p for p in gelen if p["id"] not in mevcut]
        atlanan = len(gelen) - len(yeni)
        C["plans"] = list(C.get("plans", [])) + yeni
        eklenen = len(yeni)
    mevcut_smtp = {x["id"] for x in smtp_profiles(C)}
    yeni_smtp = [norm_smtp(x) for x in (veri.get("smtp_profiles") or [])
                 if norm_smtp(x)["id"] not in mevcut_smtp]
    C["smtp_profiles"] = list(C.get("smtp_profiles", [])) + yeni_smtp
    for k, v in (veri.get("ayarlar") or {}).items():
        if k in GLOBAL_DEFAULTS and v is not None: C[k] = v
    save_cfg(C)
    sifresiz = [x["name"] for x in yeni_smtp if not x.get("pass")]
    log(f"ayar aktarimi: {eklenen} plan, {len(yeni_smtp)} mail profili yuklendi")
    return {"ok": True, "eklenen_plan": eklenen, "atlanan_plan": atlanan,
            "eklenen_smtp": len(yeni_smtp), "sifresiz_smtp": sifresiz,
            "msg": f"{eklenen} plan ve {len(yeni_smtp)} mail profili yuklendi. "
                   f"Planlar KAPALI geldi: hedef hesabi secip etkinlestir."}

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
<title>Giriş — Proxmox Yedek</title>
<link rel="icon" href="data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 64 64'%3E%3Crect width='64' height='64' rx='14' fill='%230d1117'/%3E%3Cpath d='M20 40 L32 18 L44 40 Z' fill='%23e57000'/%3E%3Cpath d='M14 44h36a3 3 0 000-6h-1.2a10 10 0 00-19.3-3.4A7 7 0 0014 44Z' fill='%2358a6ff'/%3E%3Cpath d='M32 46v9m0 0-4.5-4.5M32 55l4.5-4.5' stroke='%237ee2a8' stroke-width='3.2' stroke-linecap='round' stroke-linejoin='round' fill='none'/%3E%3C/svg%3E"><style>
*{box-sizing:border-box;margin:0;padding:0}
body{background:#0d1117;color:#e6edf3;font:14px/1.5 -apple-system,Segoe UI,Roboto,Arial;
 display:flex;flex-direction:column;align-items:center;justify-content:center;
 min-height:100vh;padding:20px}
/* Kart ve altindaki damga tek sutun: yon verilmezse body'nin flex'i ikisini
   YAN YANA diziyor ve damga kartin sagina kaciyordu. */
.box{background:#161b22;border:1px solid #232b36;border-radius:14px;padding:26px;width:100%;max-width:380px}
.girislogo{width:56px;height:56px;margin:0 auto 12px;display:block}
.girislogo svg{width:100%;height:100%}
.box h1{text-align:center}
.box .sub{text-align:center}
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
.damga{margin-top:14px;font-size:11px;color:#6b7785;width:100%;max-width:380px;
 display:flex;gap:6px;justify-content:center;align-items:center;flex-wrap:wrap}
.damga-ad{color:#8b97a5}
.damga-surum{color:#8b97a5;font-family:ui-monospace,SFMono-Regular,Consolas,monospace}
.damga-ayrac{opacity:.45}
.damga-tls{padding:1px 6px;border-radius:999px;font-weight:700;font-size:10px}
.damga-tls.acik{background:#16301f;color:#7ee2a8}
.damga-tls.kapali{background:#3a1d1d;color:#ff9b9b}
.foot{font-size:11px;color:#6e7c8c;margin-top:16px;text-align:center;line-height:1.6}
.hatirla{align-items:center;gap:8px;margin-top:14px;font-size:13px;color:#9fb4c9;cursor:pointer}
.hatirla input{width:auto;margin:0}
</style></head><body>
<form class="box" method="POST" action="/login" autocomplete="off">
  <div class="girislogo"><svg viewBox="0 0 64 64" fill="none" xmlns="http://www.w3.org/2000/svg" aria-hidden="true">  <rect x="2" y="2" width="60" height="60" rx="14" fill="#161b22" stroke="#2d3d50" stroke-width="2"/>  <path d="M20 40 L32 18 L44 40 Z" fill="#e57000" opacity=".92"/>  <path d="M14 44 h36 a3 3 0 0 0 0-6 h-1.2a10 10 0 0 0-19.3-3.4A7 7 0 0 0 14 44Z" fill="#58a6ff" opacity=".95"/>  <path d="M32 46 v9 m0 0 -4.5-4.5 M32 55 l4.5-4.5" stroke="#7ee2a8" stroke-width="3.2"        stroke-linecap="round" stroke-linejoin="round"/></svg></div>
  <h1>Proxmox → Drive Yedek</h1>
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
<div class="damga">
  <span class="damga-ad">pve-gdrive-backup</span>
  <span class="damga-surum">v{{SURUM}}</span>
  <span class="damga-ayrac">·</span>
  <span title="Bağlandığın sunucu">{{SUNUCU}}</span>
  <span class="damga-ayrac">·</span>
  <span class="damga-tls {{TLSSINIF}}" title="{{TLSIPUCU}}">{{TLS}}</span>
</div>
<script>
function yenile(){var r=Math.random().toString(36).slice(2);
 fetch("/captcha.svg?cid=").then(function(){});
 location.reload()}
</script></body></html>"""

# --- UI BUNDLE START (build_ui.py uretir, elle duzenleme) ---
HTML = r'''<!doctype html><html lang="tr"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Proxmox → Drive Yedek</title>
<link rel="icon" href="data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 64 64'%3E%3Crect width='64' height='64' rx='14' fill='%230d1117'/%3E%3Cpath d='M20 40 L32 18 L44 40 Z' fill='%23e57000'/%3E%3Cpath d='M14 44h36a3 3 0 000-6h-1.2a10 10 0 00-19.3-3.4A7 7 0 0014 44Z' fill='%2358a6ff'/%3E%3Cpath d='M32 46v9m0 0-4.5-4.5M32 55l4.5-4.5' stroke='%237ee2a8' stroke-width='3.2' stroke-linecap='round' stroke-linejoin='round' fill='none'/%3E%3C/svg%3E">
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

/* --- kapasite planlayici --- */
.kapasite{background:#0f151d;border:1px solid #232b36;border-radius:10px;padding:12px;margin-bottom:14px}
.kap-durum{font-size:12px;color:#9fb4c9;line-height:1.6}
.kap-durum b{color:#e6edf3}
.kap-bar{display:flex;height:14px;border-radius:7px;background:#232b36;overflow:hidden;margin:10px 0 6px}
.kap-bar i{display:block;height:100%}
.kap-bar i.mevcut{background:#4b5b6d}
.kap-bar i.yeni{background:#3fb950}
.kap-bar.uyari i.yeni{background:#d29922}
.kap-bar.tasma i.yeni{background:#f85149}
.kap-alt{font-size:11px;color:#8b97a5;display:flex;justify-content:space-between;gap:8px;flex-wrap:wrap}
.kap-misafir{margin-top:10px;font-size:11px;color:#8b97a5}
.kap-misafir table{width:100%;font-size:11px}
.kap-misafir td{padding:2px 6px;border:none}
.kap-misafir td:last-child{text-align:right;color:#c9d4df}
.kap-uyari{color:#ffd479;margin-top:8px;font-size:12px;line-height:1.5}
.kap-hata{color:#ff9b9b}

/* --- logo --- */
.logo{display:inline-flex;width:34px;height:34px;flex:0 0 34px}
.logo svg{width:100%;height:100%}
.girislogo{width:56px;height:56px;margin:0 auto 10px;display:block}

/* --- surukleme --- */
.modal > h2{cursor:move;user-select:none}
.modal > h2::before{content:"⠿";opacity:.35;margin-right:8px;font-size:13px;letter-spacing:-2px}
.mask.suruklenen{transition:none}

/* Dogrulama sonrasi odaklanan alan kisa sure vurgulanir */
.odak{animation:odakYan 1.4s ease-out}
@keyframes odakYan{
  0%,55%{box-shadow:0 0 0 3px rgba(255,107,107,.55)}
  100%{box-shadow:0 0 0 0 rgba(255,107,107,0)}
}

/* ---------- sag tik menusu ---------- */
.ctx{position:fixed;z-index:900;min-width:230px;max-width:340px;padding:5px;
  background:#161b22;border:1px solid #30363d;border-radius:10px;
  box-shadow:0 12px 32px rgba(0,0,0,.55);font-size:13px;
  animation:ctxGir .09s ease-out}
@keyframes ctxGir{from{opacity:0;transform:translateY(-3px)}to{opacity:1;transform:none}}
.ctx hr{border:0;border-top:1px solid #232b36;margin:5px 6px}
.ctx-baslik{padding:6px 10px 7px;color:#8b97a5;font-size:11px;font-weight:700;
  letter-spacing:.05em;text-transform:uppercase;overflow:hidden;
  text-overflow:ellipsis;white-space:nowrap}
.ctx-oge{display:flex;align-items:center;gap:9px;padding:7px 10px;border-radius:6px;
  cursor:pointer;color:#e6edf3;white-space:nowrap}
.ctx-oge:hover,.ctx-oge.on{background:#1f6feb;color:#fff}
.ctx-oge.tehlike{color:#ff9b9b}
.ctx-oge.tehlike:hover,.ctx-oge.tehlike.on{background:#8a2b2b;color:#fff}
.ctx-oge.pasif{color:#5b6472;cursor:default}
.ctx-oge.pasif:hover{background:transparent;color:#5b6472}
.ctx-simge{width:17px;text-align:center;flex:0 0 auto;opacity:.9}
.ctx-metin{overflow:hidden;text-overflow:ellipsis}

/* Zamanlayici/servis saglik uyarisi */
.uyari-kutu{border-color:#8a2b2b;background:#1f1414;margin-bottom:12px}
.uyari-kutu code{background:#2a1a1a;padding:1px 5px;border-radius:4px}

/* Baslikta oturum sahibi ve cikis */
.oturum{display:flex;align-items:center;gap:7px;padding-left:10px;
  margin-left:4px;border-left:1px solid #232b36}
.oturum .kul{font-size:12px;color:#8b97a5;font-weight:600;max-width:130px;
  overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.oturum .kul:before{content:"👤 ";opacity:.7}

/* Yedek hedef satirlari */
.yh-satir{display:flex;gap:6px;align-items:center}
.uyari-metin{color:#ffd479}

</style></head><body>
<div class="wrap">
<header>
  <span class="logo"><svg viewBox="0 0 64 64" fill="none" xmlns="http://www.w3.org/2000/svg" aria-hidden="true">
  <rect x="2" y="2" width="60" height="60" rx="14" fill="#161b22" stroke="#2d3d50" stroke-width="2"/>
  <path d="M20 40 L32 18 L44 40 Z" fill="#e57000" opacity=".92"/>
  <path d="M14 44 h36 a3 3 0 0 0 0-6 h-1.2a10 10 0 0 0-19.3-3.4A7 7 0 0 0 14 44Z" fill="#58a6ff" opacity=".95"/>
  <path d="M32 46 v9 m0 0 -4.5-4.5 M32 55 l4.5-4.5" stroke="#7ee2a8" stroke-width="3.2"
        stroke-linecap="round" stroke-linejoin="round"/>
</svg></span>
  <h1>Proxmox → Google Drive Yedek</h1>
  <span class="pill idle" id="canli" title="Canlı akış durumu">○ yoklama</span>
  <span id="tlsrozet"></span>
  <span id="uprozet"></span>
  <span class="muted" id="hinfo"></span>
  <span style="flex:1"></span>
  <select id="dilsec" onchange="dilDegistir(this.value)" title="Dil / Language"
          style="width:auto;padding:5px 8px;font-size:12px">
    <option value="tr">🇹🇷 Türkçe</option>
    <option value="en">🇬🇧 English</option>
  </select>
  <button onclick="openSettings()" title="Hesaplar, mail profilleri, güvenlik ve gelişmiş ayarlar">⚙ Ayarlar</button>
  <button class="primary" onclick="openEditor(null)">+ Yeni Plan</button>
  <span class="oturum">
    <span class="kul" id="kullanici" title="Oturum sahibi"></span>
    <button class="sm" onclick="logout()" title="Oturumu kapat">⎋ Çıkış</button>
  </span>
</header>

<div class="hesaplar" id="hesapserit"></div>
<div class="card uyari-kutu" id="saglik" style="display:none"></div>
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
      <button class="sm" onclick="preset('az')" title="Her gün 03:00, Drive'da 3 gün, çöpte 1 gün. En az yer kaplayan seçenek; son birkaç günü korur.">🗜️ Az yer — 3 gün</button>
      <button class="sm" onclick="preset('gunluk')" title="Her gün 03:00, Drive'da 14 gün, çöpte 1 gün. Daha geniş geri dönüş penceresi, daha çok yer.">📅 Günlük — 14 gün</button>
      <button class="sm" onclick="preset('haftalik')" title="Her Pazar 05:00, Drive'da 180 gün, çöpte 7 gün, düşük hız. Uzun süreli arşiv.">🗄️ Haftalık arşiv — 6 ay</button>
      <button class="sm" onclick="preset('kritik')" title="Her gün 02:00, Drive'da 30 gün, VM/CT başına en az 7 set, çöpte 3 gün.">🔒 Kritik — 30 gün</button>
      <button class="sm" onclick="preset('test')" title="Her gün, 2 gün sakla, çöpte yarım gün. Kurulumu denemek için.">🧪 Test</button>
    </div>
    <div class="hint">Senaryo seçmek formu doldurur; sonra istediğini değiştirebilirsin. Kaydetmeden hiçbir şey uygulanmaz.<br>
      <b>Gün sayısı arttıkça yer de artar.</b> 4. adımdaki kapasite paneli seçtiğin sürenin
      kaç GB tuttuğunu ve kotanın yüzde kaçını kullanacağını anında gösterir.</div>
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
  <fieldset><legend>Hedef (bulut hesabı)</legend>
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
  <fieldset><legend>Yedek hedefler (opsiyonel)</legend>
    <div class="eg" style="margin-bottom:8px">Birincil hedefe yazamazsa sırayla
      bunlar denenir. İlk başarılı olan kullanılır.
      <b>Eski yedekler yalnızca yazılan hedefte temizlenir</b> — yedeğe düştüğün gün
      birincideki yedeklere dokunulmaz, hesap düzelince orada duruyor olurlar.</div>
    <div id="e-yh-liste"></div>
    <div class="inline" style="margin-top:8px">
      <button class="sm" type="button" onclick="yhEkle()">＋ Yedek hedef ekle</button>
      <span class="small" id="e-yh-ozet"></span>
    </div>
    <div class="eg" style="margin-top:8px">Farklı bir sağlayıcı da olabilir
      (Dropbox, OneDrive, Box…). <b>Ayarlar → Hesaplar</b>'tan ekleyip burada seçersin.
      Yedek hedef <b>farklı bir hesapta</b> olursa asıl korumayı sağlar; aynı hesabın
      başka klasörü, hesap kilitlenirse işe yaramaz.</div>
  </fieldset>
</div>
<div class="wstep" data-step="4" style="display:none">
  <div class="wbaslik"><b>4. Saklama</b> <span class="small">Yedekler ne kadar süre kalsın</span></div>
  <fieldset><legend>Saklama süreleri</legend>
    <div id="kap-panel" class="kapasite">
      <div class="kap-durum" id="kap-durum">Kaynak ve hesap seçilince kapasite hesabı burada çıkar.</div>
      <div class="kap-bar" id="kap-bar" style="display:none">
        <i class="mevcut" id="kap-mevcut"></i><i class="yeni" id="kap-yeni"></i>
      </div>
      <div class="kap-alt" id="kap-alt" style="display:none"></div>
      <div class="btns" id="kap-btn" style="display:none;margin-top:8px">
        <button class="sm" onclick="kapasiteOner()" id="kap-oner">📐 Sığabilecek en uzun süreyi uygula</button>
        <button class="sm" onclick="kapasiteYukle(true)">↻ Yeniden ölç</button>
      </div>
      <div id="kap-misafir" class="kap-misafir"></div>
    </div>
    <div class="f"><label class="tip" title="Bu günden eski yedek setleri Google çöp kutusuna gönderilir. Süre dosyanın adındaki tarihe göre hesaplanır.">Drive'da tut (gün)</label>
      <div><input type="number" min="0" id="e-kd"><div class="errmsg" id="err-kd"></div>
        <div class="eg" id="eg-kd">Bu süreden eski setler Google çöp kutusuna gönderilir.</div></div></div>
    <div class="f"><label class="tip" title="Güvenlik tabanı: VM/CT başına bu kadar set, gün sınırına bakılmadan korunur.">En az set (adet)</label>
      <div><input type="number" min="0" id="e-kc"><div class="errmsg" id="err-kc"></div>
        <div class="eg" id="eg-kc">Gün kuralından muaf güvenlik tabanı.</div></div></div>
    <div class="f"><label class="tip" title="Google çöp kutusunda bekleme süresi. Bu süre dolunca kalıcı silinir ve kota boşalır.">Çöpte bekle (gün)</label>
      <div><input type="number" min="0" step="0.5" id="e-td"><div class="errmsg" id="err-td"></div>
        <div class="eg" id="eg-td">Çöpte bekleme süresi.</div></div></div>
  </fieldset>
  <fieldset><legend>Host yapılandırması</legend>
    <div class="eg" style="margin-bottom:8px">vzdump yalnızca <b>disk</b> yedeği alır.
      Host çökerse elinde diskler olur ama onları nereye geri yükleyeceğini anlatan
      hiçbir şey olmaz: depo tanımları, ağ yapılandırması, hangi CT hangi köprüde…
      Bu arşiv onu kapatır ve <b>~25 KB</b> yer kaplar.</div>
    <div class="frm">
      <label for="e-hc">Yapılandırmayı da yedekle</label>
      <div><label class="chk"><input type="checkbox" id="e-hc"> <span>Açık</span></label>
        <div class="eg">Her çalışmada tarihli bir arşiv: <code>/etc/pve</code>,
          <code>/etc/network/interfaces</code>, <code>/etc/fstab</code>, apt kaynakları.</div></div>
      <label for="e-hcj">pvesh JSON görüntüsü</label>
      <div><label class="chk"><input type="checkbox" id="e-hcj"> <span>Açık</span></label>
        <div class="eg">Proxmox REST ağacının okunabilir anlık görüntüsü. Geri yükleme
          aracı değil; "ne vardı, ne değişti" sorusunu gözle cevaplamak için.</div></div>
      <label for="e-hck">Saklanacak arşiv (adet)</label>
      <div><input type="number" min="0" max="999" id="e-hck">
        <div class="errmsg" id="err-hck"></div>
        <div class="eg">Arşivler küçük olduğu için gün kuralından bağımsız bir tabanı var.
          Gün sınırını aşanlar bu sayının altına düşmedikçe silinmez.</div></div>
    </div>
    <div class="eg" style="margin-top:8px;border-left:3px solid #d29922;padding-left:9px">
      <b>Özel anahtarlar alınmaz.</b> <code>/etc/pve/priv/</code> içi varsayılan olarak
      dışarıdadır; yalnızca <code>authorized_keys</code> ve <code>known_hosts</code>
      (açık anahtar listeleri) girer. Küme CA anahtarı ve <code>authkey.key</code>
      şifresiz Drive'a çıkmaz — tek düğümlü kurulumda bunlar geri yüklemede yeniden
      üretilir. Ayrıntı: <b>docs/GERİ-YÜKLEME.md</b>
    </div>
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
      <div class="f"><label class="tip" title="Hat kapasitesi ölçülerek mi bulunsun, elle mi girilsin?">Hat kapasitesi</label>
        <div><select id="e-bwlmode">
            <option value="ogren">Ölç ve öğren (önerilen)</option>
            <option value="manuel">Elle gireceğim</option>
          </select>
          <div class="eg" id="eg-bwlmode">Arayüzün bağ hızı internet yükleme hızını
            <b>göstermez</b>: 4×1 Gbit bond'un arkasında 60 Mbit'lik bir ISS hattı olabilir.
            Öğrenme kipinde araç, kendi sınırına dayanmadığı anlarda fiilen ulaştığı en
            yüksek sürekli hızı ölçer ve onu temel alır. Ölçülmüş bir alt sınır,
            uydurulmuş bir üst sınırdan iyidir.</div></div></div>
      <div class="f" id="bwlink-satir"><label class="tip" title="Yalnızca elle kipte kullanılır.">Elle hat kapasitesi</label>
        <div><input id="e-bwlink" placeholder="12M"><div class="errmsg" id="err-bwlink"></div>
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
        <div class="hint">Rapor içinde: çalışma sayıları, yüklenen/silinen dosyalar, kota, <b>VM/CT bazında son yedek tarihi</b> ve uyarılar.</div></div></div>
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
  <fieldset><legend>Telegram bildirimi</legend>
    <div class="eg" style="margin-bottom:8px">Mail bazen geç gelir ya da spam'e düşer.
      Telegram anlık: yedek biter bitmez, servis çökünce, betik değişince mesaj düşer.
      Botu <b>@BotFather</b>'dan oluşturup jetonu buraya yapıştır.</div>
    <div class="frm">
      <label for="g-tg">Telegram bildirimi</label>
      <div><label class="chk"><input type="checkbox" id="g-tg"> <span>Açık</span></label>
        <div class="eg">Kapalıyken hiçbir mesaj gönderilmez.</div></div>
      <label for="g-tgtoken">Bot jetonu</label>
      <div><input id="g-tgtoken" type="password" placeholder="123456789:AAE..." autocomplete="off">
        <div class="errmsg" id="err-tgtoken"></div>
        <div class="eg" id="g-tgtokenhint">Bu alan <b>hiçbir zaman geri gösterilmez</b>.
          Boş bırakırsan mevcut jeton korunur.</div></div>
      <label for="g-tgchat">Sohbet (chat id)</label>
      <div><input id="g-tgchat" placeholder="123456789">
        <div class="eg">Kişisel sohbette kendi id'in, grupta grup id'si (eksi ile başlar).
          Bilmiyorsan bota bir mesaj at, sonra
          <code>api.telegram.org/bot&lt;jeton&gt;/getUpdates</code> adresinden gör.</div></div>
    </div>
    <div class="btns" style="margin-top:8px">
      <button class="sm" onclick="tgTest()">✈ Test mesajı gönder</button>
      <span class="small" id="g-tgdurum"></span>
    </div>
    <div class="eg" style="margin-top:8px">Plan bazında kapatılabilir; bir plan kendi
      sohbetine de yazabilir (plan düzenlemede <b>Bildirim</b> adımı).</div>
  </fieldset>
  <fieldset><legend>Bakım ve taşıma</legend>
    <div class="f"><label class="tip" title="Planları ve mail profillerini başka bir kuruluma taşı.">Ayarları taşı</label>
      <div>
        <div class="btns">
          <button class="sm" onclick="ayarIndir(false)">⬇ Ayarları indir</button>
          <button class="sm" onclick="ayarIndir(true)">⬇ Şifrelerle indir</button>
          <button class="sm" onclick="ayarYukleAc()">⬆ Ayar dosyası yükle</button>
          <input type="file" id="s-dosya" accept=".json,application/json" style="display:none">
        </div>
        <div class="eg">İndirilen dosya <b>sır içermez</b>: SMTP şifreleri, arayüz şifresi ve
          Google jetonları çıkmaz. Hedef kurulumda hesapları yeniden yetkilendirir,
          mail şifrelerini yeniden girersin. "Şifrelerle indir" yalnızca SMTP şifrelerini
          ekler — dosyayı koru.</div>
        <div class="eg">Yüklenen planlar <b>kapalı</b> gelir; hesap ve klasörü
          gözden geçirdikten sonra sen açarsın. Arayüz şifresi, TLS yolları ve
          izinli ağlar hiçbir zaman aktarılmaz — bunlar host'a özeldir.</div>
      </div></div>
    <div class="f"><label class="tip" title="Proxmox arayüzünde Datacenter → Notes alanına tıklanabilir link koyar.">Proxmox linki</label>
      <div>
        <div class="btns">
          <button class="sm" onclick="pveLink(true)">🔗 Proxmox'a link ekle</button>
          <button class="sm warn" onclick="pveLink(false)">Kaldır</button>
        </div>
        <div class="eg" id="s-pvelink">durum okunuyor…</div>
        <div class="eg">Proxmox'un hiçbir dosyasına dokunulmaz, yalnızca
          <b>Datacenter → Notes</b> alanı güncellenir; sürüm yükseltmelerinde kaybolmaz.</div>
      </div></div>
  </fieldset>
  <fieldset><legend>Açık oturumlar</legend>
    <div class="eg" style="margin-bottom:8px">"Beni hatırla" ile açılmış oturumlar.
      Bir cihazı kaybettiysen buradan kapat. <b>Çıkış yapmak hatırlanan oturumu siler</b> —
      hatırlamayı denemek için çıkışa basma, sekmeyi kapatıp tekrar aç.</div>
    <div id="s-oturumlar">yükleniyor…</div>
    <div class="btns" style="margin-top:8px">
      <button class="sm" onclick="oturumlariYukle()">↻ Yenile</button>
      <button class="sm warn" onclick="oturumKapat(null, true)">Diğer tüm oturumları kapat</button>
    </div>
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
  <fieldset><legend>Yeni bulut hesabı yetkilendir</legend>
    <div class="f"><label class="tip" title="Hangi bulut sağlayıcısı. Hepsi tarayıcı ile yetkilendirilir.">Sağlayıcı</label>
      <div><select id="a-tur" onchange="saglayiciDegisti()"></select>
        <div class="eg" id="a-turhint"></div></div></div>
    <div class="f"><label class="tip" title="Plan hedefinde görünecek kısa ad.">Hesap adı</label>
      <div><input id="a-name" placeholder="ortak-hesap"><div class="errmsg" id="err-aname"></div>
        <div class="eg">Sadece harf, rakam, <code>-</code> ve <code>_</code>. Örnek: <code>kisisel</code>, <code>ortak-hesap</code></div></div></div>
    <div class="tabs" style="margin:4px 0 10px">
      <button id="a-tab1" class="on" onclick="acctTab(1)">Tarayıcıyla yetkilendir</button>
      <button id="a-tab2" onclick="acctTab(2)">Hazır jetonu yapıştır</button>
    </div>
    <div id="a-m1">
      <div class="hint">Sağlayıcı onaydan sonra tarayıcıyı <b>senin bilgisayarındaki</b> 127.0.0.1 adresine yönlendirir.
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
<div class="mask" id="m-onay"><div class="modal" style="max-width:440px">
  <h2 id="onay-baslik">Onay</h2>
  <div id="onay-metin" class="small" style="margin:10px 0 4px;line-height:1.6"></div>
  <div id="onay-girdi-sar" style="display:none;margin-top:10px">
    <input id="onay-girdi" autocomplete="off">
  </div>
  <div class="mbtns">
    <button id="onay-hayir" onclick="onayKapat(false)">Vazgeç</button>
    <button id="onay-evet" class="primary" onclick="onayKapat(true)">Tamam</button>
  </div>
</div></div>

<div class="flash" id="flash"></div>

<script>
"use strict";
/** Arka ucun /api/status ile dondurdugu veri yapilari. */
/**
 * İngilizce sözlük. Anahtar = Türkçe metnin kendisi.
 * Karşılığı olmayan metin Türkçe görünür (arayüz bozulmaz).
 */
const EN = {
    /* --- başlık ve genel --- */
    "🗄️ Proxmox → Google Drive Yedek": "🗄️ Proxmox → Google Drive Backup",
    "⚙ Ayarlar": "⚙ Settings", "+ Yeni Plan": "+ New Plan", "Vazgeç": "Cancel",
    "Kaydet": "Save", "Kapat": "Close", "Sil": "Delete", "Ekle": "Add", "Test": "Test",
    "Düzenle": "Edit", "‹ Geri": "‹ Back", "İleri ›": "Next ›", "— seç —": "— select —",
    "Seçim": "Choice", "Özet": "Summary", "Log": "Log", "Tümü": "All", "Sistem": "System",
    "plan": "plan", "çalışıyor": "running", "durum: ": "status: ",
    "mail profili yok": "no mail profile", "Hesap": "Account", "Klasör": "Folder",
    "Sunucu": "Server", "Port": "Port", "Kullanıcı": "Username", "Şifre": "Password",
    "Gönderen": "From", "Güvenlik": "Security", "Alıcı": "Recipient", "Saat": "Time",
    "Günler": "Days", "Gün": "Day", "gün": "days", "saat": "hours", "sn": "s", "dk": "min",
    "set": "sets", "adet": "count", "her gün": "every day", "hiçbiri": "none",
    "kapalı": "disabled", "etkin": "enabled", "atlandı": "skipped", "başarılı": "successful",
    "hata": "error", "yok": "none", "boş": "free", "çöp": "trash", "Çöp": "Trash",
    "hazır": "ready", "Kurulum": "Setup", "Örnek:": "Example:", "Anahtar": "Key",
    "Varsayılan": "Default", "Açıklama": "Description", "min": "min",
    /* --- plan kartı --- */
    "● ÇALIŞIYOR": "● RUNNING", "KAPALI": "DISABLED", "✔ BAŞARILI": "✔ SUCCESS",
    "✖ HATA": "✖ FAILED", "⏸ ATLANDI": "⏸ SKIPPED",
    "Kaynak": "Source", "Hedef": "Target", "Program": "Schedule", "Sonraki": "Next",
    "Saklama": "Retention", "Son çalışma": "Last run", "Haftalık rapor": "Weekly report",
    "▶ Yedekle": "▶ Back up", "🧹 Retention": "🧹 Retention", "🗑 Çöpü Boşalt": "🗑 Empty Trash",
    "📊 Rapor": "📊 Report", "✉ Test": "✉ Test", "✎ Düzenle": "✎ Edit",
    " dosya)": " files)", " gün": " days", " set": " sets", " · min ": " · min ",
    " set · çöp ": " sets · trash ",
    /* --- ilerleme --- */
    "Başlangıç": "Started", "Geçen": "Elapsed", "Aktarılan": "Transferred", "Hız": "Speed",
    "Kalan süre": "Time left", "Tahmini bitiş": "Est. finish", "Çalışıyor": "Running",
    "Hazırlanıyor": "Preparing", "Drive'a yükleniyor": "Uploading to Drive",
    "Drive listeleniyor": "Listing Drive", "Proxmox yedeği bekleniyor": "Waiting for Proxmox backup",
    "Eski yedekler çöpe taşınıyor": "Moving old backups to trash",
    "Çöp kutusu temizleniyor": "Emptying trash", "Durum güncelleniyor": "Updating status",
    "Bu iş sunucuda çalışıyor — sayfayı yenilesen de kapatsan da devam eder.": "This job runs on the server — it continues even if you refresh or close the page.",
    /* --- detay kartları --- */
    "Yedek dosyası": "Backup files", "Toplam boyut": "Total size",
    "Son yedek yaşı": "Newest backup age", "Çöpte bekleyen": "Waiting in trash",
    "Çöp süresi": "Trash period", "Google Drive kullanımı": "Google Drive usage",
    "Yedekler (Drive)": "Backups (Drive)", "Tarih": "Date", "VM/CT": "VM/CT",
    "Boyut": "Size", "Dosya": "File", "Kalan": "Left", "yedek yok": "no backups",
    "Çalışma geçmişi": "Run history", "Zaman": "Time", "Durum": "Status", "Tetik": "Trigger",
    "kayıt yok": "no records", "süre dolunca kalıcı silinir": "permanently deleted when time is up",
    "Google çöp kutusu (": "Google trash (",
    /* --- sihirbaz --- */
    "🧭 Yeni plan sihirbazı": "🧭 New plan wizard",
    "Adım adım ilerle. Hiçbir şey kaydedilmez, son adımda onaylarsın.": "Go step by step. Nothing is saved until you confirm on the last step.",
    "Tüm ayarlar tek sayfada. Alan adlarının üstüne gelince açıklama çıkar.": "All settings on one page. Hover a field label for an explanation.",
    "1. Plan": "1. Plan", "2. Kaynak": "2. Source", "3. Hedef": "3. Target",
    "4. Saklama": "4. Retention", "5. Zamanlama": "5. Schedule", "6. Aktarım": "6. Transfer",
    "7. Bildirim": "7. Notify", "8. Özet": "8. Summary",
    "Bu plana bir ad ver": "Give this plan a name",
    "Proxmox'ta hangi klasör yedeklenecek": "Which folder on Proxmox to back up",
    "Hangi Google hesabına, hangi klasöre": "Which Google account and folder",
    "Yedekler ne kadar süre kalsın": "How long backups are kept",
    "Ne zaman çalışsın, çakışma nasıl önlensin": "When to run and how to avoid conflicts",
    "Hız ve kaynak kullanımı": "Speed and resource usage",
    "Kim, ne zaman haberdar olsun": "Who gets notified and when",
    "Kaydetmeden önce kontrol et": "Check before saving",
    /* --- hazır senaryolar --- */
    "Hazır senaryolar": "Presets", "📅 Günlük — 14 gün": "📅 Daily — 14 days",
    "🗄️ Haftalık arşiv — 6 ay": "🗄️ Weekly archive — 6 months",
    "🔒 Kritik — 30 gün": "🔒 Critical — 30 days", "🧪 Test": "🧪 Test",
    "senaryo yüklendi — kaydetmeden uygulanmaz": "preset loaded — not applied until you save",
    /* --- form alanları --- */
    "Genel": "General", "Plan adı": "Plan name", "Etkin": "Enabled",
    "zamanlayıcı bu planı çalıştırsın": "let the scheduler run this plan",
    "Kaynak (Proxmox)": "Source (Proxmox)", "Yedek klasörü": "Backup folder",
    "Hedef (Google hesabı)": "Target (Google account)",
    "Saklama süreleri": "Retention periods", "Drive'da tut (gün)": "Keep on Drive (days)",
    "En az set (adet)": "Minimum sets (count)", "Çöpte bekle (gün)": "Wait in trash (days)",
    "Zamanlama": "Schedule", "Aktarım": "Transfer", "Bildirim": "Notifications",
    "Hız sınırı": "Speed limit", "Saatlik çizelge": "Hourly schedule",
    "Sadece yükleme": "Upload only", "Eşzamanlı transfer": "Concurrent transfers",
    "Checkers": "Checkers", "Drive parça boyutu": "Drive chunk size",
    "Ek rclone argümanı": "Extra rclone arguments", "Gönderen profili": "Sender profile",
    "Ne zaman mail": "When to email", "✔ Başarılı olunca": "✔ On success",
    "✖ Hata olunca": "✖ On failure", "⏸ Atlanınca": "⏸ On skip",
    "Rapor gönder": "Send report", "Dönem (gün)": "Period (days)",
    "Uyarı eşiği (gün)": "Warning threshold (days)", "Kota uyarısı (%)": "Quota warning (%)",
    "Rapor alıcısı": "Report recipient",
    "haftalık özet raporu gönder": "send a weekly summary report",
    /* --- çakışma koruması --- */
    "Proxmox ile çakışma koruması": "Proxmox conflict protection",
    "vzdump'ı bekle": "Wait for vzdump", "En fazla bekleme (dk)": "Max wait (min)",
    "Dosya yaşı (dk)": "File age (min)", "Atlanacak desenler": "Skip patterns",
    "Hatada retention": "Retention on failure",
    "Proxmox yedeği biterken bekle": "wait for the Proxmox backup to finish",
    "yükleme başarısız olsa da eskileri sil": "delete old ones even if the upload failed",
    /* --- otomatik bant genişliği --- */
    "Otomatik bant genişliği": "Automatic bandwidth", "Otomatik mod": "Automatic mode",
    "Hat kapasitesi": "Link capacity", "Diğerlerine ayrılan": "Reserved for others",
    "Alt sınır": "Lower bound", "Üst sınır": "Upper bound", "Ağ arayüzü": "Network interface",
    "Ölçüm aralığı (sn)": "Measure interval (s)", "Yumuşatma (0-1)": "Smoothing (0-1)",
    "Değişim eşiği (%)": "Change threshold (%)",
    "hattaki diğer trafiğe göre kendini ayarla": "adapt to other traffic on the link",
    "sınır yalnızca yüklemeye uygulansın": "apply the limit to uploads only",
    "Çizelgesiz": "No schedule", "Mesai dostu": "Office friendly", "Sadece gece": "Night only",
    /* --- hesaplar --- */
    "👤 Google hesapları": "👤 Google accounts", "👤 Yönet": "👤 Manage",
    "＋ Yeni hesap": "＋ New account", "Yeni Google hesabı yetkilendir": "Authorise a new Google account",
    "Hesap adı": "Account name", "Tarayıcıyla yetkilendir": "Authorise with browser",
    "Hazır jetonu yapıştır": "Paste an existing token", "Tünel komutu": "Tunnel command",
    "▶ Başlat": "▶ Start", "Jeton (JSON)": "Token (JSON)", "Henüz hesap yok.": "No accounts yet.",
    "jeton bekleniyor…": "waiting for token…", "iptal edildi": "cancelled",
    "kota okunamadı": "quota unreadable", "Yönetmek için tıkla": "Click to manage",
    /* --- mail profilleri --- */
    "✉ Mail gönderici profilleri": "✉ Mail sender profiles", "✉ Yönet": "✉ Manage",
    "Sağlayıcı şablonu": "Provider preset", "Profil adı": "Profile name",
    "Yeni profil": "New profile", "Test maili": "Test email",
    "Formu temizle": "Clear form", "Henüz profil yok.": "No profiles yet.",
    "Özel (elle gir)": "Custom (enter manually)",
    /* --- ayarlar --- */
    "Tüm planlar için ortak.": "Shared by all plans.", "Web arayüzü": "Web interface",
    "Dinlenen adres": "Listen address", "Yeni şifre": "New password",
    "Tazeleme (sn)": "Refresh (s)", "Gelişmiş": "Advanced", "Gezinme kökleri": "Browse roots",
    "Dosya adı kalıbı": "Filename pattern", "Geçmiş kaydı": "History entries",
    "Log satırı": "Log lines", "rclone tampon satırı": "rclone buffer lines",
    "Durum satır sınırı": "Status row limit", "Log boyutu (MB)": "Log size (MB)",
    "Saklanan log": "Logs kept", "rclone zaman aşımı (dk)": "rclone timeout (min)",
    "Hesap geneli cleanup": "Account-wide cleanup", "HTTPS": "HTTPS",
    "Sertifika": "Certificate", "Güvenli çerez": "Secure cookie",
    "Erişim kısıtlaması": "Access restriction", "İzinli ağlar": "Allowed networks",
    "Güncelleme": "Update", "Kontrol et": "Check", "Otomatik kur": "Auto install",
    "↻ Şimdi kontrol et": "↻ Check now", "⬇ Güncellemeyi kur": "⬇ Install update",
    "↶ Önceki sürüme dön": "↶ Roll back", "boş = değişmesin": "empty = unchanged",
    "günlük güncelleme kontrolü": "daily update check",
    "bulunca kendiliğinden kur": "install automatically when found",
    "çerezi sadece HTTPS'te gönder": "send the cookie over HTTPS only",
    "son çare olarak rclone cleanup çalıştır": "run rclone cleanup as a last resort",
    /* --- klasör gezgini --- */
    "📁 Yedek klasörünü seç": "📁 Choose the backup folder", "📁 Gözat": "📁 Browse",
    "Bu klasörü seç": "Use this folder", "⬆ üst klasör": "⬆ parent folder",
    "alt klasör yok": "no subfolders",
    /* --- kapasite --- */
    "📐 Önerilen süreyi uygula": "📐 Apply suggested period",
    "↻ Yeniden ölç": "↻ Measure again",
    "Kaynak ve hesap seçilince kapasite hesabı burada çıkar.": "The capacity estimate appears here once a source and account are chosen.",
    "ölçülüyor…": "measuring…", "ölçüm başarısız": "measurement failed",
    /* --- mesajlar --- */
    "çalışıyor…": "working…", "tamam": "done",
    "gönderiliyor…": "sending…", "kontrol ediliyor…": "checking…",
    "indiriliyor ve doğrulanıyor…": "downloading and verifying…",
    "form hatalı — kırmızı alanlara bak": "form has errors — check the red fields",
    "form hatalı": "form has errors",
    "bu adımda eksik veya hatalı alan var": "this step has missing or invalid fields",
    "önce hesap adı yaz": "enter an account name first",
    "adresi tarayıcında aç": "open the address in your browser",
    "oturum bitti": "session expired", "sunucu hatası": "server error",
    "yeni profil için şifre gerekli": "a password is required for a new profile",
    /* --- doğrulama --- */
    "plan adı gerekli": "plan name is required",
    "kaynak klasör gerekli": "source folder is required",
    "hedef klasör gerekli": "target folder is required",
    "önce bir Google hesabı ekle": "add a Google account first",
    "alıcı adresi gerekli": "recipient address is required",
    "kullanıcı adı gerekli": "username is required",
    "kalıp boş olamaz": "the pattern cannot be empty",
    "profil adı gerekli": "profile name is required",
    "geçerli bir sunucu adı yaz": "enter a valid server name",
    "geçerli JSON değil": "not valid JSON",
    "sadece harf, rakam, - ve _": "letters, digits, - and _ only",
    "ikisi birden 0 olamaz — hiç yedek kalmaz": "both cannot be 0 — you would keep no backups",
    "alt sınır üst sınırdan büyük olamaz": "the lower bound cannot exceed the upper bound",
    "geçersiz sayı": "invalid number", "bu alan gerekli": "this field is required",
    "geçersiz adres: ": "invalid address: ", "geçersiz ağ: ": "invalid network: ",
    /* --- onay soruları --- */
    "Plan silinsin mi? Drive'daki yedek dosyalarına dokunulmaz.": "Delete this plan? The backup files on Drive are not touched.",
    "Profil silinsin mi?": "Delete this profile?",
    "Kaydedilmemiş değişiklikler var, kapatılsın mı?": "There are unsaved changes. Close anyway?",
    /* --- ikinci parti --- */
    "Çar": "Wed", "Plan: ": "Plan: ", "hata: ": "error: ", "uygun.": "is a good fit.",
    "· Depo": "· Storage", " · çöp ": " · trash ", ". Örnek:": ". Example:",
    "çalışmaz": "does not run", "  ·  boş ": "  ·  free ", "  ·  çöp ": "  ·  trash ",
    " boş var.": " free.", " · güncel": " · up to date", "Düzenle: ": "Edit: ",
    "Her giriş": "Each entry", "güncel: v": "up to date: v", "çizelge: ": "schedule: ",
    " · bitiş: ": " · expires: ", "= sınırsız": "= unlimited",
    "giriş yap:": "sign in:", "· 1 Gbit ≈": "· 1 Gbit ≈", "ölçülemedi": "not measured",
    "seti korur.": "sets.", "yükleniyor…": "loading…", "(VPN ağın) ·": "(your VPN) ·",
    "Kurulu sürüm": "Installed version", "her yerden ·": "from anywhere ·",
    "ve uyarılar.": "and warnings.", "Birden fazla:": "Multiple:",
    "Kapalı bırak.": "Leave it off.", "başlatılamadı": "could not start",
    "Önerilen: <b>": "Suggested: <b>", " gün uygulandı": " days applied",
    " günlük dönem)": " day period)", " hesap tanımlı": " accounts configured",
    "API hızını kıs": "throttle API rate", "Günlük VM + CT": "Daily VM + CT",
    "Günlük yedekte": "For a daily backup", "Haftalık arşiv": "Weekly archive",
    "Yok (şifresiz)": "None (unencrypted)", "geçersiz değer": "invalid value",
    "iyi bir değer.": "is a good value.", "vzdump koruması": "vzdump protection",
    "Bağlantı şifreli": "Connection encrypted", "Günlük VM yedeği": "Daily VM backup",
    "bekle, en fazla ": "wait, at most ", "beklemeden çalış": "run without waiting",
    "yavaş hat için ·": "for a slow link ·", "ör. 30M veya boş": "e.g. 30M or empty",
    "yeni sürüm var: v": "new version available: v",
    "✉ Mail profilleri": "✉ Mail profiles", "Kurulu sürüm: <b>v": "Installed version: <b>v",
    "Proxmox depoları: ": "Proxmox storages: ", "o saatten itibaren": "from that hour on",
    "uzantısı kullanır.": "extension.", "Ölçüldü: günde <b>": "Measured: <b>",
    "çalışmaz (güvenli)": "does not run (safe)", "(boş = sabit sınır)": "(empty = fixed limit)",
    "Bit değil bayt yaz.": "Enter bytes, not bits.",
    "Drive'daki tüm çöpü": "all trash in the Drive", "SMTP sunucu adresi.": "SMTP server address.",
    "Sadece harf, rakam,": "Only letters, digits,", "yol argümanı almaz,": "takes no path argument,",
    "⚠ klasör bulunamadı": "⚠ folder not found", " gün sonra ulaşılır.": " days.",
    " günlük set, toplam ": " daily sets, total ", "SS:DD biçiminde saat": "time as HH:MM",
    "ör. 30M, 2M veya off": "e.g. 30M, 2M or off",
    "Virgülle ayır. Örnek:": "Comma separated. Example:",
    " gün</b> (boş alanın %": " days</b> (uses %",
    " gün</b> saklama + <b>": " days</b> retention + <b>",
    "RAM ≈ parça × transfer": "RAM ≈ chunk × transfers",
    "hedef Google hesabıyla": "with the target Google account",
    "senin bilgisayarındaki": "on your own computer",
    "uygun. Haftalık planda": "is right. For a weekly plan use",
    "yetkilendirme sonlandı": "authorisation ended",
    "Ölçüm ve ayar sıklığı.": "How often to measure and adjust.",
    "Outlook / Microsoft 365": "Outlook / Microsoft 365",
    "(boş = yukarıdaki alıcı)": "(empty = recipient above)",
    ", uygulama şifresi üret.": ", create an app password.",
    " gün sonra kalıcı silinir": " days it is permanently deleted",
    "Mail gönderici profilleri": "Mail sender profiles",
    "Yükleme hızın. 100 Mbit ≈": "Your upload speed. 100 Mbit ≈",
    "düz HTTP ile ayakta kalır": "stays up over plain HTTP",
    "log dosyalarını yükleme ·": "skip log files ·",
    "yap, yoksa boşuna uyarır.": "instead, or it warns needlessly.",
    "= hiç bekleme, hemen atla.": "= do not wait, skip immediately.",
    "Raporun gönderileceği gün.": "Day the report is sent.",
    "Sadece veritabanı sunucusu": "Database server only",
    "kapalı (zamanlayıcı atlar)": "disabled (scheduler skips it)",
    "yalnızca program dosyasını": "only the program file",
    " gün</b> çöp → Drive'da <b>": " days</b> trash → <b>",
    "Bu adresi tarayıcında aç ve": "Open this address in your browser and",
    "Raporun gönderileceği saat.": "Time the report is sent.",
    "Sertifikanın özel anahtarı.": "The certificate's private key.",
    " gün · VM/CT başına en az ": " days · at least ",
    "Proxmox'un kendi sertifikası:": "Proxmox's own certificate:",
    "Sertifika yüklenemezse servis": "If the certificate cannot be loaded the service",
    "(düzenlemede boş = değişmesin)": "(empty when editing = unchanged)",
    "Boşlukla ayır. vzdump yazarken": "Space separated. While writing, vzdump uses the",
    "Tahmini rclone RAM kullanımı: ": "Estimated rclone RAM use: ",
    "önce geçerli bir hesap adı yaz": "enter a valid account name first",
    "Google onaydan sonra tarayıcıyı": "After approval Google redirects the browser to",
    " set var); hedef doluluğa ancak ": " sets in the source); the target level is reached in ",
    "Her plan farklı klasöre yazmalı.": "Each plan must write to a different folder.",
    "Proxmox işin 21:00'de başlıyorsa": "If your Proxmox job starts at 21:00 then",
    "SS:DD biçiminde saat (ör. 03:00)": "time as HH:MM (e.g. 03:00)",
    "Yeni sürümün indirileceği adres.": "Where the new version is downloaded from.",
    "VM/CT bazında son yedek tarihi": "last backup date per guest",
    "⚠ Bu süre hesaba <b>sığmaz</b>: ": "⚠ This period does <b>not fit</b>: ",
    "Mesaide hattı boğma, gece hızlan:": "Go easy during work hours, fast at night:",
    "Plan hedefinde görünecek kısa ad.": "Short name shown in the plan target.",
    "Rapor kaç günlük dönemi kapsasın.": "How many days the report covers.",
    "= çöpe uğramadan hemen kalıcı sil.": "= delete permanently without using the trash.",
    "jeton alındı, hesap oluşturuluyor…": "token received, creating account…",
    'klasör adında : * ? " < > | olamaz': 'folder name cannot contain : * ? " < > |',
    "Henüz hesap yok — 'Yönet' ile ekle.": "No accounts yet — add one with 'Manage'.",
    "Proxmox yedeğin ~1.5 saat sürüyorsa": "If your Proxmox backup takes ~1.5 hours then",
    "Saklanacak eski log dosyası sayısı.": "How many rotated log files to keep.",
    "sadece yerel (nginx/SSH tüneli ile)": "local only (via nginx/SSH tunnel)",
    "Yedeğin hiç ilerlememesini engeller.": "Keeps the backup from stalling completely.",
    " dolar. VM/CT'ler büyürse yer biter.": " full. If guests grow you will run out.",
    "'ini kullanır, büyümeye pay bırakır).": "of free space, leaving room to grow).",
    "Bildirim seçili ama alıcı adresi boş.": "Notifications are on but no recipient is set.",
    "Boş bırakırsan mevcut şifre değişmez.": "Leave empty to keep the current password.",
    "IP adresi yaz (0.0.0.0 veya 127.0.0.1)": "enter an IP address (0.0.0.0 or 127.0.0.1)",
    "ÇALIŞIR — yükleme başarısızsa da siler": "RUNS — deletes even if the upload failed",
    "Google hesabı seçilmedi — 3. adıma dön.": "No Google account selected — go back to step 3.",
    "Arayüzde gösterilecek log satırı sayısı.": "How many log lines the UI shows.",
    "Boş bırakırsan yukarıdaki alıcıya gider.": "Leave empty to use the recipient above.",
    "Tarayıcısı olan herhangi bir bilgisayarda": "On any computer that has a browser run",
    "Tıklayınca seçilir, kopyala-yapıştır yap.": "Click to select, then copy and paste.",
    "Önceki sürüme dönülecek. Devam edilsin mi?": "This rolls back to the previous version. Continue?",
    "Günde bir kez yeni sürüm var mı diye bakar.": "Checks once a day for a new version.",
    "Kota bu yüzdeyi aşarsa raporda uyarı çıkar.": "Warn in the report when quota exceeds this percentage.",
    "Hangi sonuçta mail gelsin. Bağımsız seçilir.": "Which outcomes trigger an email. Chosen independently.",
    "Trafik şifresiz — yalnızca VPN içinde kullan": "Traffic is unencrypted — use only inside a VPN",
    "dengeli. Hızın sürekli inip çıkıyorsa düşür.": "is balanced. Lower it if the speed keeps oscillating.",
    "Plan başına saklanacak çalışma geçmişi kaydı.": "Run history entries kept per plan.",
    "Boş = her gün. Haftalık arşiv için tek gün seç.": "Empty = every day. Pick one day for a weekly archive.",
    "Gereksiz ayar yapılmasını ve salınımı engeller.": "Prevents needless adjustments and oscillation.",
    "Hat çok meşgulken bile bu hızın altına inilmez.": "Never drops below this even when the link is busy.",
    "SMTP kullanıcı adı, genelde tam e-posta adresi.": "SMTP username, usually the full email address.",
    "Düzenlerken boş bırakırsan mevcut şifre korunur.": "Leave empty when editing to keep the current password.",
    "Profilin görünen adı. Plan formunda bu ad çıkar.": "Display name of the profile, shown in the plan form.",
    "Adres veya port değişirse servisi yeniden başlat:": "If the address or port changes, restart the service:",
    "' kaldırılsın mı? Drive'daki dosyalara dokunulmaz.": "' be removed? Files on Drive are not touched.",
    "Dosya adı deseni. Grup 1=set, 2=tip, 3=id, 4=tarih.": "Filename pattern. Group 1=set, 2=type, 3=id, 4=date.",
    "Klasör seçici yalnızca bu köklerin altını gösterir.": "The folder picker only shows paths under these roots.",
    "geçerli olur. Boş bırakırsan sabit sınır kullanılır.": "applies. Leave empty to use the fixed limit.",
    "Google hesaplarını yönet: kota, bağlantı testi, silme": "Manage Google accounts: quota, connection test, removal",
    "Sunucu, port ve güvenlik ayarını sağlayıcından öğren.": "Get server, port and security settings from your provider.",
    "Arayüz birkaç saniye yeniden başlar. Devam edilsin mi?": "The UI restarts for a few seconds. Continue?",
    "Şifreleme yöntemi. Sağlayıcı şablonu bunu da doldurur.": "Encryption method. The provider preset fills this in too.",
    "Hesaplar, mail profilleri, güvenlik ve gelişmiş ayarlar": "Accounts, mail profiles, security and advanced settings",
    "Firewall kurmaya gerek yok — kontrol uygulamanın içinde.": "No firewall needed — the check is inside the application.",
    "Haftada bir, son dönemin özetini ve uyarıları mail atar.": "Emails a weekly summary of the period plus warnings.",
    "Bu kadar gündür başarılı yedek yoksa raporda uyarı çıkar.": "Warn in the report after this many days without a successful backup.",
    "Hattın bu yüzdesi her zaman diğer uygulamalara bırakılır.": "This percentage of the link is always left to other applications.",
    "Kapsamlı silme yetmezse hesap geneli cleanup çalışsın mı.": "Run account-wide cleanup if scoped deletion is not enough.",
    "Mail profili yok — '✉ Yönet' ile ekle, yoksa mail gitmez.": "No mail profile — add one with '✉ Manage' or no mail is sent.",
    "Sertifika dosyası. Boş bırakırsan arayüz düz HTTP çalışır.": "Certificate file. Leave empty to run the UI over plain HTTP.",
    "güvenli. Çakışırsa zaten beklenir ama boşuna beklemeyelim.": "is safe. Overlap is handled anyway, but let us not wait for nothing.",
    "Her gün, 2 gün sakla, çöpte yarım gün. Kurulumu denemek için.": "Every day, keep 2 days, half a day in trash. For trying the setup.",
    "Yanlış silme olursa bu süre içinde Drive'dan geri alabilirsin.": "If something is deleted by mistake you can restore it from Drive within this window.",
    "Açıkken sınır yalnızca yüklemeye uygulanır, indirme etkilenmez.": "When on, the limit applies to uploads only; downloads are unaffected.",
    "Hatada retention açık: yeni yedek çıkmadan eskiler silinebilir.": "Retention on failure is on: old backups may be deleted before a new one lands.",
    "Hesabın içindeki hedef klasör. Yoksa ilk çalışmada oluşturulur.": "Target folder inside the account. Created on the first run if missing.",
    "Sabit yükleme hız sınırı. Çizelge doluysa çizelge önceliklidir.": "Fixed upload speed limit. The schedule takes precedence if set.",
    "Ham rclone argümanları. Buradaki her şey komut satırına eklenir.": "Raw rclone arguments. Everything here is appended to the command line.",
    "Rapor içinde: çalışma sayıları, yüklenen/silinen dosyalar, kota,": "The report contains: run counts, uploaded/deleted files, quota,",
    "Bildirimlerin gideceği adres. Virgülle birden fazla yazabilirsin.": "Where notifications go. Comma separate for multiple addresses.",
    "Hazır sağlayıcı seçersen sunucu, port ve güvenlik otomatik dolar.": "Picking a provider preset fills in server, port and security.",
    "Hiçbiri seçili değilse her gün çalışır. Seçersen sadece o günler.": "If none are selected it runs every day; otherwise only on those days.",
    "Mailde görünecek gönderen adresi. Boşsa kullanıcı adı kullanılır.": "Sender address shown in the mail. Falls back to the username.",
    "state.json'a yazılacak azami yedek/çöp satırı. Toplamlar tam kalır.": "Maximum backup/trash rows written to state.json. Totals stay exact.",
    "Aynı klasörü paylaşan iki plan birbirinin yedeğini eski sanıp siler.": "Two plans sharing a folder will treat each other's backups as old and delete them.",
    "Hesaplanan yeni sınır mevcuttan bu yüzdeden az farklıysa uygulanmaz.": "A new limit is not applied if it differs from the current one by less than this.",
    "Yeni sürüm bulununca kendiliğinden kurar. Kapalıyken sadece bildirir.": "Installs automatically when a new version is found; otherwise it only notifies.",
    "Bir VM 3 aydır yedeklenmiyorsa gün kuralı hepsini silerdi; bu ayar son": "If a VM has not been backed up for 3 months the day rule would delete everything; this keeps the last",
    "Bu araç zaten yükleme yapar; kapatırsan listeleme/indirme de yavaşlar.": "This tool only uploads; turning it off also slows listing and downloads.",
    "Aynı anda kaç dosya yüklensin. Bellek kullanımı: parça boyutu × bu sayı.": "How many files upload at once. Memory use: chunk size × this number.",
    "Her gün 02:00, Drive'da 30 gün, VM/CT başına en az 7 set, çöpte 3 gün.": "Every day at 02:00, 30 days on Drive, at least 7 sets per guest, 3 days in trash.",
    "Kapalıyken yükleme başarısızsa hiçbir yedek silinmez. Açmanız önerilmez.": "When off, nothing is deleted if the upload fails. Turning it on is not recommended.",
    "Regex. Varsayılan vzdump adlarını tanır — değiştirmen normalde gerekmez.": "Regex. Recognises default vzdump names — you normally do not need to change it.",
    "Bu desenlere uyan dosyalar hiç yüklenmez. vzdump geçici dosyaları burada.": "Files matching these patterns are never uploaded. vzdump temporary files go here.",
    "Test maili hangi adrese gitsin?\\n(boş bırakırsan gönderen adresine gider)": "Where should the test mail go?\\n(empty = the sender address)",
    "Arayüze yalnızca bu ağlardan erişilebilir. Boş bırakırsan kısıtlama olmaz.": "Only these networks may reach the UI. Leave empty for no restriction.",
    "Listede ve mail konularında görünecek ad. Plan kimliği bu addan türetilir.": "Name shown in the list and mail subjects. The plan id is derived from it.",
    "İnternet bağlantının toplam YÜKLEME kapasitesi. Hesaplamanın temeli budur.": "Your connection's total UPLOAD capacity. This is the basis of the calculation.",
    "iyi bir değer. Çok düşürürsen yazılmakta olan dosyayı yakalama riski artar.": "is a good value. Lowering it raises the risk of catching a file still being written.",
    "Her plan bu hesaplardan birine yazar. Başkasının hesabını da ekleyebilirsin.": "Each plan writes to one of these accounts. You can add someone else's account too.",
    "Her Pazar 05:00, Drive'da 180 gün, çöpte 7 gün, düşük hız. Uzun süreli arşiv.": "Every Sunday at 05:00, 180 days on Drive, 7 days in trash, low speed. Long-term archive.",
    "Saate göre değişen hız sınırı. Doluysa yukarıdaki sabit sınırın yerine geçer.": "Speed limit that varies by hour. Overrides the fixed limit above when set.",
    "%30 iyi bir başlangıç. Yükseltirsen daha nazik, düşürürsen daha hızlı olursun.": "30% is a good start. Raise it to be gentler, lower it to go faster.",
    "Güvenlik tabanı: VM/CT başına bu kadar set, gün sınırına bakılmadan korunur.": "Safety floor: this many sets per guest are kept regardless of the day limit.",
    "Her gün 03:00, Drive'da 14 gün, çöpte 1 gün. Çoğu kurulum için doğru başlangıç.": "Every day at 03:00, 14 days on Drive, 1 day in trash. The right start for most setups.",
    "Proxmox host üzerindeki klasörler. Parantez içi: tanınan vzdump dosyası sayısı.": "Folders on the Proxmox host. In brackets: number of recognised vzdump files.",
    "Gün cinsinden tüm değerler burada. Alan adlarının üstüne gelince açıklama çıkar.": "All day-based values are here. Hover a field label for an explanation.",
    "Kapalıysa zamanlayıcı bu planı atlar. Elle 'Yedekle' ile yine çalıştırabilirsin.": "When off the scheduler skips this plan. You can still run it manually with 'Back up'.",
    "Mailin hangi hesaptan gönderileceği. Mail düğmesinden yeni profil ekleyebilirsin.": "Which account sends the mail. Add a new profile from the Mail button.",
    "Çerezin yalnızca HTTPS üzerinden gönderilmesi. TLS açıkken zaten zorunlu tutulur.": "Send the cookie over HTTPS only. Already enforced when TLS is on.",
    "Hat boşken bile bu hızın üstüne çıkılmaz. Boşsa yukarıdaki sabit sınır tavan olur.": "Never exceeds this even when the link is idle. Empty means the fixed limit is the ceiling.",
    "Her plan istediği profilden mail atar. Farklı hesaplar, farklı sunucular olabilir.": "Each plan sends mail from the profile it chooses. Different accounts and servers are fine.",
    "Güvenlik tabanı 0: uzun süre yedeklenmeyen bir VM/CT'nin tüm yedekleri silinebilir.": "Safety floor is 0: all backups of a guest not backed up for a long time may be deleted.",
    "Google çöp kutusunda bekleme süresi. Bu süre dolunca kalıcı silinir ve kota boşalır.": "How long it waits in Google trash. After that it is permanently deleted and quota is freed.",
    "Proxmox'un vzdump çıktılarını yazdığı klasör. Depo başına ayrı bir dump klasörü olur.": "The folder where Proxmox writes vzdump output. Each storage has its own dump folder.",
    "Bu süre dolarsa tur atlanır ve sonraki kontrolde yeniden denenir. Hiçbir şey silinmez.": "If this expires the round is skipped and retried at the next check. Nothing is deleted.",
    "Yedeğin yükleneceği Google hesabı. Başkasının hesabını da ekleyip burada seçebilirsin.": "The Google account backups are uploaded to. You can add and pick someone else's account.",
    "Ölçümü yumuşatır. Düşük değer daha sakin, yüksek değer daha çevik ama salınıma yatkın.": "Smooths the measurement. Lower is calmer, higher is more responsive but prone to oscillation.",
    "Sadece bu kadar dakikadır değişmemiş dosyalar yüklenir. Yazılmakta olan yedek yarım gitmez.": "Only files untouched for this many minutes are uploaded, so a backup being written is not sent half finished.",
    "Karşılaştırma işçisi sayısı. Yüklemeyi değil, 'bu dosya zaten var mı' kontrolünü hızlandırır.": "Number of comparison workers. Speeds up the 'does this file already exist' check, not the upload.",
    "Planın başlayacağı saat (24 saat biçimi). Proxmox'un kendi yedek işi bittikten sonrasını seç.": "When the plan starts (24-hour clock). Pick a time after your Proxmox backup job finishes.",
    "Arayüzün dinleyeceği adres. 127.0.0.1 yaparsan sadece SSH tüneli/ters vekil üzerinden erişilir.": "Address the UI listens on. Use 127.0.0.1 to allow access only via SSH tunnel or reverse proxy.",
    "Senaryo seçmek formu doldurur; sonra istediğini değiştirebilirsin. Kaydetmeden hiçbir şey uygulanmaz.": "Choosing a preset fills the form; you can change anything afterwards. Nothing is applied until you save.",
    "Proxmox'un kendi yedeği çalışırken yüklemeye başlanmaz. Kilit dosyası, süreç ve yazılan dosyalar kontrol edilir.": "Uploading does not start while Proxmox's own backup runs. The lock file, process and files being written are all checked.",
    "Trafiğin ölçüleceği ağ arayüzü. Proxmox'ta köprü yerine fiziksel/bond arayüzü seçmek VM ve CT trafiğini de kapsar.": "Interface to measure traffic on. On Proxmox, picking the physical/bond interface instead of the bridge also covers VM and CT traffic.",
    "Hattaki diğer trafiği ölçüp yükleme hızını canlı ayarlar. Başka bir yedekleme yazılımı hattı kullandığında geri çekilir.": "Measures other traffic on the link and adjusts the upload speed live. Backs off when another backup tool uses the line.",
    "Kota ölçülüyor, birkaç saniye sonra tekrar bak.": "Measuring quota, check again in a few seconds.",
    "Kota okunamadı — doluluk hesaplanamıyor. Gereken alan yine de doğru.": "Quota unavailable — usage cannot be projected. The required space is still correct.",
    "Sığabilecek en uzun süre: <b>": "Longest period that fits: <b>",
    "'ini kullanır). Bu bir tavsiye değil, üst sınır.": "of free space). This is an upper bound, not a recommendation.",
    "Kısa süre daha az yer kaplar; uzun süre geç fark edilen bir soruna karşı daha geniş geri dönüş penceresi verir.": "A shorter period uses less space; a longer one gives a wider recovery window for problems noticed late.",
    "📐 Sığabilecek en uzun süreyi uygula": "📐 Apply longest period that fits",
    "🗜️ Az yer — 3 gün": "🗜️ Low space — 3 days",
    "Her gün 03:00, Drive'da 3 gün, çöpte 1 gün. En az yer kaplayan seçenek; son birkaç günü korur.": "Every day at 03:00, 3 days on Drive, 1 day in trash. The smallest option; keeps the last few days.",
    "Her gün 03:00, Drive'da 14 gün, çöpte 1 gün. Daha geniş geri dönüş penceresi, daha çok yer.": "Every day at 03:00, 14 days on Drive, 1 day in trash. Wider recovery window, more space.",
    "Gün sayısı arttıkça yer de artar.": "More days means more space.",
    "Onay": "Confirm", "Bilgi gerekli": "Input required", "Tamam": "OK",
    "Gün kuralı kapalı — yalnızca aşağıdaki set tabanı korur.": "Day rule disabled — only the set floor below protects backups.",
    "günden eski setler Google çöp kutusuna gönderilir.": "days and older are moved to Google trash.",
    "Günlük yedek alıyorsan Drive'da yaklaşık": "With daily backups Drive holds about",
    "set durur.": "sets.", "Ölçülene göre": "Measured, that is",
    "0 riskli: gün kuralı bir VM/CT'nin tüm yedeklerini silebilir.": "0 is risky: the day rule could delete every backup of a VM/CT.",
    "Bir VM/CT uzun süre yedeklenmese bile en yeni": "Even if a VM/CT is not backed up for a long time, the newest",
    "seti gün kuralından muaf tutulur.": "sets are exempt from the day rule.",
    "Taban gün sayısından büyük ya da eşit: pratikte saklamayı taban belirler.": "The floor is at or above the day count, so in practice the floor decides retention.",
    "0 = çöpe uğramadan hemen kalıcı silinir; yanlış silmede geri dönüş olmaz.": "0 = deleted permanently without using the trash; no way back if it was a mistake.",
    "Yanlış silme olursa": "If something is deleted by mistake you have",
    "içinde Drive'ın çöp kutusundan geri alabilirsin.": "to restore it from Drive's trash.",
    "Çöpte yaklaşık": "About", "bekler.": "waits in trash.",
    "Bu süreden eski setler Google çöp kutusuna gönderilir.": "Sets older than this go to Google trash.",
    "Gün kuralından muaf güvenlik tabanı.": "Safety floor, exempt from the day rule.",
    "Çöpte bekleme süresi.": "How long it waits in trash.",
    " gün · min ": " days · min ",
    "Yedeklemeyi başlat": "Start backup",
    "bu plan zaten çalışıyor": "this plan is already running",
    "Planı duraklat": "Pause plan",
    "Planı etkinleştir": "Enable plan",
    "zamanlama durur, dosyalara dokunulmaz": "scheduling stops; no file is touched",
    "plan duraklatıldı": "plan paused",
    "plan etkinleştirildi": "plan enabled",
    "Retention'ı şimdi çalıştır": "Run retention now",
    "süresi dolan setleri Drive çöpüne taşır": "moves expired sets to Drive trash",
    "Çöpü boşalt": "Empty trash",
    "çöpte süresi dolmuş dosyaları kalıcı siler": "permanently deletes expired files in trash",
    "Drive durumunu tazele": "Refresh Drive status",
    "Haftalık raporu şimdi gönder": "Send weekly report now",
    "bu planda rapor kapalı": "weekly report is off for this plan",
    "önce plana mail adresi gir": "add a mail address to the plan first",
    "Test maili gönder": "Send test mail",
    "Kopyasını oluştur": "Duplicate",
    " (kopya)": " (copy)",
    "kopya hazır — gözden geçirip kaydet": "copy ready — review and save",
    "Planı JSON olarak indir": "Download plan as JSON",
    "başka sunucuya taşımak için": "to move it to another server",
    "Kaynak klasörü kopyala": "Copy source folder",
    "Hedefi kopyala": "Copy target",
    "kaynak": "source",
    "hedef": "target",
    "Bu planın loglarını göster": "Show this plan's logs",
    "Planı sil": "Delete plan",
    "Bağlantıyı test et": "Test connection",
    "Kotayı yenile": "Refresh quota",
    "Hesap adını kopyala": "Copy account name",
    "hesap adı": "account name",
    "Kota bilgisini kopyala": "Copy quota info",
    "Hesabı kaldır": "Remove account",
    "Drive'daki dosyalara dokunulmaz": "files on Drive are not touched",
    "Sunucu adresini kopyala": "Copy server address",
    "sunucu": "server",
    "Profili sil": "Delete profile",
    "Log — ": "Log — ",
    "tümü": "all",
    "sistem": "system",
    "Bu satırı kopyala": "Copy this line",
    "satır": "line",
    "Görünen logu kopyala": "Copy visible log",
    "Log dosyası olarak indir": "Download as log file",
    "Yenile": "Refresh",
    "Sistem loglarına geç": "Switch to system logs",
    "Tüm logları göster": "Show all logs",
    "Dosya adını kopyala": "Copy file name",
    "dosya adı": "file name",
    "Satırı kopyala": "Copy row",
    "Tabloyu kopyala": "Copy table",
    "tablo": "table",
    "Yeni plan": "New plan",
    "Şimdi yenile": "Refresh now",
    "Google hesapları": "Google accounts",
    "SMTP profilleri": "SMTP profiles",
    "Ayarlar": "Settings",
    "panoya kopyalandı": "copied to clipboard",
    "kopyalanamadı — metni elle seçmen gerekiyor": "could not copy — select the text manually",
    "Yarım kalmış bir düzenleme var — ": "You have an unfinished edit — ",
    "Kaydedilmemişti. Geri yüklensin mi?": "It was not saved. Restore it?",
    "Yarım kalan düzenleme": "Unfinished edit",
    "Geri yükle": "Restore",
    "az önce": "just now",
    " dakika önce": " minutes ago",
    "yeni plan": "new plan",
    "taslak geri yüklendi — kaydetmedikçe uygulanmaz": "draft restored — nothing is applied until you save",
    "İptal": "Cancel",
    "Henüz plan yok. Sağ üstten + Yeni Plan ile başla.": "No plans yet. Start with + New Plan at the top right.",
    " gönderilmiş": " sent",
    " (varsayılan rota)": " (default route)",
    "Proxmox'ta köprü (vmbr0) yalnızca host trafiğini görebilir; ": "On Proxmox a bridge (vmbr0) only sees host traffic; ",
    "VM ve CT trafiğini de saymak için fiziksel veya bond arayüzünü seç.": "pick the physical or bond interface to also count VM and CT traffic.",
    "Yeni sürüm var: ": "New version available: ",
    " hazır": " ready",
    "%) · çöp: ": "%) · trash: ",
    " · boş: ": " · free: ",
    "● CANLI": "● LIVE",
    "○ yoklama": "○ polling",
    "Sunucu değişiklikleri anında gönderiyor": "The server pushes changes instantly",
    "Canlı akış yok — belirli aralıklarla yenileniyor": "No live stream — refreshing on a timer",
    "Zamanlama kapalı; dosyalara dokunulmaz.": "Scheduling is off; no file is touched.",
    "Bu plan henüz hiç çalışmadı. İlk çalışma: ": "This plan has never run yet. First run: ",
    "Son denemede iş yapılmadı — genelde vzdump hâlâ çalışıyordu. ": "The last attempt did no work — usually vzdump was still running. ",
    "Sebep kartın altındaki özet satırında yazar. Sonraki turda tekrar denenir.": "The reason is in the summary line under the card. It retries on the next tick.",
    "Canlı akış": "Live stream",
    "Canlı akış (SSE)": "Live stream (SSE)",
    "Diski tarama sıklığı (ms)": "Disk watch interval (ms)",
    "Eş zamanlı canlı bağlantı sınırı": "Concurrent live connection limit",
    "Oturum adres bağlama": "Session address binding",
    "Host yapılandırması": "Host configuration",
    "yedekleniyor · son ": "backed up · keeping last ",
    " arşiv saklanır": " archives",
    " · JSON görüntü dahil": " · JSON snapshot included",
    "yedeklenmiyor": "not backed up",
    "Yapılandırmayı da yedekle": "Back up host configuration",
    "pvesh JSON görüntüsü": "pvesh JSON snapshot",
    "Saklanacak arşiv (adet)": "Archives to keep",
    "Yapılandırma arşivini indir": "Download configuration archive",
    "Yapılandırmayı şimdi yedekle": "Back up configuration now",
    "Zamanlayıcı gecikmiş": "Scheduler is late",
    "Zamanlayıcı hiç çalışmadı": "Scheduler has never run",
    "Kontrol et: ": "Check: ",
    "(otomatik: ": "(automatic: ",
    "  · köprü": "  · bridge",
    "  ← önerilen": "  ← recommended",
    "Otomatik seçim: ": "Automatic choice: ",
    "elle secildi": "selected manually",
    "varsayilan rota": "default route",
    "Köprü (vmbr0) sayaçları VM'ler arası yerel trafiği de sayar — o trafik internete hiç çıkmaz, yükleme hızınla yarışmaz. Köprünün altındaki bond/fiziksel arayüz doğru ölçümü verir.": "A bridge (vmbr0) also counts VM-to-VM local traffic, which never reaches the internet and does not compete with your upload. The bond or physical interface under the bridge gives the correct measurement.",
    "Zamanlayici henuz hic calismadi.": "The scheduler has never run yet.",
    "Bağlandığın sunucu": "Server you are connecting to",
    "Oturum sahibi": "Signed in as",
    "Oturumu kapat": "Sign out",
    "⎋ Çıkış": "⎋ Sign out",
    "Kaydedilmemiş değişiklikler var. Yine de çıkılsın mı?": "You have unsaved changes. Sign out anyway?",
    "Çıkış": "Sign out",
    "Çık": "Sign out",
    "Yedek hedef yok — sadece birincil kullanılır.": "No fallback targets — only the primary is used.",
    "klasör": "folder",
    "Yukarı taşı": "Move up",
    "Bu hedefi kaldır": "Remove this target",
    "⚠ ": "⚠ ",
    " hedef birincil ile aynı hesapta — hesap kilitlenirse işe yaramaz": " target(s) are on the same account as the primary — useless if that account is locked",
    " yedek hedef tanımlı": " fallback target(s) configured",
    "Yedek hedefler": "Fallback targets",
    "yok — sadece birincil hedef": "none — primary target only",
    "Birincil çalışmazsa sırayla denenir: ": "Tried in order if the primary fails: ",
    " yedek": " fallback",
    "Son yazılan": "Last written to",
    "  (denenmedi)": "  (untested)",
    "Gerçek hesapla uçtan uca doğrulandı.": "Verified end to end with a real account.",
    "OAuth akışı çalışıyor ama yükleme/saklama davranışı gerçek hesapla denenmedi. Önce küçük bir planla dene.": "The OAuth flow works, but upload/retention behaviour has not been tested with a real account. Try a small plan first.",
    "Kapsam drive.file: yalnizca bu aracin olusturdugu dosyalari gorur, Drive'inin gerisine erisemez.": "Scope drive.file: it only sees files this tool created and cannot reach the rest of your Drive.",
    "Bazi kurumsal hesaplarda drive_id/drive_type de gerekir; gerekirse hesabi rclone config ile elle kur.": "Some business accounts also need drive_id/drive_type; if so, configure the account manually with rclone config.",
    "Oturumu kapatmak istediğine emin misin?\n\"Beni hatırla\" işaretlemiş olsan bile hatırlanan oturum silinir.": "Are you sure you want to sign out?\nEven if you ticked \"Remember me\", the remembered session is deleted.",
    "Bakım ve taşıma": "Maintenance and migration",
    "Ayarları taşı": "Migrate settings",
    "⬇ Ayarları indir": "⬇ Download settings",
    "⬇ Şifrelerle indir": "⬇ Download with passwords",
    "⬆ Ayar dosyası yükle": "⬆ Upload settings file",
    "Proxmox linki": "Proxmox link",
    "🔗 Proxmox'a link ekle": "🔗 Add link to Proxmox",
    "Kaldır": "Remove",
    "Açık oturumlar": "Open sessions",
    "Cihaz": "Device",
    "Adres": "Address",
    "Açılış": "Opened",
    "bu tarayıcı": "this browser",
    "Hatırlanan açık oturum yok.": "No remembered sessions.",
    "Hatırlama: ": "Remember me: ",
    "açık": "on",
    "adres bağlama: ": "address binding: ",
    "Diğer tüm oturumları kapat": "Close all other sessions",
    "Bu tarayıcı dışındaki tüm hatırlanan oturumlar kapatılsın mı?": "Close every remembered session except this browser?",
    "Oturumlar": "Sessions",
    "İndirilecek dosya SMTP şifrelerini düz metin içerecek.\nYalnızca güvendiğin bir yere kaydet.": "The downloaded file will contain SMTP passwords in plain text.\nSave it somewhere you trust.",
    "Şifrelerle indir": "Download with passwords",
    "İndir": "Download",
    "dosya geçerli JSON değil": "file is not valid JSON",
    "Dosyada ": "The file has ",
    " plan, ": " plan(s), ",
    " mail profili var": " mail profile(s)",
    " (sürüm ": " (version ",
    "Mevcut planların korunsun mu, yoksa yerlerine bunlar mı geçsin?": "Keep your existing plans, or replace them with these?",
    "Ayar yükle": "Load settings",
    "Ekle (mevcutlar kalsın)": "Add (keep existing)",
    "Durum okunamadı: ": "Could not read status: ",
    "Link ekli: ": "Link is in place: ",
    "Link yok. Eklenecek adres: ": "No link. Address to add: ",
    "durum okunamadı": "could not read status",
    "okunamadı": "could not read",
    "Çalışan betik değişmiş": "The running script has changed",
    "Güncelleme yaptıysan normaldir; referansı yenile: ": "Normal if you just updated; refresh the baseline: ",
    "Betik okunamadi.": "Could not read the script.",
    "Butunluk referansi henuz alinmadi.": "Integrity baseline not taken yet.",
    "Calisan betik degismis. Beklenen {b}…, bulunan {s}…. Guncelleme yaptiysan normaldir; yapmadiysan INCELE.": "The running script changed. Expected {b}…, found {s}…. Normal if you updated; if not, INVESTIGATE.",
    "Telegram bildirimi": "Telegram notifications",
    "Bot jetonu": "Bot token",
    "Sohbet (chat id)": "Chat (chat id)",
    "✈ Test mesajı gönder": "✈ Send test message",
    "jeton kayıtlı": "token saved",
    "jeton girilmemiş": "no token",
    "önce bot jetonunu gir ve kaydet": "enter the bot token and save first",
    "önce Kaydet'e bas, sonra test et": "press Save first, then test",
};
/**
 * İki dilli arayüz. Türkçe kaynak dildir; İngilizce çalışma anında uygulanır.
 *
 * Neden böyle: HTML'de 388, TypeScript'te 225 görünür metin var. Hepsini anahtara
 * çevirmek yerine Türkçe metnin kendisi anahtar olarak kullanılır. Böylece işaretleme
 * hiç değişmez, yeni metin eklerken sözlüğe satır eklemek yeter — unutulursa Türkçesi
 * görünür, arayüz bozulmaz.
 */
let DIL = "tr";
/** Türkçe metni geçerli dile çevirir. Karşılığı yoksa olduğu gibi döner. */
function C(s) {
    if (DIL === "tr")
        return s;
    const d = EN[s];
    if (d !== undefined)
        return d;
    // Sonundaki noktalama/boşluk farkını tolere et: "Kaydet…" -> "Kaydet"
    const kirp = s.trim();
    if (kirp !== s && EN[kirp] !== undefined)
        return s.replace(kirp, EN[kirp]);
    return s;
}
function dilAl() { return DIL; }
function dilKur(d) {
    DIL = d === "en" ? "en" : "tr";
    try {
        localStorage.setItem("pg_dil", DIL);
    }
    catch { /* localStorage kapali olabilir */ }
    document.documentElement.setAttribute("lang", DIL);
}
/** Sayfadaki duragan metinleri (metin dugumleri, title, placeholder) cevirir. */
function sayfayiCevir(kok) {
    if (DIL === "tr")
        return;
    const k = kok || document.body;
    const yuru = document.createTreeWalker(k, NodeFilter.SHOW_TEXT);
    const dugumler = [];
    let n = yuru.nextNode();
    while (n) {
        dugumler.push(n);
        n = yuru.nextNode();
    }
    for (const d of dugumler) {
        const ham = d.nodeValue || "";
        const kirp = ham.trim();
        if (!kirp)
            continue;
        const ust = d.parentElement;
        if (ust && (ust.tagName === "SCRIPT" || ust.tagName === "STYLE"))
            continue;
        const yeni = C(kirp);
        if (yeni !== kirp)
            d.nodeValue = ham.replace(kirp, yeni);
    }
    const oznitelikler = [["title", "title"], ["placeholder", "placeholder"]];
    for (const [sec, oz] of oznitelikler) {
        Array.prototype.slice.call(k.querySelectorAll("[" + sec + "]")).forEach((e) => {
            const v = e.getAttribute(oz);
            if (v) {
                const y = C(v);
                if (y !== v)
                    e.setAttribute(oz, y);
            }
        });
    }
}
/** Dil secicisi degisince: kaydet, sayfayi cevir, arayuzu yeniden ciz. */
function dilDegistir(d) {
    dilKur(d);
    // Sunucuya da bildir: mailler ve login sayfasi ayni dilde olsun.
    void fetch("/api/settings/save", {
        method: "POST",
        headers: { "X-CSRF-Token": csrf(), "Content-Type": "application/json" },
        body: JSON.stringify({ dil: d }),
    }).finally(() => location.reload());
}
/** Acilista kayitli dili uygula. */
function dilBaslat() {
    let d = "tr";
    try {
        d = localStorage.getItem("pg_dil") || "tr";
    }
    catch { /* yok say */ }
    dilKur(d);
    const sec = document.getElementById("dilsec");
    if (sec)
        sec.value = dilAl();
    sayfayiCevir();
}
/* ---------- uygulama ici diyaloglar ---------- */
/* Tarayicinin confirm/prompt kutulari "web sitesinin mesaji" diye gorunuyor ve
   arayuzle uyumsuz. Ayni isi yapan, uygulamanin kendi penceresi kullanilir. */
let onayCoz = null;
function onayKapat(evet) {
    const girdiAcik = el("onay-girdi-sar").style.display !== "none";
    const deger = girdiAcik ? el("onay-girdi").value : "";
    el("m-onay").classList.remove("show");
    const c = onayCoz;
    onayCoz = null;
    if (c)
        c(evet ? deger : null);
}
/** confirm() yerine. true/false doner.
 *  Buton etiketleri verilebilir: "Sil / Vazgec" gibi netlik gerektiren yerlerde
 *  "Tamam / Iptal" ne olacagini soylemiyordu. */
function onay(metin, baslik, evetEtiket, hayirEtiket) {
    return new Promise((coz) => {
        setTxt("onay-baslik", baslik || C("Onay"));
        setTxt("onay-evet", evetEtiket || C("Tamam"));
        setTxt("onay-hayir", hayirEtiket || C("İptal"));
        setHtml("onay-metin", metin.split("\n").map((x) => esc(x)).join("<br>"));
        el("onay-girdi-sar").style.display = "none";
        onayCoz = (d) => coz(d !== null);
        el("m-onay").classList.add("show");
        el("onay-evet").focus();
    });
}
/** prompt() yerine. Girilen metni ya da iptalde null doner. */
function sorMetin(metin, varsayilan, baslik) {
    return new Promise((coz) => {
        setTxt("onay-baslik", baslik || C("Bilgi gerekli"));
        setTxt("onay-evet", C("Tamam"));
        setTxt("onay-hayir", C("İptal"));
        setHtml("onay-metin", metin.split("\n").map((x) => esc(x)).join("<br>"));
        el("onay-girdi-sar").style.display = "";
        el("onay-girdi").value = varsayilan || "";
        onayCoz = coz;
        el("m-onay").classList.add("show");
        el("onay-girdi").focus();
    });
}
/* ---------- pencereleri suruklenebilir yap ---------- */
function surukleKur() {
    Array.prototype.slice.call(document.querySelectorAll(".modal > h2")).forEach((bas) => {
        const pencere = bas.parentElement;
        if (!pencere || bas.dataset.suruklenir)
            return;
        bas.dataset.suruklenir = "1";
        let x = 0, y = 0, bx = 0, by = 0, aktif = false;
        bas.addEventListener("pointerdown", (e) => {
            aktif = true;
            x = e.clientX;
            y = e.clientY;
            const m = /translate\((-?[\d.]+)px,\s*(-?[\d.]+)px\)/.exec(pencere.style.transform || "");
            bx = m ? parseFloat(m[1]) : 0;
            by = m ? parseFloat(m[2]) : 0;
            bas.setPointerCapture(e.pointerId);
        });
        bas.addEventListener("pointermove", (e) => {
            if (!aktif)
                return;
            pencere.style.transform = `translate(${bx + e.clientX - x}px, ${by + e.clientY - y}px)`;
        });
        const birak = (e) => {
            if (!aktif)
                return;
            aktif = false;
            try {
                bas.releasePointerCapture(e.pointerId);
            }
            catch { /* yok say */ }
        };
        bas.addEventListener("pointerup", birak);
        bas.addEventListener("pointercancel", birak);
        // cift tiklama: pencereyi ortala
        bas.addEventListener("dblclick", () => { pencere.style.transform = ""; });
    });
}
/* Sag tik menusu.
 *
 * Tarayicinin kendi menusu bu arayuzde ise yaramiyor: kullanicinin isteyecegi
 * seyler "yedegi baslat", "dosya adini kopyala", "bu plani disa aktar" gibi
 * uygulamaya ozel eylemler. Butonlari her karta dizmek yerine sik kullanilanlari
 * butonda, gerisini sag tikta topluyoruz.
 *
 * Tek bir delege dinleyici var; listeler her yenilendiginde tekrar baglamak
 * gerekmiyor (kartlar refresh ile bastan uretiliyor).
 */
const MENU_KAYIT = [];
let menuEl = null;
let menuOgeleri = [];
let menuSecim = -1;
function menuKapat() {
    if (!menuEl)
        return;
    menuEl.remove();
    menuEl = null;
    menuOgeleri = [];
    menuSecim = -1;
}
/** Menuyu imlecin yaninda acar; ekran disina tasarsa ice ceker. */
function menuAc(x, y, ogeler) {
    menuKapat();
    const kutu = document.createElement("div");
    kutu.className = "ctx";
    kutu.setAttribute("role", "menu");
    menuOgeleri = ogeler;
    ogeler.forEach((o, i) => {
        if (o.ayrac) {
            kutu.appendChild(document.createElement("hr"));
            return;
        }
        const d = document.createElement("div");
        if (o.baslik) {
            d.className = "ctx-baslik";
            d.textContent = C(o.etiket || "");
            kutu.appendChild(d);
            return;
        }
        d.className = "ctx-oge" + (o.pasif ? " pasif" : "") + (o.tehlike ? " tehlike" : "");
        d.setAttribute("role", "menuitem");
        d.setAttribute("data-i", String(i));
        if (o.ipucu)
            d.title = C(o.ipucu);
        d.innerHTML = '<span class="ctx-simge">' + (o.simge || "") + "</span>"
            + '<span class="ctx-metin"></span>';
        d.querySelector(".ctx-metin").textContent = C(o.etiket || "");
        if (!o.pasif) {
            d.onclick = (e) => { var _a; e.stopPropagation(); menuKapat(); void ((_a = o.is) === null || _a === void 0 ? void 0 : _a.call(o)); };
            d.onmouseenter = () => menuVurgula(i);
        }
        kutu.appendChild(d);
    });
    // Once gorunmez yerlestir ki gercek olcusunu okuyabilelim
    kutu.style.left = "-9999px";
    kutu.style.top = "-9999px";
    document.body.appendChild(kutu);
    const g = kutu.getBoundingClientRect();
    const bosluk = 6;
    let sol = x, ust = y;
    if (sol + g.width + bosluk > window.innerWidth)
        sol = Math.max(bosluk, x - g.width);
    if (ust + g.height + bosluk > window.innerHeight)
        ust = Math.max(bosluk, y - g.height);
    // Menu ekrandan uzunsa kendi icinde kaysin
    if (g.height + bosluk * 2 > window.innerHeight) {
        ust = bosluk;
        kutu.style.maxHeight = window.innerHeight - bosluk * 2 + "px";
        kutu.style.overflowY = "auto";
    }
    kutu.style.left = sol + "px";
    kutu.style.top = ust + "px";
    menuEl = kutu;
}
function menuVurgula(i) {
    if (!menuEl)
        return;
    Array.prototype.slice.call(menuEl.querySelectorAll(".ctx-oge"))
        .forEach((d) => d.classList.remove("on"));
    menuSecim = i;
    const d = menuEl.querySelector('.ctx-oge[data-i="' + i + '"]');
    if (d) {
        d.classList.add("on");
        d.scrollIntoView({ block: "nearest" });
    }
}
/** Klavyeyle gezinirken atlanacak ogeleri (ayrac, baslik, pasif) es geçer. */
function menuGez(yon) {
    const n = menuOgeleri.length;
    if (!n)
        return;
    let i = menuSecim;
    for (let adim = 0; adim < n; adim++) {
        i = (i + yon + n) % n;
        const o = menuOgeleri[i];
        if (!o.ayrac && !o.baslik && !o.pasif) {
            menuVurgula(i);
            return;
        }
    }
}
/** Bir secici icin sag tik menusu tanimlar. uret() null donerse menu acilmaz. */
function sagTik(secici, uret) {
    MENU_KAYIT.push({ secici, uret });
}
function menuTetikle(x, y, hedef, olay) {
    const e = hedef;
    if (!e || !e.closest)
        return false;
    for (const k of MENU_KAYIT) {
        const kap = e.closest(k.secici);
        if (!kap)
            continue;
        const ogeler = k.uret(kap, olay);
        if (!ogeler || !ogeler.length)
            return false;
        menuAc(x, y, ogeler);
        return true;
    }
    return false;
}
function menuKur() {
    document.addEventListener("contextmenu", (e) => {
        // Metin secilmisse tarayicinin kopyala menusu daha faydali; karisma.
        const secili = window.getSelection();
        if (secili && String(secili).length > 2 && !e.shiftKey)
            return;
        if (menuTetikle(e.clientX, e.clientY, e.target, e))
            e.preventDefault();
        else
            menuKapat();
    });
    document.addEventListener("click", (e) => {
        if (menuEl && !e.target.closest(".ctx"))
            menuKapat();
    });
    document.addEventListener("keydown", (e) => {
        var _a;
        if (!menuEl)
            return;
        if (e.key === "Escape") {
            e.preventDefault();
            menuKapat();
        }
        else if (e.key === "ArrowDown") {
            e.preventDefault();
            menuGez(1);
        }
        else if (e.key === "ArrowUp") {
            e.preventDefault();
            menuGez(-1);
        }
        else if (e.key === "Enter" && menuSecim >= 0) {
            e.preventDefault();
            const o = menuOgeleri[menuSecim];
            menuKapat();
            void ((_a = o === null || o === void 0 ? void 0 : o.is) === null || _a === void 0 ? void 0 : _a.call(o));
        }
    }, true);
    window.addEventListener("resize", menuKapat);
    window.addEventListener("blur", menuKapat);
    document.addEventListener("scroll", menuKapat, true);
    // Dokunmatik: uzun basma sag tik yerine gecer
    let zaman = 0, bx = 0, by = 0;
    document.addEventListener("touchstart", (e) => {
        if (e.touches.length !== 1)
            return;
        const t = e.touches[0];
        bx = t.clientX;
        by = t.clientY;
        const hedef = e.target;
        zaman = window.setTimeout(() => menuTetikle(bx, by, hedef, e), 520);
    }, { passive: true });
    const iptal = () => { if (zaman) {
        clearTimeout(zaman);
        zaman = 0;
    } };
    document.addEventListener("touchmove", iptal, { passive: true });
    document.addEventListener("touchend", iptal, { passive: true });
}
/* ---------- ortak yardimcilar ---------- */
/** Panoya yazar. HTTPS/localhost disinda clipboard API yok; eski yola duser. */
async function panoyaYaz(metin, ne) {
    try {
        if (navigator.clipboard && window.isSecureContext) {
            await navigator.clipboard.writeText(metin);
        }
        else {
            const t = document.createElement("textarea");
            t.value = metin;
            t.style.position = "fixed";
            t.style.opacity = "0";
            document.body.appendChild(t);
            t.select();
            document.execCommand("copy");
            t.remove();
        }
        flash((ne ? C(ne) + " " : "") + C("panoya kopyalandı"), true);
    }
    catch {
        flash(C("kopyalanamadı — metni elle seçmen gerekiyor"), false);
    }
}
/** Metni dosya olarak indirir (log, plan disa aktarimi vb.). */
function dosyaIndir(ad, icerik, tip) {
    const b = new Blob([icerik], { type: tip || "text/plain;charset=utf-8" });
    const u = URL.createObjectURL(b);
    const a = document.createElement("a");
    a.href = u;
    a.download = ad;
    document.body.appendChild(a);
    a.click();
    a.remove();
    window.setTimeout(() => URL.revokeObjectURL(u), 1000);
}
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
const WD = ["Pzt", "Sal", C("Çar"), "Per", "Cum", "Cmt", "Paz"];
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
function markDirty() { dirty = true; taslakPlanla(); }
/* ---------- F5 taslagi ----------
 * beforeunload uyarisi yalnizca soruyor; "evet" dersen yazdiklarin gidiyordu.
 * Acik formu araliklarla yerel olarak sakliyoruz, donunce geri yuklemeyi oneriyoruz.
 * Sunucuya hicbir sey gitmez; taslak yalnizca bu tarayicida durur. */
const TASLAK = "pg_taslak";
let taslakZamanlayici = 0;
/** #m-edit icindeki tum girdilerin anlik degeri. Alan tablosuna bagimli degil:
 *  forma yeni bir alan eklendiginde taslak da kendiliginden kapsar. */
function formAnlik() {
    const o = {};
    const kap = document.getElementById("m-edit");
    if (!kap)
        return o;
    Array.prototype.slice.call(kap.querySelectorAll("input,select,textarea"))
        .forEach((g) => {
        if (!g.id || g.type === "file" || g.type === "password")
            return;
        o[g.id] = g.type === "checkbox" || g.type === "radio" ? g.checked : g.value;
    });
    o.__gunler = Array.prototype.slice.call(el("e-wd").querySelectorAll("input:checked"))
        .map((c) => c.value);
    return o;
}
function formGeriYukle(o) {
    for (const k of Object.keys(o)) {
        if (k === "__gunler")
            continue;
        const g = document.getElementById(k);
        if (!g)
            continue;
        if (g.type === "checkbox" || g.type === "radio")
            g.checked = Boolean(o[k]);
        else
            g.value = String(o[k]);
    }
    const gunler = o.__gunler || [];
    Array.prototype.slice.call(el("e-wd").querySelectorAll("input"))
        .forEach((c) => { c.checked = gunler.indexOf(c.value) >= 0; });
}
function taslakPlanla() {
    if (taslakZamanlayici)
        return; // saniyede bir yaz, her tusa basista degil
    taslakZamanlayici = window.setTimeout(() => {
        taslakZamanlayici = 0;
        if (!dirty || !el("m-edit").classList.contains("show"))
            return;
        try {
            localStorage.setItem(TASLAK, JSON.stringify({
                pid: EDIT, sihirbaz: wSihirbaz, adim: wAktif,
                zaman: Date.now(), alanlar: formAnlik(),
            }));
        }
        catch { /* kota dolu olabilir, taslak zorunlu degil */ }
    }, 1000);
}
function taslakSil() {
    try {
        localStorage.removeItem(TASLAK);
    }
    catch { /* yok say */ }
}
/** Acilista yarim kalmis duzenleme varsa geri yuklemeyi onerir. */
async function taslakSor() {
    let t = null;
    try {
        t = JSON.parse(localStorage.getItem(TASLAK) || "null");
    }
    catch {
        t = null;
    }
    if (!t || !t.alanlar)
        return;
    const yas = (Date.now() - (t.zaman || 0)) / 60000;
    if (yas > 60 * 24) {
        taslakSil();
        return;
    } // bir gunden eski taslak ise yaramaz
    if (t.pid && S && !S.plans.some((p) => p.id === t.pid)) {
        taslakSil();
        return;
    }
    const ad = String(t.alanlar["e-name"] || "").trim();
    const ne = t.pid ? C("Plan: ") + (ad || t.pid) : C("yeni plan");
    const sure = yas < 1 ? C("az önce") : Math.round(yas) + C(" dakika önce");
    if (!await onay(C("Yarım kalmış bir düzenleme var — ") + ne + " (" + sure + ").\n"
        + C("Kaydedilmemişti. Geri yüklensin mi?"), C("Yarım kalan düzenleme"), C("Geri yükle"), C("Sil"))) {
        taslakSil();
        return;
    }
    openEditor(t.pid);
    formGeriYukle(t.alanlar);
    if (t.sihirbaz) {
        wAktif = Math.max(1, Math.min(ADIMLAR.length, t.adim || 1));
        wGoster();
    }
    ceviriUygula();
    ramHint();
    saklamaIpucu();
    markDirty();
    flash(C("taslak geri yüklendi — kaydetmedikçe uygulanmaz"), true);
}
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
/** Dogrulama turunda ilk hatali alanin id'si. hataOdakla() bunu kullanir. */
let ilkHataAlani = null;
function hataTuruBaslat() { ilkHataAlani = null; }
/** Hatali alan ekranin disinda kalabiliyordu: sihirbazda dogru adima gec,
 *  alani ortala ve odagi ver ki kullanici neyi duzeltecegini gorsun. */
function hataOdakla() {
    const id = ilkHataAlani;
    if (!id)
        return;
    const e = document.getElementById(id);
    if (!e)
        return;
    if (wSihirbaz) {
        const adim = ADIM_ALANLARI.findIndex((liste) => liste.indexOf(id) >= 0);
        if (adim >= 0 && adim + 1 !== wAktif) {
            wAktif = adim + 1;
            wGoster();
        }
    }
    const kutu = e.closest("details");
    if (kutu && !kutu.open)
        kutu.open = true;
    window.setTimeout(() => {
        try {
            e.scrollIntoView({ block: "center", behavior: "smooth" });
        }
        catch {
            e.scrollIntoView();
        }
        try {
            e.focus({ preventScroll: true });
        }
        catch {
            e.focus();
        }
        e.classList.add("odak");
        window.setTimeout(() => e.classList.remove("odak"), 1400);
    }, 60);
}
function bad(id, msg) {
    if (!ilkHataAlani)
        ilkHataAlani = id;
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
/** Elle kapasite alani yalnizca manuel kipte gorunsun. */
function bwLinkKipi() {
    const satir = document.getElementById("bwlink-satir");
    if (satir)
        satir.style.display = val("e-bwlmode") === "manuel" ? "" : "none";
}
/** Yalnizca gorunumu ayarlar; "degisti" damgasi BIRAKMAZ.
 *  openEditor formu kurarken bunu cagirir. */
function bwAutoUygula() {
    const acik = chk("e-bwauto");
    el("bwauto-box").style.display = acik ? "" : "none";
    fld("e-bw").disabled = acik;
    fld("e-bwsch").disabled = acik;
}
/** Kullanici kutuyu tikladiginda. Onceden bu tek fonksiyon vardi ve openEditor
 *  da onu cagiriyordu: her plan acilisi aninda "kaydedilmemis degisiklik var"
 *  sayiliyor, hicbir sey degistirmeden kapatirken uyari cikiyordu. */
function bwAutoToggle() { bwAutoUygula(); markDirty(); }
async function loadIfaces(secili) {
    try {
        const j = await api("/api/ifaces");
        const list = j.ifaces || [];
        const etiket = (i) => esc(i.name)
            + (i.kopru ? C("  · köprü") : "")
            + (i.hiz ? "  · " + i.hiz + " Mbit" : "")
            + "  · " + hb(i.tx) + C(" gönderilmiş")
            + (i.onerilen ? C("  ← önerilen") : "");
        setHtml("e-bwif", '<option value="">' + C("(otomatik: ") + esc(j.onerilen || "-")
            + ")</option>" + list.map((i) => '<option value="' + esc(i.name) + '">'
            + etiket(i) + "</option>").join(""));
        setVal("e-bwif", secili);
        setHtml("e-bwifhint", C("Otomatik seçim: ") + "<b>" + esc(j.onerilen || "-") + "</b> ("
            + esc(C(j.onerilen_neden || "")) + "). "
            + C("Köprü (vmbr0) sayaçları VM'ler arası yerel trafiği de sayar — o trafik "
                + "internete hiç çıkmaz, yükleme hızınla yarışmaz. Köprünün altındaki "
                + "bond/fiziksel arayüz doğru ölçümü verir."));
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
    const ip = (t) => ' title="' + esc(C(t)) + '"';
    if (p.running)
        return '<span class="pill run">● ÇALIŞIYOR</span>';
    if (!p.enabled)
        return '<span class="pill off"' + ip("Zamanlama kapalı; dosyalara dokunulmaz.")
            + ">KAPALI</span>";
    // Hic calismamis plan "ATLANDI" gibi gorunmesin: ikisi ayri sey.
    if (!s.last_run)
        return '<span class="pill idle"'
            + ip("Bu plan henüz hiç çalışmadı. İlk çalışma: " + (p.next_run || "-"))
            + ">⏳ BEKLİYOR</span>";
    if (s.status === "basarili")
        return '<span class="pill ok">✔ BAŞARILI</span>';
    if (s.status === "HATA")
        return '<span class="pill err">✖ HATA</span>';
    if (s.status === "atlandi")
        return '<span class="pill run"'
            + ip("Son denemede iş yapılmadı — genelde vzdump hâlâ çalışıyordu. "
                + "Sebep kartın altındaki özet satırında yazar. Sonraki turda tekrar denenir.")
            + ">⏸ ATLANDI</span>";
    return '<span class="pill idle">' + esc(s.status || "—").toUpperCase() + "</span>";
}
/** Zamanlayici durmussa bunu susarak geçmek olmaz: timer olurse hicbir yedek
 *  alinmaz ve tek belirtisi "sonraki calisma"nin gecmiste kalmasi olur. */
function saglikCiz() {
    const h = S && S.saglik;
    const kutu = document.getElementById("saglik");
    if (!kutu)
        return;
    const uyarilar = [];
    if (h && h.butunluk === "DEGISTI") {
        // Bu en agiri: calisan kod beklenenden farkli. Ustte ve kirmizi dursun.
        uyarilar.push('<b>🛑 ' + esc(C("Çalışan betik değişmiş")) + "</b>"
            + '<div class="small" style="margin-top:5px">' + esc(h.butunluk_mesaj || "") + "</div>"
            + '<div class="small" style="margin-top:5px">'
            + esc(C("Güncelleme yaptıysan normaldir; referansı yenile: "))
            + "<code>pve-gdrive butunluk --sabitle</code></div>");
    }
    if (h && h.tick !== "iyi") {
        uyarilar.push('<b>⚠ ' + esc(C(h.tick === "gecikmis" ? "Zamanlayıcı gecikmiş"
            : "Zamanlayıcı hiç çalışmadı"))
            + "</b><div class=\"small\" style=\"margin-top:5px\">" + esc(h.tick_mesaj || "") + "</div>"
            + '<div class="small" style="margin-top:5px">'
            + esc(C("Kontrol et: ")) + "<code>systemctl status pve-gdrive-tick.timer</code></div>");
    }
    if (!uyarilar.length) {
        kutu.style.display = "none";
        kutu.textContent = "";
        return;
    }
    kutu.style.display = "";
    kutu.className = "card uyari-kutu";
    kutu.innerHTML = uyarilar.join('<hr style="border:0;border-top:1px solid #4a2222;margin:9px 0">');
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
    h += '</tbody></table><div class="hintC(">Bu iş sunucuda çalışıyor — sayfayı yenilesen de '
        + ")kapatsan da devam eder.</div></div>";
    return h;
}
function planCard(p) {
    const s = p.state || {};
    return '<div class="plan' + (p.id === sel ? " sel" : "") + '" data-plan="' + esc(p.id)
        + "\" onclick=\"pick('" + p.id + "')\">"
        + "<h3>" + esc(p.name) + pillOf(p) + "</h3>"
        + progBox(p)
        + '<div class="row"><span>Kaynak</span><b>' + esc(p.src_dir)
        + (p.src_exists ? ' <span class="small">(' + p.src_dumps + " dosya)</span>" : ' <span class="pill err">yok</span>')
        + "</b></div>"
        + '<div class="row"><span>Hedef</span><b>' + esc(p.remote)
        + ((p.yedek_hedefler || []).length
            ? ' <span class="pill idle" title="' + esc(C("Birincil çalışmazsa sırayla denenir: "))
                + esc((p.yedek_hedefler || []).join(", ")) + '">+'
                + (p.yedek_hedefler || []).length + C(" yedek") + "</span>" : "")
        + "</b></div>"
        + (s.aktif_hedef && s.aktif_hedef !== p.remote
            ? '<div class="row"><span>' + C("Son yazılan") + '</span><b class="uyari-metin">'
                + esc(s.aktif_hedef) + "</b></div>" : "")
        + '<div class="row"><span>Program</span><b>' + progOf(p) + "</b></div>"
        + '<div class="row"><span>Sonraki</span><b>' + esc(p.next_run || "-") + "</b></div>"
        + '<div class="row"><span>Saklama</span><b>' + p.keep_days + C(" gün · min ") + p.keep_count
        + C(" set · çöp ") + p.drive_trash_days + C(" gün") + "</b></div>"
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
        + C("%) · çöp: ") + hb(q.trashed || 0) + C(" · boş: ") + hb(q.free || 0) + "</div></div>"
        + '<div class="cols"><div class="panel"><h2>Yedekler (Drive)</h2><table><thead><tr><th>Tarih</th>'
        + '<th>VM/CT</th><th class="r">Boyut</th><th>Dosya</th></tr></thead><tbody>'
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
    const sunucuDil = (S.settings && S.settings.dil);
    const sec = document.getElementById("dilsec");
    if (sec && sunucuDil && sec.value !== dilAl())
        sec.value = dilAl();
    const tls = S.tls;
    setHtml("tlsrozet", tls && tls.aktif
        ? '<span class="pill ok" title="' + esc(C("Bağlantı şifreli")) + '">🔒 HTTPS</span>'
        : '<span class="pill err" title="' + esc(C("Trafik şifresiz — yalnızca VPN içinde kullan"))
            + '">⚠ HTTP</span>');
    const g = S.guncelleme;
    setHtml("uprozet", g && g.yeni_var
        ? '<span class="pill run" title="' + esc(C("Yeni sürüm var: ") + (g.uzak || ""))
            + '" style="cursor:pointer" onclick="openSettings()">⬆ ' + esc(g.uzak || "") + C(" hazır")
            + "</span>"
        : '<span class="small" title="' + esc(C("Kurulu sürüm")) + '">v' + esc(S.surum || "?")
            + "</span>");
    saglikCiz();
    kullaniciCiz();
    setTxt("hinfo", ps.length + " plan" + (running ? " · " + running + " çalışıyor" : "")
        + (S.updated ? " · durum: " + S.updated : "") + (S.smtp_ready ? "" : " · mail profili yok"));
    setHtml("plans", ps.map(planCard).join("")
        || '<div class="card">' + C("Henüz plan yok. Sağ üstten + Yeni Plan ile başla.") + "</div>");
    hesapSerit();
    setHtml("detail", detail(ps.filter((p) => p.id === sel)[0]));
    ceviriUygula();
    const tabs = [["all", "Tümü"], ["system", "Sistem"]]
        .concat(ps.map((p) => [p.id, p.name]));
    setHtml("logtabs", tabs.map((t) => '<button class="' + (LOGSRC === t[0] ? "on" : "")
        + "\" onclick=\"setLog('" + t[0] + "')\">" + esc(t[1]) + "</button>").join(""));
}
function setLog(src) { LOGSRC = src; remember(); void loadLog(); }
/** Dinamik uretilen icerik de cevrilsin (kartlar, tablolar, modallar). */
function ceviriUygula() { if (dilAl() !== "tr")
    sayfayiCevir(); }
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
                + esc(q.error || C("kota okunamadı")) + "</div></div>";
        }
        const pct = x.pct === null || x.pct === undefined ? 0 : x.pct;
        const sinif = pct >= 90 ? " dolu" : (pct >= 75 ? " uyari" : "");
        return '<div class="hesap' + sinif + '" onclick="openAccounts()" title="Yönetmek için tıkla">'
            + '<div class="ad"><b>' + esc(x.name) + "</b><span>" + pct.toFixed(0) + "%</span></div>"
            + '<div class="mini"><i style="width:' + Math.min(100, pct) + '%"></i></div>'
            + '<div class="alt">' + hb(q.used) + " / " + hb(q.total)
            + (q.trashed ? C(" · çöp ") + hb(q.trashed) : "") + "</div></div>";
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
    akisBaslat();
    yoklamaAyarla();
}
/* ---------- canli olay akisi (SSE) ----------
 * Once birkac saniyede bir /api/status cekiliyordu: hicbir sey degismese de
 * istek gidiyor, degisiklik ise gec gorunuyordu. Artik sunucu itiyor.
 * Akis kurulamazsa (eski vekil, kapali ayar) eski yoklama moduna dusuyoruz —
 * arayuz her durumda calisir kalir. */
let akis = null;
let akisCanli = false;
let akisHata = 0;
function akisDurumu(canli) {
    if (akisCanli === canli)
        return;
    akisCanli = canli;
    const e = document.getElementById("canli");
    if (e) {
        e.className = "pill " + (canli ? "ok" : "idle");
        e.textContent = canli ? C("● CANLI") : C("○ yoklama");
        e.title = canli ? C("Sunucu değişiklikleri anında gönderiyor")
            : C("Canlı akış yok — belirli aralıklarla yenileniyor");
    }
    yoklamaAyarla();
}
/** Akis calisirken yoklama tamamen durur; yalnizca yedek olarak seyrek doner. */
function yoklamaAyarla() {
    const base = ((S && S.settings && S.settings.ui_refresh_sec) || 5) * 1000;
    window.clearInterval(refTimer);
    const iv = akisCanli ? 60000 : (running ? Math.min(base, 2000) : base);
    refTimer = window.setInterval(() => void refresh(), iv);
}
function akisBaslat() {
    if (akis || typeof EventSource === "undefined")
        return;
    if (S && S.settings && S.settings.sse_enabled === false)
        return;
    try {
        akis = new EventSource("/api/events");
    }
    catch {
        akis = null;
        return;
    }
    akis.addEventListener("open", () => { akisHata = 0; akisDurumu(true); });
    akis.addEventListener("durum", (e) => {
        try {
            const y = JSON.parse(e.data);
            if (y.login) {
                location.reload();
                return;
            }
            // csrf ve kullanici adi yalnizca /api/status ile gelir (oturuma bagli).
            // Akis paketi public_status() uretir; oradan gelmeyeni mevcudundan koru,
            // yoksa her canli guncellemede baslikta ad kaybolur.
            if (S && !y.csrf)
                y.csrf = S.csrf;
            if (S && !y.user)
                y.user = S.user;
            S = y;
            akisDurumu(true);
            render();
        }
        catch { /* bozuk paket: bir sonraki tazeleme duzeltir */ }
    });
    akis.addEventListener("ilerleme", (e) => {
        try {
            const m = JSON.parse(e.data);
            if (!S)
                return;
            S.plans.forEach((p) => {
                const g = m[p.id];
                p.progress = g;
                p.running = Boolean(g);
            });
            running = S.plans.filter((p) => p.running).length;
            render();
        }
        catch { /* yok say */ }
    });
    akis.addEventListener("log", (e) => {
        try {
            logEkle(JSON.parse(e.data).satirlar || []);
        }
        catch { /* yok say */ }
    });
    akis.addEventListener("kalp", () => akisDurumu(true));
    akis.addEventListener("error", () => {
        akisDurumu(false);
        // EventSource kendi yeniden baglanir; ustuste basarisiz olursa vazgec
        if (++akisHata >= 6 && akis) {
            akis.close();
            akis = null;
        }
    });
}
/** Akistan gelen satirlari log kutusuna ekler. Secili kaynaga gore suzulur,
 *  kutu sonundaysa asagi kaydirilir (okurken zipladigi olmasin). */
function logEkle(satirlar) {
    if (!satirlar.length)
        return;
    const kutu = el("log");
    const dipte = kutu.scrollHeight - kutu.scrollTop - kutu.clientHeight < 40;
    const suz = satirlar.filter((x) => {
        if (LOGSRC === "all")
            return true;
        const m = /\|\s*\[([^\]]+)\]/.exec(x);
        return LOGSRC === "system" ? !m : Boolean(m && m[1] === LOGSRC);
    });
    if (!suz.length)
        return;
    kutu.textContent = ((kutu.textContent || "") + "\n" + suz.join("\n")).trim();
    // Bellek sinirli kalsin: en fazla son 2000 satir tutulur
    const t = (kutu.textContent || "").split("\n");
    if (t.length > 2000)
        kutu.textContent = t.slice(-2000).join("\n");
    if (dipte)
        kutu.scrollTop = kutu.scrollHeight;
}
async function act(d, pid) {
    flash(C("çalışıyor…"), true);
    try {
        const j = await api("/api/action?do=" + d + "&plan=" + encodeURIComponent(pid), { method: "POST" });
        flash(j.msg || "tamam", j.ok);
    }
    catch {
        flash("hata", false);
    }
    if (!akisCanli)
        window.setTimeout(() => void refresh(), 900);
}
async function delPlan(pid) {
    if (!await onay(C("Plan silinsin mi? Drive'daki yedek dosyalarına dokunulmaz.")))
        return;
    const j = await api("/api/plan/delete?plan=" + encodeURIComponent(pid), { method: "POST" });
    flash(j.msg || "", j.ok);
    void refresh();
}
async function logout() {
    // Cikis her zaman sorulur. Yanlislikla (ya da beklenmedik bir yoldan)
    // tetiklenen bir cikis, "beni hatirla" oturumunu sessizce yok ediyordu.
    const metin = dirty
        ? C("Kaydedilmemiş değişiklikler var. Yine de çıkılsın mı?")
        : C("Oturumu kapatmak istediğine emin misin?\n"
            + "\"Beni hatırla\" işaretlemiş olsan bile hatırlanan oturum silinir.");
    if (!await onay(metin, C("Çıkış"), C("Çık"), C("Vazgeç")))
        return;
    await api("/logout", { method: "POST" });
    location.reload();
}
/** Basliktaki kullanici adi ve cikis dugmesi. */
function kullaniciCiz() {
    const e = document.getElementById("kullanici");
    if (!e || !S)
        return;
    e.textContent = S.user || "";
    e.title = C("Oturum sahibi");
}
/* ---------- plan sihirbazi ---------- */
const ADIMLAR = [C("Plan"), "Kaynak", "Hedef", "Saklama", "Zamanlama", C("Aktarım"), "Bildirim", C("Özet")];
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
        hataOdakla();
        flash(C("bu adımda eksik veya hatalı alan var"), false);
        return;
    }
    wAktif = Math.min(ADIMLAR.length, Math.max(1, wAktif + yon));
    wGoster();
    if (wAktif === 4)
        void kapasiteYukle();
    el("m-edit").scrollTop = 0;
}
function wSatir(baslik, deger, uyari) {
    return '<tr' + (uyari ? ' class="uyari"' : "") + "><td>" + esc(baslik) + "</td><td>"
        + esc(deger) + "</td></tr>";
}
function wOzet() {
    const wd = Array.prototype.slice.call(el("e-wd").querySelectorAll("input:checked"))
        .map((c) => WD[Number(c.value) - 1]);
    const bildirim = [chk("e-nsuc") ? C("başarılı") : "", chk("e-nerr") ? "hata" : "",
        chk("e-nskip") ? C("atlandı") : ""].filter(Boolean).join(", ") || C("hiçbiri");
    const oto = chk("e-bwauto");
    const hiz = oto ? ("otomatik (" + val("e-bwmin") + " – " + (val("e-bwmax") || val("e-bw")) + ")")
        : (val("e-bwsch") ? C("çizelge: ") + val("e-bwsch") : val("e-bw"));
    let h = '<table class="ozet"><tbody>';
    h += wSatir(C("Plan adı"), val("e-name"));
    h += wSatir("Durum", chk("e-enabled") ? "etkin" : C("kapalı (zamanlayıcı atlar)"), !chk("e-enabled"));
    h += wSatir("Kaynak", val("e-src"));
    h += wSatir("Hedef", (val("e-acct") || "?") + ":" + val("e-folder"));
    h += wSatir("Saklama", val("e-kd") + C(" gün · VM/CT başına en az ") + val("e-kc") + C(" set"));
    const yh = yhTopla();
    h += wSatir(C("Yedek hedefler"), yh.length
        ? yh.map((x, i) => (i + 1) + ". " + esc(x)).join("<br>")
        : C("yok — sadece birincil hedef"));
    h += wSatir(C("Host yapılandırması"), chk("e-hc")
        ? C("yedekleniyor · son ") + val("e-hck") + C(" arşiv saklanır")
            + (chk("e-hcj") ? C(" · JSON görüntü dahil") : "")
        : C("yedeklenmiyor"), !chk("e-hc"));
    h += wSatir(C("Çöp süresi"), val("e-td") + C(" gün sonra kalıcı silinir"));
    h += wSatir("Program", (wd.length ? wd.join(",") : C("her gün")) + C(" saat ") + val("e-runat"));
    h += wSatir(C("vzdump koruması"), chk("e-wv")
        ? C("bekle, en fazla ") + val("e-wvm") + C(" dk") : C("beklemeden çalış"), !chk("e-wv"));
    h += wSatir("Hatada retention", chk("e-pof")
        ? C("ÇALIŞIR — yükleme başarısızsa da siler") : C("çalışmaz (güvenli)"), chk("e-pof"));
    h += wSatir(C("Hız"), hiz);
    h += wSatir("Mail", (val("e-mail") || "—") + "  ·  " + bildirim);
    h += wSatir(C("Haftalık rapor"), chk("e-wr")
        ? WD[Number(val("e-rday")) - 1] + " " + val("e-rat") + " (" + val("e-rdays") + C(" günlük dönem)")
        : C("kapalı"));
    h += "</tbody></table>";
    const uyarilar = [];
    if (!val("e-acct"))
        uyarilar.push(C("Google hesabı seçilmedi — 3. adıma dön."));
    if (Number(val("e-kc")) === 0)
        uyarilar.push(C("Güvenlik tabanı 0: uzun süre yedeklenmeyen bir VM/CT'nin tüm yedekleri silinebilir."));
    if (chk("e-pof"))
        uyarilar.push(C("Hatada retention açık: yeni yedek çıkmadan eskiler silinebilir."));
    if (!val("e-mail") && (chk("e-nsuc") || chk("e-nerr") || chk("e-wr")))
        uyarilar.push(C("Bildirim seçili ama alıcı adresi boş."));
    if (uyarilar.length) {
        h += '<div class="hint" style="margin-top:10px;color:#ffd479">'
            + uyarilar.map((u) => "⚠ " + esc(u)).join("<br>") + "</div>";
    }
    setHtml("w-ozet", h);
}
/** Hesap ekleme paneli tek bir DOM parcasidir; sihirbaz ile modal arasinda tasinir. */
/* ---------- yedek hedefler ---------- */
// Basarisizlikta sirayla denenecek hedefler. Satirlar dinamik: hesap secimi +
// klasor. Kaydederken "hesap:klasor" dizisine cevrilir.
let YH = [];
function yhCiz() {
    const kutu = document.getElementById("e-yh-liste");
    if (!kutu)
        return;
    if (!YH.length) {
        kutu.innerHTML = '<div class="small">' + C("Yedek hedef yok — sadece birincil kullanılır.")
            + "</div>";
    }
    else {
        kutu.innerHTML = YH.map((h, i) => '<div class="inline yh-satir" style="margin-bottom:6px">'
            + '<span class="small" style="width:18px;text-align:right">' + (i + 1) + ".</span>"
            + '<select class="yh-hesap" data-i="' + i + '" style="flex:0 0 40%"></select>'
            + '<input class="yh-klasor" data-i="' + i + '" style="flex:1" placeholder="'
            + esc(C("klasör")) + '" value="' + esc(h.klasor) + '">'
            + '<button class="sm" type="button" data-yukari="' + i + '" title="'
            + esc(C("Yukarı taşı")) + '"' + (i ? "" : " disabled") + ">↑</button>"
            + '<button class="sm warn" type="button" data-sil="' + i + '" title="'
            + esc(C("Bu hedefi kaldır")) + '">✕</button></div>').join("");
        // Hesap listeleri birincil hedefle ayni kaynaktan doldurulur
        const secenekler = REM.map((r) => '<option value="' + esc(r.name) + '">'
            + esc(r.name) + "</option>").join("");
        Array.prototype.slice.call(kutu.querySelectorAll(".yh-hesap"))
            .forEach((sel) => {
            const i = Number(sel.getAttribute("data-i"));
            sel.innerHTML = secenekler;
            sel.value = YH[i].hesap || (REM[0] && REM[0].name) || "";
            sel.onchange = () => { YH[i].hesap = sel.value; yhOzet(); markDirty(); };
        });
        Array.prototype.slice.call(kutu.querySelectorAll(".yh-klasor"))
            .forEach((g) => {
            const i = Number(g.getAttribute("data-i"));
            g.oninput = () => { YH[i].klasor = g.value; yhOzet(); markDirty(); };
        });
        Array.prototype.slice.call(kutu.querySelectorAll("[data-sil]"))
            .forEach((b) => {
            b.onclick = () => {
                YH.splice(Number(b.getAttribute("data-sil")), 1);
                yhCiz();
                markDirty();
            };
        });
        Array.prototype.slice.call(kutu.querySelectorAll("[data-yukari]"))
            .forEach((b) => {
            b.onclick = () => {
                const i = Number(b.getAttribute("data-yukari"));
                if (i < 1)
                    return;
                const t = YH[i - 1];
                YH[i - 1] = YH[i];
                YH[i] = t;
                yhCiz();
                markDirty();
            };
        });
    }
    yhOzet();
}
/** Ayni hesabin baska klasoru gercek koruma saglamaz; bunu soyle. */
function yhOzet() {
    const e = document.getElementById("e-yh-ozet");
    if (!e)
        return;
    const ana = val("e-acct");
    const ayni = YH.filter((h) => h.hesap === ana).length;
    e.textContent = !YH.length ? ""
        : ayni ? C("⚠ ") + ayni + C(" hedef birincil ile aynı hesapta — hesap kilitlenirse işe yaramaz")
            : YH.length + C(" yedek hedef tanımlı");
    e.className = "small" + (ayni ? " uyari-metin" : "");
}
function yhEkle() {
    YH.push({ hesap: val("e-acct") || (REM[0] && REM[0].name) || "", klasor: "" });
    yhCiz();
    markDirty();
}
function yhTopla() {
    return YH.map((h) => (h.hesap || "").trim() + ":" + (h.klasor || "").trim())
        .filter((x) => x.length > 1 && !x.startsWith(":") && !x.endsWith(":"));
}
function yhDoldur(liste) {
    YH = (liste || []).map((x) => {
        const i = String(x).indexOf(":");
        return i < 0 ? { hesap: String(x), klasor: "" }
            : { hesap: String(x).slice(0, i), klasor: String(x).slice(i + 1) };
    });
    yhCiz();
}
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
    az: { keep_days: 3, keep_count: 3, drive_trash_days: 1, run_at: "03:00", weekdays: [],
        bwlimit: "30M", transfers: 2, checkers: 4, drive_chunk: "64M", min_age_min: 10,
        vzdump_wait_min: 120, weekly_report: true, report_day: 1, report_at: "09:00",
        report_days: 7, report_stale_days: 2, report_quota_warn: 90 },
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
    saklamaIpucu();
    markDirty();
    flash(C("senaryo yüklendi — kaydetmeden uygulanmaz"), true);
}
/** Saklama alanlarinin yardim metni yazilan degere gore guncellenir.
 *  Sabit "14 gun" ornegi, alanda 3 yazarken yaniltiyordu. */
function saklamaIpucu() {
    const gun = Number(val("e-kd")) || 0;
    const taban = Number(val("e-kc")) || 0;
    const cop = Number(val("e-td")) || 0;
    const gunluk = KAP && KAP.analiz && KAP.analiz.ok ? (KAP.analiz.gunluk || 0) : 0;
    let a = gun === 0
        ? C("Gün kuralı kapalı — yalnızca aşağıdaki set tabanı korur.")
        : gun + " " + C("günden eski setler Google çöp kutusuna gönderilir.")
            + " " + C("Günlük yedek alıyorsan Drive'da yaklaşık") + " " + gun + " " + C("set durur.");
    if (gunluk)
        a += " " + C("Ölçülene göre") + " ≈ " + hb(gunluk * gun) + ".";
    setHtml("eg-kd", a);
    let b2 = taban === 0
        ? '<b style="color:#ffd479">' + C("0 riskli: gün kuralı bir VM/CT'nin tüm yedeklerini silebilir.") + "</b>"
        : C("Bir VM/CT uzun süre yedeklenmese bile en yeni") + " <b>" + taban + "</b> "
            + C("seti gün kuralından muaf tutulur.");
    if (taban && gun && taban >= gun)
        b2 += " " + C("Taban gün sayısından büyük ya da eşit: pratikte saklamayı taban belirler.");
    setHtml("eg-kc", b2);
    setHtml("eg-td", cop === 0
        ? C("0 = çöpe uğramadan hemen kalıcı silinir; yanlış silmede geri dönüş olmaz.")
        : C("Yanlış silme olursa") + " <b>" + cop + " " + C("gün") + "</b> "
            + C("içinde Drive'ın çöp kutusundan geri alabilirsin.")
            + (gunluk ? " " + C("Çöpte yaklaşık") + " " + hb(gunluk * cop) + " " + C("bekler.") : ""));
}
function ramHint() {
    const c = String(val("e-chunk") || "").match(/^(\d+(?:\.\d+)?)([KMG])$/i);
    const t = Number(val("e-tr")) || 1;
    if (!c) {
        setTxt("e-ram", C("RAM ≈ parça × transfer"));
        return;
    }
    const carp = { K: 1 / 1024, M: 1, G: 1024 };
    const mb = parseFloat(c[1]) * carp[c[2].toUpperCase()];
    setTxt("e-ram", C("Tahmini rclone RAM kullanımı: ") + Math.round(mb * t) + " MB ("
        + c[0] + " × " + t + " transfer)");
}
function openEditor(pid) {
    const p = pid && S ? S.plans.filter((x) => x.id === pid)[0] : undefined;
    EDIT = pid || null;
    dirty = false;
    wSihirbaz = !pid; // yeni plan: sihirbaz, mevcut plan: tek sayfa form
    wAktif = 1;
    setTxt("ed-title", p ? C("Plan: ") + p.name : C("🧭 Yeni plan sihirbazı"));
    setTxt("ed-alt", p
        ? C("Tüm ayarlar tek sayfada. Alan adlarının üstüne gelince açıklama çıkar.")
        : C("Adım adım ilerle. Hiçbir şey kaydedilmez, son adımda onaylarsın."));
    const d = {
        name: "", enabled: true, src_dir: "/var/lib/vz/dump", remote: C("gdrive:proxmox-yedek"),
        keep_days: 14, keep_count: 3, drive_trash_days: 1, run_at: "03:00", weekdays: [],
        bwlimit: "30M", bwlimit_schedule: "", bwlimit_upload_only: true,
        transfers: 2, checkers: 4, drive_chunk: "64M", rclone_extra: [],
        mail_to: "", smtp_profile: "", notify_success: true, notify_failure: true, notify_skipped: false,
        wait_for_vzdump: true, vzdump_wait_min: 60, min_age_min: 10,
        skip_patterns: ["*.dat", "*.tmp", "*.part"], prune_on_failure: false, weekly_report: true,
        host_config_enabled: true, host_config_json: true, host_config_keep_count: 30,
        report_day: 1, report_at: "09:00", report_days: 7, report_stale_days: 2,
        report_quota_warn: 90, report_mail_to: "",
    };
    const v = (p || d);
    // Ortak alanlar tablodan doldurulur (bkz. alanlar.ts); asagidakiler ozel durumlar.
    alanlariDoldur(v);
    const rp = String(v.remote || C("gdrive:proxmox-yedek")).split(":");
    setVal("e-folder", rp.slice(1).join(":"));
    void loadIfaces(v.bw_auto_iface || "");
    bwAutoUygula();
    setHtml("e-rday", WD.map((n, i) => '<option value="' + (i + 1) + '">' + n + "</option>").join(""));
    setVal("e-rday", v.report_day || 1);
    setHtml("e-wd", WD.map((n, i) => '<label><input type="checkbox" value="' + (i + 1) + '"'
        + ((v.weekdays || []).indexOf(i + 1) >= 0 ? " checked" : "") + ">" + n + "</label>").join(""));
    setTxt("e-srchint", p ? (p.src_exists ? p.src_dumps + " dosya bulundu" : C("⚠ klasör bulunamadı")) : "");
    void loadRemotes(rp[0]).then(() => yhDoldur(v.yedek_hedefler || []));
    loadSmtpSelect(v.smtp_profile);
    void loadStorages();
    ramHint();
    saklamaIpucu();
    bwLinkKipi();
    Array.prototype.slice.call(document.querySelectorAll("#m-edit input,#m-edit select"))
        .forEach((e) => { e.oninput = markDirty; e.onchange = markDirty; });
    fld("e-bwlmode").onchange = () => { bwLinkKipi(); markDirty(); };
    fld("e-acct").onchange = () => { yhOzet(); markDirty(); };
    fld("e-kd").oninput = () => { kapasiteCiz(); saklamaIpucu(); markDirty(); };
    fld("e-kc").oninput = () => { saklamaIpucu(); markDirty(); };
    fld("e-td").oninput = () => { kapasiteCiz(); saklamaIpucu(); markDirty(); };
    fld("e-chunk").oninput = () => { ramHint(); markDirty(); };
    fld("e-tr").oninput = () => { ramHint(); markDirty(); };
    hesapPaneliTasi("w-hesap-yuvasi", false);
    ceviriUygula();
    KAP = null;
    kapAnahtar = "";
    wGoster();
    if (!wSihirbaz)
        void kapasiteYukle();
    openM("m-edit");
}
function validatePlan() {
    // Alanlarin tamami tablodan dogrulanir (bkz. alanlar.ts).
    // Burada yalnizca birden fazla alani birlikte ilgilendiren kurallar kalir.
    hataTuruBaslat();
    let ok = alanlariDogrula();
    if (!val("e-acct"))
        ok = bad("e-acct", C("önce bir Google hesabı ekle")) && ok;
    else
        good("e-acct");
    ok = vRx("e-folder", RX.folder, C('klasör adında : * ? " < > | olamaz')) && ok;
    if (!val("e-folder").trim())
        ok = bad("e-folder", C("hedef klasör gerekli")) && ok;
    if (chk("e-bwauto")) {
        const alt = bwBytes(val("e-bwmin")), ust = bwBytes(val("e-bwmax"));
        if (ust && alt && alt > ust)
            ok = bad("e-bwmin", C("alt sınır üst sınırdan büyük olamaz")) && ok;
    }
    if (Number(val("e-kc")) === 0 && Number(val("e-kd")) === 0) {
        ok = bad("e-kc", C("ikisi birden 0 olamaz — hiç yedek kalmaz")) && ok;
    }
    return ok;
}
async function savePlan() {
    if (!validatePlan()) {
        hataOdakla();
        flash(C("form hatalı — kırmızı alanlara bak"), false);
        return;
    }
    const wd = Array.prototype.slice.call(el("e-wd").querySelectorAll("input:checked"))
        .map((c) => Number(c.value));
    const body = {
        ...alanlariTopla(), // tum ortak alanlar (bkz. alanlar.ts)
        yedek_hedefler: yhTopla(),
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
        taslakSil();
        void refresh();
    }
}
let KAP = null;
let kapAnahtar = "";
/** Kaynak klasoru olcup secilen hesabin kotasina gore projeksiyon gosterir.
 *  Saklama suresini tahminle degil olcumle secmek icin. */
async function kapasiteYukle(zorla) {
    const src = val("e-src").trim(), hesap = val("e-acct");
    if (!src || !hesap) {
        setTxt("kap-durum", C("Kaynak ve hesap seçilince kapasite hesabı burada çıkar."));
        return;
    }
    const anahtar = src + "|" + hesap;
    if (!zorla && anahtar === kapAnahtar && KAP) {
        kapasiteCiz();
        return;
    }
    setTxt("kap-durum", C("ölçülüyor…"));
    try {
        KAP = await api("/api/analiz?src=" + encodeURIComponent(src) + "&hesap=" + encodeURIComponent(hesap));
        kapAnahtar = anahtar;
    }
    catch {
        setTxt("kap-durum", C("ölçüm başarısız"));
        return;
    }
    kapasiteCiz();
    saklamaIpucu();
}
function kapasiteCiz() {
    if (!KAP)
        return;
    const a = KAP.analiz, q = KAP.kota || {};
    const goster = (id, g) => { el(id).style.display = g ? "" : "none"; };
    if (!a.ok) {
        setHtml("kap-durum", '<span class="kap-hata">⚠ ' + esc(a.hata || C("ölçülemedi")) + "</span>");
        ["kap-bar", "kap-alt", "kap-btn"].forEach((i) => goster(i, false));
        setHtml("kap-misafir", "");
        return;
    }
    const gunluk = a.gunluk || 0;
    const gun = Number(val("e-kd")) || 0, cop = Number(val("e-td")) || 0;
    const gereken = gunluk * (gun + cop);
    const toplam = Number(q.total) || 0, kullanilan = Number(q.used) || 0, bos = Number(q.free) || 0;
    const sonraPct = toplam ? ((kullanilan + gereken) / toplam) * 100 : 0;
    const mevcutPct = toplam ? (kullanilan / toplam) * 100 : 0;
    const sigar = gereken < bos;
    setHtml("kap-durum", C("Ölçüldü: günde <b>") + hb(gunluk) + "</b> üretiliyor ("
        + (a.set_sayisi || 0) + C(" günlük set, toplam ") + hb(a.toplam) + ").<br>"
        + "<b>" + gun + C(" gün</b> saklama + <b>") + cop + C(" gün</b> çöp → Drive'da <b>") + hb(gereken)
        + "</b> gerekir.");
    goster("kap-bar", true);
    goster("kap-alt", true);
    goster("kap-btn", true);
    const bar = el("kap-bar");
    bar.className = "kap-bar" + (!sigar ? " tasma" : (sonraPct >= 80 ? " uyari" : ""));
    el("kap-mevcut").style.width = Math.min(100, mevcutPct) + "%";
    el("kap-yeni").style.width = Math.min(100 - Math.min(100, mevcutPct), toplam ? (gereken / toplam) * 100 : 0) + "%";
    setHtml("kap-alt", "<span>şu an dolu: " + hb(kullanilan) + "</span>"
        + "<span>bu planla: <b>%" + sonraPct.toFixed(1) + "</b></span>"
        + "<span>hesap: " + hb(toplam) + "</span>");
    let uyari = "";
    if (KAP.kota && KAP.kota.bekliyor) {
        uyari = C("Kota ölçülüyor, birkaç saniye sonra tekrar bak.");
    }
    else if (!toplam) {
        uyari = C("Kota okunamadı — doluluk hesaplanamıyor. Gereken alan yine de doğru.");
    }
    else if (!sigar) {
        uyari = C("⚠ Bu süre hesaba <b>sığmaz</b>: ") + hb(gereken) + " gerekiyor, " + hb(bos) + C(" boş var.");
    }
    else if (sonraPct >= 85) {
        uyari = "⚠ Hesap %" + sonraPct.toFixed(0) + C(" dolar. VM/CT'ler büyürse yer biter.");
    }
    if (KAP.oneri) {
        // "Onerilen" demek yaniltiyordu: bu en iyi secim degil, bos alana guvenle
        // sigabilecek en uzun sure. Kisa sure daha az yer kaplar.
        uyari += (uyari ? "<br>" : "")
            + C("Sığabilecek en uzun süre: <b>") + KAP.oneri + C(" gün</b> (boş alanın %")
            + (KAP.oneri_pay_pct || 60) + C("'ini kullanır). Bu bir tavsiye değil, üst sınır.")
            + "<br>" + C("Kısa süre daha az yer kaplar; uzun süre geç fark edilen bir soruna karşı daha geniş geri dönüş penceresi verir.");
    }
    const ilk = "<br>İlk yükleme <b>" + hb(a.toplam) + "</b> olur (kaynakta " + (a.set_sayisi || 0)
        + C(" set var); hedef doluluğa ancak ") + gun + C(" gün sonra ulaşılır.");
    setHtml("kap-misafir", (uyari ? '<div class="kap-uyari">' + uyari + ilk + "</div>" : '<div class="kap-uyari">' + ilk.slice(4) + "</div>")
        + "<table><tbody>" + (a.misafirler || []).map((m) => "<tr><td>" + esc(m.ad) + "</td><td>set başına " + hb(m.set_basina)
        + " · %" + m.pay + "</td></tr>").join("") + "</tbody></table>");
}
function kapasiteOner() {
    if (!KAP || !KAP.oneri)
        return;
    setVal("e-kd", KAP.oneri);
    good("e-kd");
    markDirty();
    kapasiteCiz();
    flash(KAP.oneri + C(" gün uygulandı"), true);
}
/* ---------- klasor gezgini ---------- */
async function loadStorages() {
    try {
        const j = await api("/api/storages");
        const s = j.storages || [];
        setHtml("e-stor", s.length ? C("Proxmox depoları: ") + s.map((x) => "<a href=\"#\" onclick=\"setSrc('" + x.path + "');return false\" style=\"color:#58a6ff\">"
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
    let h = j.parent ? "<div onclick=\"goDir('" + j.parent + C("')\"><span>⬆ üst klasör</span><span></span></div>") : "";
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
    setTxt("e-accthint", REM.length ? REM.length + C(" hesap tanımlı") : C("Henüz hesap yok — 'Yönet' ile ekle."));
}
let SAG = [];
async function saglayicilariYukle() {
    try {
        const j = await api("/api/saglayicilar");
        SAG = (j.saglayicilar || []).filter((x) => x.kurulu);
    }
    catch {
        SAG = [];
    }
    setHtml("a-tur", SAG.map((x) => '<option value="' + esc(x.tur) + '">'
        + esc(x.simge + " " + x.ad) + (x.dogrulandi ? "" : C("  (denenmedi)"))
        + "</option>").join(""));
    saglayiciDegisti();
}
function saglayiciDegisti() {
    const x = SAG.filter((y) => y.tur === val("a-tur"))[0];
    const e = document.getElementById("a-turhint");
    if (!e)
        return;
    if (!x) {
        e.textContent = "";
        return;
    }
    // Neyin gercekten denendigini sakla: "calisiyor gibi duruyor" demek yaniltir.
    e.innerHTML = (x.not ? esc(C(x.not)) + " " : "")
        + (x.dogrulandi
            ? '<b style="color:#7ee2a8">' + esc(C("Gerçek hesapla uçtan uca doğrulandı.")) + "</b>"
            : '<b style="color:#ffd479">' + esc(C("OAuth akışı çalışıyor ama yükleme/saklama "
                + "davranışı gerçek hesapla denenmedi. Önce küçük bir planla dene.")) + "</b>");
}
function openAccounts() {
    openM("m-acct");
    hesapPaneliTasi("hesap-ekle-yuvasi", true);
    acctTab(1);
    void saglayicilariYukle();
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
        const line = q.ok ? hb(q.used) + " / " + hb(q.total) + C("  ·  çöp ") + hb(q.trashed || 0)
            + C("  ·  boş ") + hb(q.free || 0) : "⚠ " + esc(q.error || C("kota okunamadı"));
        return '<div class="card" data-hesap="' + esc(r.name) + '" style="margin-bottom:8px"><div style="display:flex;'
            + 'justify-content:space-between;gap:8px;align-items:center;flex-wrap:wrap">'
            + "<b>" + esc(r.name) + '</b> <span class="small">' + esc(r.type) + "</span>"
            + '<span style="flex:1"></span>'
            + "<button class=\"sm\" onclick=\"acctTest('" + r.name + "')\">Test</button>"
            + "<button class=\"sm warn\" onclick=\"acctDel('" + r.name + "')\">" + C("Sil")
            + "</button></div>"
            + '<div class="small" style="margin-top:6px">' + line + "</div></div>";
    }).join("") : '<div class="small">Henüz hesap yok.</div>');
}
async function acctTest(n) {
    flash("kontrol ediliyor…", true);
    const j = await api("/api/remote/test?name=" + encodeURIComponent(n), { method: "POST" });
    flash(j.msg || "", j.ok);
}
async function acctDel(n) {
    if (!await onay("'" + n + C("' kaldırılsın mı? Drive'daki dosyalara dokunulmaz.")))
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
        bad("a-token", C("geçerli JSON değil"));
        return;
    }
    good("a-token");
    const j = await api("/api/remote/add", { method: "POST",
        body: JSON.stringify({ name: val("a-name"), token: val("a-token"),
            tur: val("a-tur") || "drive" }) });
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
        bad("a-name", C("önce geçerli bir hesap adı yaz"));
        return;
    }
    good("a-name");
    const j = await api("/api/remote/auth/start", { method: "POST",
        body: JSON.stringify({ tur: val("a-tur") || "drive" }) });
    setVal("a-tunnel", j.tunnel || "");
    if (!j.ok) {
        flash(j.msg || C("başlatılamadı"), false);
        return;
    }
    el("a-authbox").style.display = "";
    setTxt("a-url", j.url || "");
    flash(C("adresi tarayıcında aç"), true);
    pollAuth();
}
function pollAuth() {
    window.clearInterval(authTimer);
    authTimer = window.setInterval(() => {
        void (async () => {
            const st = await api("/api/remote/auth/status");
            if (st.ready) {
                window.clearInterval(authTimer);
                setTxt("a-wait", C("jeton alındı, hesap oluşturuluyor…"));
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
                setTxt("a-wait", C("yetkilendirme sonlandı"));
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
        hint: C("Gmail hesap şifresi çalışmaz. Google Hesabı → Güvenlik → 2 Adımlı Doğrulama → Uygulama şifreleri'nden 16 haneli şifre üret.") },
    outlook: { host: "smtp.office365.com", port: 587, security: "starttls",
        hint: C("Microsoft 365 / Outlook. Kurumsal hesaplarda SMTP AUTH kapalı olabilir, yöneticiden açtırman gerekebilir.") },
    yandex: { host: "smtp.yandex.com", port: 465, security: "ssl",
        hint: C("Yandex'te 'Uygulama şifreleri' bölümünden şifre üret. Kullanıcı adı tam adres olmalı.") },
    yahoo: { host: "smtp.mail.yahoo.com", port: 465, security: "ssl",
        hint: C("Yahoo'da uygulama şifresi zorunlu, normal şifre kabul edilmez.") },
    custom: { host: "", port: 587, security: "starttls",
        hint: C("Sunucu, port ve güvenlik ayarını sağlayıcından öğren.") },
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
    setTxt("e-smtphint", SMTP.length ? "" : C("Mail profili yok — '✉ Yönet' ile ekle, yoksa mail gitmez."));
}
function renderSmtp() {
    SMTP = (S && S.smtp) || [];
    setHtml("s-list", SMTP.length ? SMTP.map((x) => '<div class="card" data-smtp="' + esc(x.id) + '" style="margin-bottom:8px"><div style="display:flex;justify-content:space-between;'
        + 'gap:8px;align-items:center;flex-wrap:wrap"><b>' + esc(x.name) + "</b>"
        + '<span style="flex:1"></span>'
        + "<button class=\"sm\" onclick=\"smtpEdit('" + x.id + "')\">" + C("Düzenle") + "</button>"
        + "<button class=\"sm\" onclick=\"smtpTest('" + x.id + "')\">Test maili</button>"
        + "<button class=\"sm warn\" onclick=\"smtpDel('" + x.id + "')\">" + C("Sil")
        + "</button></div>"
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
    setTxt("s-formtitle", C("Düzenle: ") + x.name);
}
async function smtpSave() {
    let ok = true;
    ok = vTxt("s-name", C("profil adı gerekli")) && ok;
    ok = vRx("s-host", RX.host, C("geçerli bir sunucu adı yaz")) && ok;
    ok = vNum("s-port", 1, 65535, C("1-65535 arası port")) && ok;
    ok = vMails("s-user", true) && ok;
    ok = vMails("s-from", true) && ok;
    if (!ok) {
        flash(C("form hatalı"), false);
        return;
    }
    const b = {
        id: val("s-id"), name: val("s-name"), host: val("s-host"), port: Number(val("s-port")),
        security: val("s-sec"), user: val("s-user"), from: val("s-from"),
    };
    if (val("s-pass"))
        b.pass = val("s-pass");
    if (!val("s-id") && !val("s-pass")) {
        flash(C("yeni profil için şifre gerekli"), false);
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
    if (!await onay(C("Profil silinsin mi?")))
        return;
    const j = await api("/api/smtp/delete?id=" + encodeURIComponent(id), { method: "POST" });
    flash(j.msg || "", j.ok);
    await refresh();
    renderSmtp();
    loadSmtpSelect();
}
async function smtpTest(id) {
    const to = await sorMetin(C("Test maili hangi adrese gitsin?\n(boş bırakırsan gönderen adresine gider)"), "");
    if (to === null)
        return;
    flash(C("gönderiliyor…"), true);
    const j = await api("/api/smtp/test?id=" + encodeURIComponent(id) + "&to=" + encodeURIComponent(to), { method: "POST" });
    flash(j.msg || "", j.ok);
}
/* ---------- genel ayarlar ---------- */
/** Telegram testi. Jeton kaydedilmemisse once kaydetmesi gerektigini soyle. */
async function tgTest() {
    const e = document.getElementById("g-tgdurum");
    const yaz = (t, iyi) => {
        if (e) {
            e.textContent = t;
            e.className = "small" + (iyi ? "" : " uyari-metin");
        }
    };
    const jetonVar = Boolean(S && S.telegram_jeton_var) || Boolean(val("g-tgtoken").trim());
    if (!jetonVar) {
        yaz(C("önce bot jetonunu gir ve kaydet"), false);
        return;
    }
    if (val("g-tgtoken").trim()) {
        yaz(C("önce Kaydet'e bas, sonra test et"), false);
        return;
    }
    yaz(C("gönderiliyor…"), true);
    const j = await api("/api/telegram/test", { method: "POST",
        body: JSON.stringify({ chat: val("g-tgchat").trim() }) });
    yaz(j.msg || "", j.ok);
    flash(j.msg || "", j.ok);
}
/* ---------- bakim: ayar tasima, Proxmox linki, oturumlar ---------- */
/** Tarayici indirmesi: sunucu Content-Disposition ile dosya adini verir. */
function ayarIndir(sirlarla) {
    if (sirlarla) {
        void onay(C("İndirilecek dosya SMTP şifrelerini düz metin içerecek.\n"
            + "Yalnızca güvendiğin bir yere kaydet."), C("Şifrelerle indir"), C("İndir"), C("Vazgeç")).then((e) => {
            if (e)
                window.location.href = "/api/disa-aktar?sirlar=1";
        });
        return;
    }
    window.location.href = "/api/disa-aktar";
}
function ayarYukleAc() { el("s-dosya").click(); }
async function ayarYukle(dosya) {
    let veri;
    try {
        veri = JSON.parse(await dosya.text());
    }
    catch {
        flash(C("dosya geçerli JSON değil"), false);
        return;
    }
    const d = veri;
    const np = (d.plans || []).length, ns = (d.smtp_profiles || []).length;
    const kip = await onay(C("Dosyada ") + np + C(" plan, ") + ns + C(" mail profili var")
        + (d._surum ? C(" (sürüm ") + esc(d._surum) + ")" : "") + ".\n"
        + C("Mevcut planların korunsun mu, yoksa yerlerine bunlar mı geçsin?"), C("Ayar yükle"), C("Ekle (mevcutlar kalsın)"), C("Vazgeç"));
    if (!kip)
        return;
    const j = await api("/api/ice-aktar", { method: "POST",
        body: JSON.stringify({ veri, kip: "ekle" }) });
    flash(j.msg || "", j.ok);
    if (j.ok) {
        void refresh();
        renderSmtp();
    }
}
async function pveLinkDurum() {
    const e = document.getElementById("s-pvelink");
    if (!e)
        return;
    try {
        const j = await api("/api/proxmox-link");
        e.innerHTML = !j.ok ? esc(C("Durum okunamadı: ") + (j.msg || ""))
            : j.var ? "✅ " + esc(C("Link ekli: ")) + "<code>" + esc(j.url) + "</code>"
                : "○ " + esc(C("Link yok. Eklenecek adres: ")) + "<code>" + esc(j.url) + "</code>";
    }
    catch {
        e.textContent = C("durum okunamadı");
    }
}
async function pveLink(ekle) {
    const j = await api("/api/proxmox-link", { method: "POST",
        body: JSON.stringify({ ekle }) });
    flash(j.msg || "", j.ok);
    void pveLinkDurum();
}
async function oturumlariYukle() {
    const kutu = document.getElementById("s-oturumlar");
    if (!kutu)
        return;
    try {
        const j = await api("/api/oturumlar");
        const o = j.oturumlar || [];
        kutu.innerHTML = !o.length
            ? '<div class="small">' + C("Hatırlanan açık oturum yok.") + "</div>"
            : '<table><thead><tr><th>' + C("Cihaz") + "</th><th>" + C("Adres")
                + "</th><th>" + C("Açılış") + '</th><th class="r">' + C("Kalan")
                + "</th><th></th></tr></thead><tbody>"
                + o.map((x) => "<tr><td>" + (x.bu_mu ? "<b>" + C("bu tarayıcı") + "</b>"
                    : '<code class="small">' + esc(x.onek) + "…</code>")
                    + "</td><td>" + esc(x.adres) + "</td><td>" + esc(x.olusma)
                    + '</td><td class="r">' + x.kalan_gun + C(" gün") + "</td><td>"
                    + (x.bu_mu ? "" : '<button class="sm warn" onclick="oturumKapat(\''
                        + esc(x.onek) + "')\">" + C("Kapat") + "</button>")
                    + "</td></tr>").join("") + "</tbody></table>"
                + '<div class="small" style="margin-top:6px">'
                + C("Hatırlama: ") + (j.ayarlar.remember_enabled ? C("açık") : C("kapalı"))
                + " · " + esc(String(j.ayarlar.remember_days)) + C(" gün")
                + " · " + C("adres bağlama: ") + esc(String(j.ayarlar.session_ip_bind || "ip"))
                + " · SameSite: " + esc(String(j.ayarlar.cookie_samesite || "Lax")) + "</div>";
        ceviriUygula();
    }
    catch {
        kutu.textContent = C("okunamadı");
    }
}
async function oturumKapat(onek, hepsi) {
    if (hepsi && !await onay(C("Bu tarayıcı dışındaki tüm hatırlanan oturumlar kapatılsın mı?"), C("Oturumlar"), C("Kapat"), C("Vazgeç")))
        return;
    const j = await api("/api/oturum/kapat", { method: "POST",
        body: JSON.stringify(hepsi ? { hepsi: true } : { onek }) });
    flash(j.msg || "", j.ok);
    void oturumlariYukle();
}
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
            + "</b> · veren: " + esc(c ? c.veren : "-") + C(" · bitiş: ") + esc(c ? c.bitis : "-")
        : '<span style="color:#ff9b9b">⚠ TLS kapalı</span> — arayüz düz HTTP çalışıyor.');
    setChk("g-tg", Boolean(s.telegram_enabled));
    setVal("g-tgchat", String(s.telegram_chat_id || ""));
    setVal("g-tgtoken", "");
    setTxt("g-tgdurum", S && S.telegram_jeton_var ? C("jeton kayıtlı") : C("jeton girilmemiş"));
    void pveLinkDurum();
    void oturumlariYukle();
    const df = el("s-dosya");
    df.onchange = () => { if (df.files && df.files[0])
        void ayarYukle(df.files[0]); df.value = ""; };
    openM("m-set");
}
async function saveSettings() {
    let ok = true;
    ok = vRx("g-bind", RX.ip, C("IP adresi yaz (0.0.0.0 veya 127.0.0.1)")) && ok;
    ok = vNum("g-port", 1, 65535, "1-65535") && ok;
    ok = vTxt("g-user", C("kullanıcı adı gerekli")) && ok;
    ok = vNum("g-refresh", 1, 3600, C("1-3600 sn")) && ok;
    ok = vNum("g-hist", 1, 1000, "1-1000") && ok;
    ok = vNum("g-logn", 10, 5000, "10-5000") && ok;
    ok = vNum("g-tail", 1, 1000, "1-1000") && ok;
    ok = vNum("g-rows", 1, 10000, "1-10000") && ok;
    ok = vNum("g-logmb", 0, 1000, "0-1000 MB") && ok;
    ok = vNum("g-logkeep", 1, 20, "1-20") && ok;
    ok = vNum("g-tmo", 0, 1440, C("0-1440 dk")) && ok;
    ok = vTxt("g-re", C("kalıp boş olamaz")) && ok;
    const netler = val("g-nets").split(",").map((x) => x.trim()).filter(Boolean);
    const kotuNet = netler.filter((x) => !/^\d{1,3}(\.\d{1,3}){3}(\/\d{1,2})?$/.test(x)
        && !/^[0-9a-fA-F:]+(\/\d{1,3})?$/.test(x));
    if (kotuNet.length)
        ok = bad("g-nets", C("geçersiz ağ: ") + kotuNet[0]) && ok;
    else
        good("g-nets");
    if (!ok) {
        flash(C("form hatalı"), false);
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
    b.telegram_enabled = chk("g-tg");
    b.telegram_chat_id = val("g-tgchat").trim();
    // Jeton yalnizca YENI girildiyse gonderilir; bos ise sunucudaki korunur
    if (val("g-tgtoken").trim())
        b.telegram_token = val("g-tgtoken").trim();
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
    let h = C("Kurulu sürüm: <b>v") + esc(v) + "</b>";
    if (g && g.hata)
        h += ' · <span style="color:#ff9b9b">kontrol hatası: ' + esc(g.hata) + "</span>";
    else if (g && g.yeni_var)
        h += ' · <span style="color:#ffd479">yeni sürüm hazır: <b>v'
            + esc(g.uzak || "") + "</b></span>";
    else if (g && g.uzak)
        h += C(" · güncel");
    setHtml("g-guncel", h);
    el("g-upbtn").style.display = g && g.yeni_var ? "" : "none";
}
async function upKontrol() {
    flash("kontrol ediliyor…", true);
    const j = await api("/api/update/check?force=1");
    await refresh();
    upDurum();
    flash(j.hata ? C("hata: ") + j.hata
        : (j.yeni_var ? C("yeni sürüm var: v") + j.uzak : C("güncel: v") + j.surum), !j.hata);
}
async function upKur() {
    if (!await onay(C("Güncelleme kurulacak.\n\nPlanların ve ayarların korunur, ikisinin de yedeği alınır.\n")
        + C("Arayüz birkaç saniye yeniden başlar. Devam edilsin mi?")))
        return;
    flash(C("indiriliyor ve doğrulanıyor…"), true);
    const j = await api("/api/update/apply", { method: "POST" });
    flash(j.msg || "", j.ok);
    if (j.ok)
        window.setTimeout(() => location.reload(), 6000);
}
async function upGeri() {
    if (!await onay(C("Önceki sürüme dönülecek. Devam edilsin mi?")))
        return;
    const j = await api("/api/update/rollback", { method: "POST" });
    flash(j.msg || "", j.ok);
    if (j.ok)
        window.setTimeout(() => location.reload(), 6000);
}
/* ---------- baslangic ---------- */
/** Kaydedilmemis degisiklik varsa uygulama ici onay sorar. */
async function kapatmayiDene(m) {
    if (m.id === "m-edit" && dirty
        && !await onay(C("Kaydedilmemiş değişiklikler var, kapatılsın mı?")))
        return;
    m.classList.remove("show");
    if (m.id === "m-edit")
        dirty = false;
}
Array.prototype.slice.call(document.querySelectorAll(".mask")).forEach((m) => {
    m.addEventListener("click", (e) => {
        if (e.target !== m || m.id === "m-onay")
            return; // onay penceresi disi tiklama kapatmaz
        void kapatmayiDene(m);
    });
});
document.addEventListener("keydown", (e) => {
    const acik = Array.prototype.slice.call(document.querySelectorAll(".mask.show"));
    if (acik.some((m) => m.id === "m-onay")) {
        if (e.key === "Enter")
            onayKapat(true);
        else if (e.key === "Escape")
            onayKapat(false);
        return;
    }
    if (e.key !== "Escape")
        return;
    acik.forEach((m) => void kapatmayiDene(m));
});
/* ---------- sag tik menuleri ----------
 * Kayitlar bir kez yapilir; listeler yenilendiginde tekrar baglamak gerekmez
 * cunku dinleyici document uzerinde ve secici ile eslesiyor (bkz. menu.ts). */
function planBul(id) {
    return S ? S.plans.filter((p) => p.id === id)[0] : undefined;
}
/** Planin yalnizca etkin bayragini degistirir. save_plan ad ve hedefi zorunlu
 *  gordugu icin onlari da yolluyoruz; gerisi sunucuda korunur. */
async function planDurumDegistir(p) {
    const j = await api("/api/plan/save", { method: "POST", body: JSON.stringify({ id: p.id, name: p.name, remote: p.remote, enabled: !p.enabled }) });
    flash(j.ok ? (p.enabled ? C("plan duraklatıldı") : C("plan etkinleştirildi")) : (j.msg || ""), j.ok);
    void refresh();
}
/** Mevcut plandan kopya: sihirbaz yerine dolu formu acar, id bos kalir. */
function planKopyala(p) {
    openEditor(null);
    wSihirbaz = false;
    wGoster();
    alanlariDoldur(p);
    const i = p.remote.indexOf(":");
    setVal("e-acct", p.remote.slice(0, i));
    setVal("e-folder", p.remote.slice(i + 1));
    setVal("e-name", p.name + C(" (kopya)"));
    setChk("e-enabled", false); // kopya kapali baslar, once gozden gecirilsin
    Array.prototype.slice.call(el("e-wd").querySelectorAll("input")).forEach((c) => {
        c.checked = (p.weekdays || []).indexOf(Number(c.value)) >= 0;
    });
    ramHint();
    saklamaIpucu();
    markDirty();
    flash(C("kopya hazır — gözden geçirip kaydet"), true);
}
function planMenusu(kap) {
    const p = planBul(kap.getAttribute("data-plan") || "");
    if (!p)
        return [];
    const aliciVar = Boolean(p.report_mail_to || p.mail_to);
    return [
        { baslik: true, etiket: p.name },
        { simge: "▶", etiket: "Yedeklemeyi başlat", pasif: p.running,
            ipucu: p.running ? "bu plan zaten çalışıyor" : "", is: () => act("backup", p.id) },
        { simge: p.enabled ? "⏸" : "✅", etiket: p.enabled ? "Planı duraklat" : "Planı etkinleştir",
            ipucu: p.enabled ? "zamanlama durur, dosyalara dokunulmaz" : "",
            is: () => planDurumDegistir(p) },
        { ayrac: true },
        { simge: "🧹", etiket: "Retention'ı şimdi çalıştır", pasif: p.running,
            ipucu: "süresi dolan setleri Drive çöpüne taşır", is: () => act("prune", p.id) },
        { simge: "🗑", etiket: "Çöpü boşalt", pasif: p.running,
            ipucu: "çöpte süresi dolmuş dosyaları kalıcı siler", is: () => act("purgetrash", p.id) },
        { simge: "↻", etiket: "Drive durumunu tazele", pasif: p.running,
            is: () => act("refresh", p.id) },
        { ayrac: true },
        { simge: "📊", etiket: "Haftalık raporu şimdi gönder", pasif: !aliciVar || !p.weekly_report,
            ipucu: !p.weekly_report ? "bu planda rapor kapalı"
                : !aliciVar ? "önce plana mail adresi gir" : "", is: () => act("report", p.id) },
        { simge: "✉", etiket: "Test maili gönder", pasif: !p.mail_to,
            ipucu: p.mail_to ? "" : "önce plana mail adresi gir", is: () => act("testmail", p.id) },
        { ayrac: true },
        { simge: "✎", etiket: "Düzenle", is: () => openEditor(p.id) },
        { simge: "⧉", etiket: "Kopyasını oluştur", is: () => planKopyala(p) },
        { simge: "⬇", etiket: "Planı JSON olarak indir",
            ipucu: "başka sunucuya taşımak için", is: () => dosyaIndir("plan-" + p.id + ".json", JSON.stringify(p, null, 2), "application/json") },
        { simge: "📋", etiket: "Kaynak klasörü kopyala", is: () => panoyaYaz(p.src_dir, C("kaynak")) },
        { simge: "📋", etiket: "Hedefi kopyala", is: () => panoyaYaz(p.remote, C("hedef")) },
        { simge: "📄", etiket: "Bu planın loglarını göster", is: () => setLog(p.id) },
        { simge: "🧩", etiket: "Yapılandırmayı şimdi yedekle",
            pasif: p.running || !p.host_config_enabled,
            ipucu: p.host_config_enabled ? "/etc/pve, ağ ve depo tanımları — ~25 KB"
                : "bu planda yapılandırma yedeği kapalı",
            is: () => act("hostconf", p.id) },
        { ayrac: true },
        { simge: "🗑", etiket: "Planı sil", tehlike: true, is: () => delPlan(p.id) },
    ];
}
function hesapMenusu(kap) {
    const ad = kap.getAttribute("data-hesap") || "";
    const r = REM.filter((x) => x.name === ad)[0];
    const q = (r && r.quota) || {};
    return [
        { baslik: true, etiket: ad },
        { simge: "🔌", etiket: "Bağlantıyı test et", is: () => acctTest(ad) },
        { simge: "↻", etiket: "Kotayı yenile", is: () => renderAccounts() },
        { simge: "📋", etiket: "Hesap adını kopyala", is: () => panoyaYaz(ad, C("hesap adı")) },
        { simge: "📋", etiket: "Kota bilgisini kopyala", pasif: !q.ok,
            is: () => panoyaYaz(ad + ": " + hb(q.used || 0) + " / " + hb(q.total || 0)) },
        { ayrac: true },
        { simge: "🗑", etiket: "Hesabı kaldır", tehlike: true,
            ipucu: "Drive'daki dosyalara dokunulmaz", is: () => acctDel(ad) },
    ];
}
function smtpMenusu(kap) {
    const id = kap.getAttribute("data-smtp") || "";
    const x = SMTP.filter((y) => y.id === id)[0];
    if (!x)
        return [];
    return [
        { baslik: true, etiket: x.name },
        { simge: "✎", etiket: "Düzenle", is: () => smtpEdit(id) },
        { simge: "✉", etiket: "Test maili gönder", is: () => smtpTest(id) },
        { simge: "📋", etiket: "Sunucu adresini kopyala",
            is: () => panoyaYaz(x.host + ":" + x.port, C("sunucu")) },
        { ayrac: true },
        { simge: "🗑", etiket: "Profili sil", tehlike: true, is: () => smtpDel(id) },
    ];
}
/** Imlecin durdugu log satirini dondurur. Tarayici caret API'si varsa metin
 *  ofsetinden kesin bulunur; yoksa satir yuksekligiyle tahmin edilir. */
function logSatiriBul(tum, olay) {
    const d = document;
    let ofset = -1;
    try {
        if (d.caretPositionFromPoint) {
            const k = d.caretPositionFromPoint(olay.clientX, olay.clientY);
            if (k)
                ofset = k.offset;
        }
        else if (d.caretRangeFromPoint) {
            const r = d.caretRangeFromPoint(olay.clientX, olay.clientY);
            if (r)
                ofset = r.startOffset;
        }
    }
    catch { /* desteklenmiyorsa tahmine duseriz */ }
    if (ofset >= 0 && ofset <= tum.length) {
        const bas = tum.lastIndexOf("\n", Math.max(0, ofset - 1)) + 1;
        const son = tum.indexOf("\n", ofset);
        return tum.slice(bas, son < 0 ? tum.length : son);
    }
    const kutu = el("log");
    const g = kutu.getBoundingClientRect();
    const sy = parseFloat(getComputedStyle(kutu).lineHeight) || 16;
    const satirlar = tum.split("\n");
    const i = Math.floor((olay.clientY - g.top + kutu.scrollTop) / sy);
    return satirlar[Math.max(0, Math.min(satirlar.length - 1, i))] || "";
}
function logMenusu(_kap, olay) {
    const tum = el("log").textContent || "";
    const satir = logSatiriBul(tum, olay);
    const kaynak = LOGSRC === "all" ? C("tümü") : LOGSRC === "system" ? C("sistem") : LOGSRC;
    return [
        { baslik: true, etiket: C("Log — ") + kaynak },
        { simge: "📋", etiket: "Bu satırı kopyala", pasif: !satir.trim(),
            ipucu: satir.slice(0, 90), is: () => panoyaYaz(satir, C("satır")) },
        { simge: "📋", etiket: "Görünen logu kopyala", is: () => panoyaYaz(tum) },
        { simge: "⬇", etiket: "Log dosyası olarak indir",
            is: () => dosyaIndir("pve-gdrive-" + LOGSRC + ".log", tum) },
        { ayrac: true },
        { simge: "↻", etiket: "Yenile", is: () => loadLog() },
        { simge: "📄", etiket: "Sistem loglarına geç", pasif: LOGSRC === "system",
            is: () => setLog("system") },
        { simge: "📚", etiket: "Tüm logları göster", pasif: LOGSRC === "all", is: () => setLog("all") },
    ];
}
/** Yedek ve cop tablolarindaki satirlar: dosya adiyla ugrasmak icin. */
function satirMenusu(tr) {
    const hucreler = Array.prototype.slice.call(tr.querySelectorAll("td"));
    if (!hucreler.length)
        return null;
    const metinler = hucreler.map((td) => (td.textContent || "").trim());
    // En uzun hucre dosya adidir (vzdump-qemu-100-...zst)
    const dosya = metinler.filter((m) => m.indexOf("vzdump") === 0)[0] || "";
    return [
        { simge: "📋", etiket: "Dosya adını kopyala", pasif: !dosya,
            ipucu: dosya, is: () => panoyaYaz(dosya, C("dosya adı")) },
        { simge: "📋", etiket: "Satırı kopyala", is: () => panoyaYaz(metinler.join("  ")) },
        { simge: "📋", etiket: "Tabloyu kopyala", is: () => {
                const govde = tr.closest("table");
                const s2 = Array.prototype.slice.call(govde ? govde.querySelectorAll("tr") : [])
                    .map((r) => Array.prototype.slice.call(r.querySelectorAll("th,td"))
                    .map((c) => (c.textContent || "").trim()).join("\t")).join("\n");
                void panoyaYaz(s2, C("tablo"));
            } },
    ];
}
function genelMenu() {
    return [
        { simge: "➕", etiket: "Yeni plan", is: () => openEditor(null) },
        { simge: "↻", etiket: "Şimdi yenile", is: () => refresh() },
        { ayrac: true },
        { simge: "👤", etiket: "Google hesapları", is: () => openAccounts() },
        { simge: "✉", etiket: "SMTP profilleri", is: () => openSmtp() },
        { simge: "⚙", etiket: "Ayarlar", is: () => openSettings() },
    ];
}
function menuleriTanimla() {
    sagTik("[data-plan]", planMenusu);
    sagTik("[data-hesap]", hesapMenusu);
    sagTik("[data-smtp]", smtpMenusu);
    sagTik("#log", logMenusu);
    sagTik(".panel table tbody tr", satirMenusu);
    sagTik("#plans, .wrap > header, #detail", genelMenu);
}
dilBaslat();
surukleKur();
menuKur();
menuleriTanimla();
void refresh().then(taslakSor);
/**
 * Plan formu alan tablosu.
 *
 * Onceden openEditor / savePlan / validatePlan ayni alan listesini uc kez, uc farkli
 * bicimde tekrarliyordu; yeni bir alan eklerken uc yeri birden duzenlemek gerekiyordu.
 * Artik tek kaynak burasi: doldur(), topla() ve dogrula() bu tablodan turer.
 */
const PLAN_ALANLARI = [
    // 1. Plan
    { id: "e-name", anahtar: "name", tip: "metin", adim: 1, mesaj: C("plan adı gerekli") },
    { id: "e-enabled", anahtar: "enabled", tip: "onay", adim: 0 },
    // 2. Kaynak
    { id: "e-src", anahtar: "src_dir", tip: "metin", adim: 2, mesaj: C("kaynak klasör gerekli") },
    // 3. Hedef — remote iki alandan birlesir, ozel islenir (e-acct + e-folder)
    // 4. Saklama
    { id: "e-kd", anahtar: "keep_days", tip: "sayi", adim: 4, min: 0, max: 3650, mesaj: C("0-3650 arası gün") },
    { id: "e-kc", anahtar: "keep_count", tip: "sayi", adim: 4, min: 0, max: 999, mesaj: C("0-999 arası adet") },
    { id: "e-td", anahtar: "drive_trash_days", tip: "sayi", adim: 4, min: 0, max: 365, mesaj: C("0-365 arası gün") },
    { id: "e-hc", anahtar: "host_config_enabled", tip: "onay", adim: 0 },
    { id: "e-hcj", anahtar: "host_config_json", tip: "onay", adim: 0 },
    { id: "e-hck", anahtar: "host_config_keep_count", tip: "sayi", adim: 4, min: 0, max: 999,
        mesaj: C("0-999 arası adet") },
    // 5. Zamanlama ve cakisma
    { id: "e-runat", anahtar: "run_at", tip: "saat", adim: 5, mesaj: C("SS:DD biçiminde saat (ör. 03:00)") },
    { id: "e-wv", anahtar: "wait_for_vzdump", tip: "onay", adim: 0 },
    { id: "e-wvm", anahtar: "vzdump_wait_min", tip: "sayi", adim: 5, min: 0, max: 1440, mesaj: C("0-1440 dakika") },
    { id: "e-mage", anahtar: "min_age_min", tip: "sayi", adim: 5, min: 0, max: 1440, mesaj: C("0-1440 dakika") },
    { id: "e-skip", anahtar: "skip_patterns", tip: "liste", adim: 0 },
    { id: "e-pof", anahtar: "prune_on_failure", tip: "onay", adim: 0 },
    // 6. Aktarim
    { id: "e-bw", anahtar: "bwlimit", tip: "metin", adim: 6, rx: RX.bw, ops: true, mesaj: C("ör. 30M, 2M veya off") },
    { id: "e-tr", anahtar: "transfers", tip: "sayi", adim: 6, min: 1, max: 64, mesaj: C("1-64 arası") },
    { id: "e-ck", anahtar: "checkers", tip: "sayi", adim: 6, min: 1, max: 64, mesaj: C("1-64 arası") },
    { id: "e-chunk", anahtar: "drive_chunk", tip: "metin", adim: 6, rx: RX.chunk, mesaj: C("ör. 64M, 128M, 8M") },
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
        kosul: () => chk("e-wr"), mesaj: C("SS:DD biçiminde saat") },
    { id: "e-rdays", anahtar: "report_days", tip: "sayi", adim: 7, min: 1, max: 365, vars: 7,
        kosul: () => chk("e-wr"), mesaj: C("1-365 gün") },
    { id: "e-rstale", anahtar: "report_stale_days", tip: "sayi", adim: 7, min: 0, max: 365, vars: 2,
        kosul: () => chk("e-wr"), mesaj: C("0-365 gün") },
    { id: "e-rquota", anahtar: "report_quota_warn", tip: "sayi", adim: 7, min: 0, max: 100, vars: 90,
        kosul: () => chk("e-wr"), mesaj: C("0-100 arası yüzde") },
    // Bant genisligi cizelgesi ve otomatik mod: yalnizca ilgiliyken dogrulanir
    { id: "e-bwsch", anahtar: "bwlimit_schedule", tip: "metin", adim: 6, ops: true, vars: "",
        ozelDogrula: (id) => vBwSched(id) },
    { id: "e-bwlmode", anahtar: "bw_auto_link_mode", tip: "metin", adim: 0, vars: "ogren" },
    { id: "e-bwlink", anahtar: "bw_auto_link", tip: "metin", adim: 6, rx: RX.bw, vars: "100M",
        kosul: () => chk("e-bwauto"), mesaj: C("ör. 12M, 100M") },
    { id: "e-bwres", anahtar: "bw_auto_reserve_pct", tip: "sayi", adim: 6, min: 0, max: 95, vars: 30,
        kosul: () => chk("e-bwauto"), mesaj: C("0-95 arası yüzde") },
    { id: "e-bwmin", anahtar: "bw_auto_min", tip: "metin", adim: 6, rx: RX.bw, vars: "1M",
        kosul: () => chk("e-bwauto"), mesaj: C("ör. 512K, 1M") },
    { id: "e-bwmax", anahtar: "bw_auto_max", tip: "metin", adim: 6, rx: RX.bw, ops: true, vars: "",
        kosul: () => chk("e-bwauto"), mesaj: C("ör. 30M veya boş") },
    { id: "e-bwint", anahtar: "bw_auto_interval_sec", tip: "sayi", adim: 6, min: 2, max: 3600, vars: 10,
        kosul: () => chk("e-bwauto"), mesaj: C("2-3600 sn") },
    { id: "e-bwsm", anahtar: "bw_auto_smooth", tip: "sayi", adim: 6, min: 0.05, max: 1, vars: 0.4,
        kosul: () => chk("e-bwauto"), mesaj: C("0.05 - 1 arası") },
    { id: "e-bwstep", anahtar: "bw_auto_step_pct", tip: "sayi", adim: 6, min: 1, max: 90, vars: 25,
        kosul: () => chk("e-bwauto"), mesaj: C("1-90 arası yüzde") },
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
    hataTuruBaslat();
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
            ok = vNum(a.id, (_a = a.min) !== null && _a !== void 0 ? _a : 0, (_b = a.max) !== null && _b !== void 0 ? _b : null, a.mesaj || C("geçersiz sayı")) && ok;
        else if (a.tip === "saat")
            ok = vRx(a.id, RX.time, a.mesaj || "SS:DD", a.ops) && ok;
        else if (a.rx)
            ok = vRx(a.id, a.rx, a.mesaj || C("geçersiz değer"), a.ops) && ok;
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
    elif cmd == "bildir":
        # systemd OnFailure= bunu cagirir: pve-gdrive.py bildir <birim>
        sys.exit(birim_bildir(sys.argv[2] if len(sys.argv) > 2 else "bilinmeyen"))
    elif cmd == "butunluk":
        if "--sabitle" in a:
            print(butunluk_sabitle("elle")["msg"])
        else:
            d, m2 = butunluk_kontrol()
            st = read_state()
            print(f"dosya    : {betik_yolu()}")
            print(f"su anki  : {betik_ozeti()}")
            print(f"referans : {st.get('betik_sha256') or '(yok)'}"
                  + (f"  [{st.get('betik_sha_zaman')} / {st.get('betik_sha_sebep')}]"
                     if st.get("betik_sha256") else ""))
            print(f"durum    : {d}" + (f" - {m2}" if m2 else ""))
            sys.exit(0 if d == "iyi" else 1)
    elif cmd in ("version", "surum", "--version"):
        print(SURUM)
    elif cmd == "oturumlar":
        yol = oturum_dosyasi()
        print(f"dosya: {yol}")
        try:
            kayit = json.load(open(yol)).get("oturumlar") or {}
        except FileNotFoundError:
            print("  dosya yok - hic kalici oturum acilmamis"); kayit = {}
        except Exception as e:
            print(f"  okunamadi: {e}"); kayit = {}
        simdi = time.time()
        for t, v in kayit.items():
            kalan = (v.get("bitis", 0) - simdi) / 86400
            print(f"  {t[:12]}…  kullanici={v.get('user')}  adres={v.get('ip')}  "
                  f"kalan={kalan:.1f} gun  {'GECERLI' if kalan > 0 else 'SURESI DOLMUS'}")
        if not kayit: print("  (bos)")
        print(f"ayarlar: remember_enabled={cfg().get('remember_enabled')} "
              f"gun={cfg().get('remember_days')} "
              f"adres_baglama={cfg().get('session_ip_bind') or 'ip'} "
              f"samesite={cfg().get('cookie_samesite') or 'Lax'}")
    elif cmd == "saglik":
        d, m2 = tick_sagligi()
        yas = tick_yasi_dk()
        print(f"zamanlayici : {d}" + (f" (son tick {int(yas)} dk once)" if yas is not None else ""))
        if m2: print(f"uyari       : {m2}")
        sys.exit(0 if d == "iyi" else 1)
    elif cmd == "serve": serve()
    elif cmd == "snapshot":
        for p in cfg().get("plans", []):
            if pid and p["id"] != pid: continue
            put_pstate(p["id"], update_snapshot(p)); print("ok:", p["id"])
    elif cmd == "status": print(json.dumps(public_status(), ensure_ascii=False, indent=2))
    elif cmd in ("version", "--version", "-V"): print(SURUM)
    elif cmd == "aglar":
        if "--ac" in a: r = aglari_yonet("ac")
        elif "--ekle" in a: r = aglari_yonet("ekle", opt("--ekle"))
        elif "--cikar" in a: r = aglari_yonet("cikar", opt("--cikar"))
        else: r = aglari_yonet()
        if not r.get("ok"): print("HATA:", r.get("msg")); return
        ag = r["aglar"]
        print("izinli aglar:", ", ".join(ag) if ag else "(kisitlama yok - herkes erisebilir)")
        if ag and cfg().get("lan_hep_acik", True):
            print("ayrica hep acik (yerel ag):",
                  ", ".join(str(x) for x in host_lan_aglari()) or "(bulunamadi)")
        if len(a) > 1:
            print("degisiklik icin servisi yeniden baslat: systemctl restart pve-gdrive-ui")
    elif cmd in ("disa-aktar", "export"):
        print(json.dumps(disa_aktar("--sirlarla" in a), ensure_ascii=False, indent=2))
    elif cmd in ("ice-aktar", "import"):
        kaynak = opt("--dosya")
        try:
            ham = open(kaynak).read() if kaynak else sys.stdin.read()
            veri = json.loads(ham)
        except Exception as e:
            print(f"okunamadi: {e}"); return
        kip = "degistir" if "--degistir" in a else "ekle"
        r = ice_aktar(veri, kip)
        print(r["msg"] if r.get("ok") else "HATA: " + r.get("msg", ""))
        if r.get("sifresiz_smtp"):
            print("Sifresi olmayan mail profilleri (arayuzden gir): "
                  + ", ".join(r["sifresiz_smtp"]))
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
