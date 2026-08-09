#!/usr/bin/env python3
"""Etkilesimsiz kurulumda varsayilan yapilandirmayi uretir.

Once install.sh icinde `python3 -c '...'` olarak gomuluydu. Ayri dosya olmasinin
iki sebebi var:
  1) TEST EDILEBILSIN. Kurulumun urettigi config, urunun bugunku plan semasina
     uymak zorunda; gomulu tek satiri calistirmadan bunu kimse dogrulayamiyordu.
  2) Yazilacak yolu PVE_GDRIVE_CONF belirlesin -- urun ve kurulum.py zaten bu
     degiskeni taniyor, install.sh ise yolu iki yerde sabit yaziyordu.

Yeni plan alanlari buraya EKLENMEZ: urun PLAN_DEFAULTS ile birlestirdigi icin
burada olmayan alan guvenli varsayilanini alir (or. include_patterns = [] =
klasordeki her sey). Burada yalnizca ORTAMDAN olculen ya da kullanicidan gelen
degerler durur.

Ortam degiskenleri: PGD_ALLOW_NETWORKS, PGD_SRC_DIR, PGD_UI_PORT, PGD_UI_USER,
PGD_UI_PASS, PGD_TLS, PVE_GDRIVE_CONF, PGD_SIFRE_DOSYASI
"""


import json, os, re, secrets

def ortam(ad, vars=""): return os.environ.get(ad, "").strip() or vars

aglar = [x.strip() for x in ortam("PGD_ALLOW_NETWORKS").split(",") if x.strip()]
if not aglar:
    ip = (os.environ.get("SSH_CLIENT") or "").split()
    if ip and re.match(r"^\d{1,3}(\.\d{1,3}){3}$", ip[0]):
        p = ip[0].split("."); aglar = [f"{p[0]}.{p[1]}.{p[2]}.0/24"]
        print(f"   izinli ag: {aglar[0]} (SSH kaynagi {ip[0]})")

RE = re.compile(r"^vzdump-(qemu|lxc)-\d+-\d{4}_\d{2}_\d{2}-")
def dumplar():
    out, cur = [], None
    try:
        for line in open("/etc/pve/storage.cfg"):
            m = re.match(r"^(\w+):\s*(\S+)", line)
            if m: cur = {"path": None, "content": ""}; out.append(cur)
            elif cur is not None:
                m2 = re.match(r"\s+(\w+)\s+(.*)", line)
                if m2 and m2.group(1) in ("path", "content"): cur[m2.group(1)] = m2.group(2).strip()
    except Exception: return []
    r = []
    for s in out:
        if not s.get("path") or "backup" not in (s.get("content") or ""): continue
        d = os.path.join(s["path"], "dump")
        try: n = len([x for x in os.listdir(d) if RE.match(x)])
        except Exception: n = -1
        r.append((n, d))
    return sorted(r, reverse=True)

src = ortam("PGD_SRC_DIR")
if not src:
    ks = dumplar()
    src = ks[0][1] if ks and ks[0][0] > 0 else "/var/lib/vz/dump"
    print(f"   kaynak: {src}")

cert, key = "/etc/pve/local/pve-ssl.pem", "/etc/pve/local/pve-ssl.key"
tls = ortam("PGD_TLS", "1" if (os.path.exists(cert) and os.path.exists(key)) else "0") == "1"
sifre = ortam("PGD_UI_PASS") or secrets.token_urlsafe(12)
uretildi = not ortam("PGD_UI_PASS")

c = {"ui_bind": "0.0.0.0", "ui_port": int(ortam("PGD_UI_PORT", "8787")),
     "ui_user": ortam("PGD_UI_USER", "admin"), "ui_pass": sifre, "allow_networks": aglar,
     "ssl_cert": cert if tls else "", "ssl_key": key if tls else "", "cookie_secure": bool(tls),
     "smtp_profiles": [],
     "plans": [{"id": "gunluk", "name": "Gunluk yedek", "enabled": False, "src_dir": src,
                "remote": "gdrive:proxmox-yedek", "keep_days": 14, "keep_count": 3,
                "drive_trash_days": 1, "run_at": "03:00", "wait_for_vzdump": True,
                "vzdump_wait_min": 120}]}
CONF = os.environ.get("PVE_GDRIVE_CONF", "/etc/pve-gdrive.conf")
with open(CONF, "w") as f: json.dump(c, f, indent=2, ensure_ascii=False)
os.chmod(CONF, 0o600)
# Uretilen arayuz sifresi /tmp'de duruyor ve install.sh okuyup siliyor.
# O aralikta host'taki her yerel kullanici okuyabiliyordu: dosya once
# 0600 acilir, sonra yazilir.
sifre_dosyasi = os.environ.get("PGD_SIFRE_DOSYASI", "/tmp/.pgd-sifre")
fd = os.open(sifre_dosyasi, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
with os.fdopen(fd, "w") as f:
    f.write(sifre if uretildi else "")
