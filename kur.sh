#!/usr/bin/env bash
# pve-gdrive-backup tek satirlik kurulum / one-line installer
#
#   curl -fsSL https://raw.githubusercontent.com/hzkucuk/pve-gdrive-backup/main/kur.sh | bash
#
# Sihirbazi atlamak icin:
#   curl -fsSL .../kur.sh | PGD_SESSIZ=1 bash
set -euo pipefail

REPO="${PGD_REPO:-https://github.com/hzkucuk/pve-gdrive-backup}"
DAL="${PGD_DAL:-main}"
HEDEF="${PGD_HEDEF:-/opt/pve-gdrive-backup}"

renk() { printf '\033[%sm%s\033[0m\n' "$1" "$2"; }
bilgi() { renk "36;1" "==> $*"; }
hata()  { renk "31;1" "HATA: $*" >&2; exit 1; }

[ "$(id -u)" = "0" ] || hata "root olarak calistir (sudo -i)"

# curl | bash ile calisirken stdin borudan gelir ve sihirbaz soru soramaz.
# Terminal VARSA girdiyi ona baglayip etkilesimi geri kazaniriz. Dosyanin varligi
# yetmez, gercekten acilabiliyor olmasi gerekir (cron/ssh -T'de acilmaz).
if [ ! -t 0 ]; then
  if { exec 3</dev/tty; } 2>/dev/null; then
    exec 0<&3 3<&-
  else
    export PGD_SESSIZ=1   # terminal yok: sihirbaz atlanir, otomatik tespit kullanilir
  fi
fi

bilgi "bagimliliklar kontrol ediliyor"
eksik=()
command -v rclone >/dev/null || eksik+=(rclone)
command -v python3 >/dev/null || eksik+=(python3)
command -v curl >/dev/null || command -v wget >/dev/null || eksik+=(curl)
if [ ${#eksik[@]} -gt 0 ]; then
  bilgi "kuruluyor: ${eksik[*]}"
  export DEBIAN_FRONTEND=noninteractive
  apt-get update -qq
  apt-get install -y -qq "${eksik[@]}" >/dev/null || hata "paketler kurulamadi: ${eksik[*]}"
fi
command -v rclone >/dev/null || hata "rclone kurulamadi"

bilgi "indiriliyor: $REPO ($DAL)"
gecici="$(mktemp -d)"
trap 'rm -rf "$gecici"' EXIT
url="$REPO/archive/refs/heads/${DAL}.tar.gz"
if command -v curl >/dev/null; then
  curl -fsSL "$url" | tar xz -C "$gecici" || hata "indirilemedi: $url"
else
  wget -qO- "$url" | tar xz -C "$gecici" || hata "indirilemedi: $url"
fi
kaynak="$(find "$gecici" -maxdepth 1 -type d -name 'pve-gdrive-backup-*' | head -1)"
[ -n "$kaynak" ] || hata "arsiv beklenen bicimde degil"

# Onceki kurulum varsa yerinde guncelle; /etc/pve-gdrive.conf'a dokunulmaz
if [ -d "$HEDEF" ]; then
  bilgi "mevcut kopya guncelleniyor: $HEDEF"
  rm -rf "$HEDEF.eski" && mv "$HEDEF" "$HEDEF.eski"
fi
mkdir -p "$(dirname "$HEDEF")"
mv "$kaynak" "$HEDEF"
chmod +x "$HEDEF"/install.sh "$HEDEF"/kurulum.py "$HEDEF"/proxmox-link.sh 2>/dev/null || true

bilgi "kurulum baslatiliyor"
cd "$HEDEF"
./install.sh
