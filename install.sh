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
#   PVE_GDRIVE_CONF=/yol/pve-gdrive.conf   yapilandirma dosyasi (varsayilan /etc/...)
set -euo pipefail
here="$(cd "$(dirname "$0")" && pwd)"
CONF="${PVE_GDRIVE_CONF:-/etc/pve-gdrive.conf}"
export PVE_GDRIVE_CONF="$CONF"

command -v rclone >/dev/null || { echo "HATA: once 'apt install -y rclone' calistir"; exit 1; }
[ -d /etc/pve ] || echo "UYARI: /etc/pve yok - bu makine Proxmox host'u olmayabilir"

# Eski surumden geciliyorsa zamanlayiciyi kaldir (zamanlama artik plan bazli)
if systemctl list-unit-files 2>/dev/null | grep -q pve-gdrive-backup.timer; then
  echo ">> eski pve-gdrive-backup.timer kaldiriliyor"
  systemctl disable --now pve-gdrive-backup.timer 2>/dev/null || true
  rm -f /etc/systemd/system/pve-gdrive-backup.{service,timer}
fi

install -m 755 "$here/pve_gdrive.py" /usr/local/bin/pve_gdrive.py
# Kisa ad: "pve-gdrive saglik" gibi kullanim icin. Sembolik baglanti guvenli,
# guncelleme realpath ile gercek dosyayi bulur (bkz. betik_yolu).
ln -sfn /usr/local/bin/pve_gdrive.py /usr/local/bin/pve-gdrive
# pve-gdrive-bildir@.service dahil tum birimler (sablon birim enable edilmez,
# OnFailure ile ihtiyac aninda calisir)
install -m 644 "$here"/systemd/pve-gdrive-*.service "$here"/systemd/pve-gdrive-*.timer /etc/systemd/system/
# Durum dizininde jetonlar, oturumlar ve config yedekleri var: yalnizca root
mkdir -p /var/lib/pve-gdrive && chmod 700 /var/lib/pve-gdrive

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
CONF = os.environ.get("PVE_GDRIVE_CONF", "/etc/pve-gdrive.conf")
with open(CONF, "w") as f:
    json.dump(d["_conf"], f, indent=2, ensure_ascii=False)
os.chmod(CONF, 0o600)
# Sifre dosyasi once 0600 acilir: /tmp herkese okunur, install.sh onu
# okuyup silene kadar uretilen arayuz sifresi ortada kalmasin.
sd = os.environ.get("PGD_SIFRE_DOSYASI", "/tmp/.pgd-sifre")
fd = os.open(sd, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
with os.fdopen(fd, "w") as f: f.write(d.get("_sifre", ""))
print(">> " + CONF + " yazildi")
'
elif [ ! -f "$CONF" ]; then
  echo ">> ortam olculuyor (etkilesimsiz kip)..."
  python3 "$here/tools/varsayilan-conf.py"
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
echo "  Servis   : $(systemctl is-active pve-gdrive-ui) | Surum: $(pve_gdrive.py version 2>/dev/null || echo '?')"
echo "  Bildirim : birim cokerse mail (pve-gdrive-bildir@.service)"
echo
echo "Siradaki adimlar:"
echo "  1) Google hesabi: kendi bilgisayarinda bir terminalde tunel ac"
echo "       ssh -N -L 53682:127.0.0.1:53682 root@${ip:-<host-ip>}"
echo "     Arayuzde: + Yeni Plan -> 3. adim -> '+ Yeni hesap' -> Baslat"
echo "  2) Mail profili : Ayarlar -> Mail profilleri"
echo "  3) Proxmox linki: Arayuz -> Ayarlar -> Bakim ve tasima -> 'Proxmox'a link ekle'"
echo "     (Datacenter -> Notes alanina tiklanabilir link koyar. Terminalden:"
echo "      ./proxmox-link.sh)"
echo "  4) Plan KAPALI olusturuldu; hesabi secip gozden gecirdikten sonra etkinlestir."
echo
echo "Kaynak klasor gorunmuyorsa: ZFS havuzlari (zfspool) yedek DOSYASI tutamaz."
echo "  Arayuzde plan duzenle -> Kaynak: yedek alamayan depolar sebebiyle listelenir"
echo "  ve 'Yedek alani olustur' dugmesi havuzda dataset + dizin deposu acar."
echo
echo "Baska bir kurulumdan ayar tasima:"
echo "  eski hostta: pve-gdrive disa-aktar > ayarlar.json"
echo "  bu hostta  : pve-gdrive ice-aktar < ayarlar.json"
echo
echo "Teshis komutlari:"
echo "  pve-gdrive saglik      zamanlayici yasiyor mu (cikis kodu 0/1)"
echo "  pve-gdrive plans       planlar ve sonraki calisma"
echo "  pve-gdrive oturumlar   'beni hatirla' oturumlari"
echo "  journalctl -u pve-gdrive-ui -f"
echo
echo "NOT: 'Beni hatirla' cikis yapinca silinir (tasarim geregi). Sinamak icin"
echo "     cikisa basma, sekmeyi kapatip tekrar ac. Ayrica cerez ADRESE baglidir:"
echo "     hep ayni adresle gir (IP ile girdiysen hep IP ile)."
