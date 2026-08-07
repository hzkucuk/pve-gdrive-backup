#!/usr/bin/env bash
# Proxmox arayuzune yedek UI'sina giden bir link ekler (Datacenter -> Notes).
# Upgrade-guvenli: Proxmox'un hicbir dosyasina dokunmaz, sadece not alanini gunceller.
set -euo pipefail
port="$(python3 -c "import json;print(json.load(open('/etc/pve-gdrive.conf')).get('ui_port',8787))" 2>/dev/null || echo 8787)"
host="${1:-$(hostname -I 2>/dev/null | awk '{print $1}')}"
url="http://${host}:${port}"
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
