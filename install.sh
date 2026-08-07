#!/usr/bin/env bash
# Proxmox HOST uzerinde calistir (root). rclone + gdrive remote'u onceden kurulu olmali.
set -euo pipefail
here="$(cd "$(dirname "$0")" && pwd)"

command -v rclone >/dev/null || { echo "once: apt install -y rclone && rclone config (gdrive remote)"; exit 1; }

# Eski tek planli surumden geciliyorsa timer'i kaldir (config otomatik goc eder)
if systemctl list-unit-files | grep -q pve-gdrive-backup.timer; then
  echo ">> eski pve-gdrive-backup.timer kaldiriliyor (zamanlama artik plan bazli)"
  systemctl disable --now pve-gdrive-backup.timer 2>/dev/null || true
  rm -f /etc/systemd/system/pve-gdrive-backup.{service,timer}
fi

install -m 755 "$here/pve_gdrive.py" /usr/local/bin/pve_gdrive.py
install -m 644 "$here"/systemd/pve-gdrive-*.service "$here"/systemd/pve-gdrive-*.timer /etc/systemd/system/

mkdir -p /var/lib/pve-gdrive
if [ ! -f /etc/pve-gdrive.conf ]; then
  python3 /usr/local/bin/pve_gdrive.py init
  echo ">> /etc/pve-gdrive.conf olusturuldu."
fi
chmod 600 /etc/pve-gdrive.conf

systemctl daemon-reload
systemctl enable --now pve-gdrive-ui.service
systemctl enable --now pve-gdrive-tick.timer
systemctl restart pve-gdrive-ui.service

port="$(python3 -c "import json;print(json.load(open('/etc/pve-gdrive.conf')).get('ui_port',8787))" 2>/dev/null || echo 8787)"
ip="$(hostname -I 2>/dev/null | awk '{print $1}')"
echo
echo "Kurulum tamam."
echo "  UI      : http://${ip:-<host-ip>}:${port}   (kullanici/sifre icin: /etc/pve-gdrive.conf)"
echo "  Planlar : pve_gdrive.py plans"
echo "  Zamanlayici: systemctl list-timers | grep pve-gdrive"
echo "  Log     : tail -f /var/log/pve-gdrive.log"
echo
echo "Siradaki adim: UI'a gir, sag ustten Ayarlar (sifre + SMTP), sonra '+ Yeni Plan'."
