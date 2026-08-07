#!/usr/bin/env bash
# Proxmox arayuzune yedek UI'sina giden bir link ekler (Datacenter -> Notes).
# Upgrade-guvenli: Proxmox'un hicbir dosyasina dokunmaz, sadece not alanini gunceller.
set -euo pipefail
cfg=/etc/pve-gdrive.conf
port="$(python3 -c "import json;print(json.load(open('$cfg')).get('ui_port',8787))" 2>/dev/null || echo 8787)"
# TLS acikken link https olmali, yoksa tarayici bos sayfa gosterir
sema="$(python3 -c "
import json,os
try: c=json.load(open('$cfg'))
except Exception: c={}
cert,key=c.get('ssl_cert'),c.get('ssl_key')
print('https' if cert and key and os.path.exists(cert) and os.path.exists(key) else 'http')
" 2>/dev/null || echo http)"
host="${1:-$(hostname -I 2>/dev/null | awk '{print $1}')}"
url="${sema}://${host}:${port}"
mark="<!-- pve-gdrive -->"
line="${mark} [🗄️ Google Drive Yedek](${url})"

cur="$(pvesh get /cluster/options --output-format json 2>/dev/null | python3 -c \
  "import json,sys; print(json.load(sys.stdin).get('description',''))" 2>/dev/null || echo "")"

if printf '%s' "$cur" | grep -q "$mark"; then
  cur="$(printf '%s' "$cur" | grep -v "$mark" || true)"
fi
new="$(printf '%s\n%s' "$cur" "$line" | sed '/^$/d')"
pvesh set /cluster/options --description "$new"
echo "Eklendi. Proxmox arayuzunde: Datacenter -> Notes"
echo "  $url"
echo
echo "Not: Notes alani Markdown render eder, link tiklanabilir olur."
