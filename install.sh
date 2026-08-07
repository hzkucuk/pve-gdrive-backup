#!/usr/bin/env bash
# Proxmox HOST uzerinde calistir (root).
#
# Terminal etkilesimliyse soru soran kurulum sihirbazini acar (kurulum.py).
# Etkilesimsiz kipte (boru, cron, PGD_SESSIZ=1) ortami olcup varsayilan uretir.
#
# Ortam degiskenleriyle ezilebilir:
#   PGD_SESSIZ=1                       sihirbazi atla
#   PGD_ALLOW_NETWORKS="10.8.0.0/24"   izinli aglar (bos = kisitlama yok)
#   PGD_SRC_DIR=/mnt/pve/depo/dump     kaynak klasor
#   PGD_UI_PORT / PGD_UI_USER / PGD_UI_PASS / PGD_TLS
set -euo pipefail
here="$(cd "$(dirname "$0")" && pwd)"
CONF=/etc/pve-gdrive.conf

command -v rclone >/dev/null || { echo "HATA: once 'apt install -y rclone' calistir"; exit 1; }
[ -d /etc/pve ] || echo "UYARI: /etc/pve yok - bu makine Proxmox host'u olmayabilir"

# Eski surumden geciliyorsa zamanlayiciyi kaldir (zamanlama artik plan bazli)
if systemctl list-unit-files 2>/dev/null | grep -q pve-gdrive-backup.timer; then
  echo ">> eski pve-gdrive-backup.timer kaldiriliyor"
  systemctl disable --now pve-gdrive-backup.timer 2>/dev/null || true
  rm -f /etc/systemd/system/pve-gdrive-backup.{service,timer}
fi

install -m 755 "$here/pve_gdrive.py" /usr/local/bin/pve_gdrive.py
install -m 644 "$here"/systemd/pve-gdrive-*.service "$here"/systemd/pve-gdrive-*.timer /etc/systemd/system/
mkdir -p /var/lib/pve-gdrive

etkilesimli=0
[ -t 0 ] && [ -t 1 ] && [ -z "${PGD_SESSIZ:-}" ] && [ -f "$here/kurulum.py" ] && etkilesimli=1

if [ "$etkilesimli" = "1" ]; then
  # Sihirbaz son satirda JSON dondurur; onu dosyaya yaziyoruz.
  cikti="$(python3 "$here/kurulum.py")" || { echo "Kurulum iptal edildi."; exit 1; }
  printf '%s\n' "$cikti" | tail -1 | python3 -c '
import json, os, sys
try: d = json.loads(sys.stdin.read())
except Exception: sys.exit(0)
if d.get("_kip") != "yaz": sys.exit(0)
with open("/etc/pve-gdrive.conf", "w") as f:
    json.dump(d["_conf"], f, indent=2, ensure_ascii=False)
os.chmod("/etc/pve-gdrive.conf", 0o600)
with open("/tmp/.pgd-sifre", "w") as f: f.write(d.get("_sifre", ""))
print(">> /etc/pve-gdrive.conf yazildi")
'
elif [ ! -f "$CONF" ]; then
  echo ">> ortam olculuyor (etkilesimsiz kip)..."
  python3 -c '
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
with open("/etc/pve-gdrive.conf", "w") as f: json.dump(c, f, indent=2, ensure_ascii=False)
os.chmod("/etc/pve-gdrive.conf", 0o600)
with open("/tmp/.pgd-sifre", "w") as f: f.write(sifre if uretildi else "")
'
else
  echo ">> $CONF zaten var, dokunulmuyor (planlarin korunur)"
fi

chmod 600 "$CONF"
systemctl daemon-reload
systemctl enable --now pve-gdrive-ui.service >/dev/null
systemctl enable --now pve-gdrive-tick.timer >/dev/null
systemctl restart pve-gdrive-ui.service
sleep 2

port="$(python3 -c "import json;print(json.load(open('$CONF')).get('ui_port',8787))")"
sema="$(python3 -c "import json;print('https' if json.load(open('$CONF')).get('ssl_cert') else 'http')")"
kul="$(python3 -c "import json;print(json.load(open('$CONF'))['ui_user'])")"
ip="$(hostname -I 2>/dev/null | awk '{print $1}')"
sifre="$(cat /tmp/.pgd-sifre 2>/dev/null || true)"; rm -f /tmp/.pgd-sifre

echo
echo "================= Kurulum tamam ================="
echo "  Arayuz   : ${sema}://${ip:-<host-ip>}:${port}"
echo "  Kullanici: ${kul}"
[ -n "$sifre" ] && echo "  SIFRE    : ${sifre}   <-- simdi kaydet, bir daha gosterilmez"
echo "  Servis   : $(systemctl is-active pve-gdrive-ui) | Surum: $(pve_gdrive.py version)"
echo
echo "Siradaki adimlar:"
echo "  1) Google hesabi: kendi bilgisayarinda bir terminalde tunel ac"
echo "       ssh -N -L 53682:127.0.0.1:53682 root@${ip:-<host-ip>}"
echo "     Arayuzde: + Yeni Plan -> 3. adim -> '+ Yeni hesap' -> Baslat"
echo "  2) Mail profili : Ayarlar -> Mail profilleri"
echo "  3) Proxmox linki: ./proxmox-link.sh"
echo "  4) Plan KAPALI olusturuldu; hesabi secip gozden gecirdikten sonra etkinlestir."
echo
echo "Baska bir kurulumdan ayar tasima:"
echo "  eski hostta: pve_gdrive.py disa-aktar > ayarlar.json"
echo "  bu hostta  : pve_gdrive.py ice-aktar < ayarlar.json"
