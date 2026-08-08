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

# curl | bash ile calisirken stdin BORUDUR ve bash betigin kendisini oradan
# okur. Sihirbazin soru sorabilmesi icin terminale ihtiyaci var, ama bu betigin
# stdin'ini terminale cevirmek OLUMCUL: bash betigin kalanini klavyeden beklemeye
# baslar, ekrana hicbir sey yazilmadan asili kalir. (Proxmox web konsolunda tam
# olarak bu oldu; ssh -T'de /dev/tty acilmadigi icin fark edilmemisti.)
#
# Dogrusu: kendi stdin'imize DOKUNMA, terminali yalnizca calistiracagimiz
# sihirbaza ayri bir betimleyiciyle ver.
TTY_VAR=0
if [ -t 0 ]; then
  TTY_VAR=1                 # zaten terminaldeyiz (indirilip elle calistirilmis)
elif { : </dev/tty; } 2>/dev/null; then
  TTY_VAR=1                 # boruyuz ama terminal erisilebilir
else
  export PGD_SESSIZ=1       # terminal yok: sihirbaz atlanir, otomatik tespit
fi

# Ilk satir HEMEN basilir: kurulum sessizce asili kalirsa nerede takildigi
# belli olsun. (Ilk denemede curl'de takilip ekrana hicbir sey yazmamisti.)
bilgi "pve-gdrive-backup kurulumu basliyor"

# Dis dunyaya erisim var mi? Yoksa curl zaman asimi olmadan dakikalarca asilir.
bilgi "internet erisimi kontrol ediliyor"
if ! curl -fsS --connect-timeout 8 --max-time 20 -o /dev/null \
     "https://raw.githubusercontent.com/" 2>/dev/null; then
  echo
  renk "31;1" "HATA: https://raw.githubusercontent.com adresine ulasilamiyor."
  echo "Kontrol et:"
  echo "  ping -c2 1.1.1.1        # ag var mi"
  echo "  ping -c2 github.com     # DNS calisiyor mu"
  echo "  cat /etc/resolv.conf    # DNS sunucusu tanimli mi"
  echo "  curl -v --max-time 10 https://raw.githubusercontent.com/  # ayrintili hata"
  echo
  echo "Vekil sunucu arkasindaysan:"
  echo "  export https_proxy=http://vekil:3128 && curl -fsSL .../kur.sh | bash"
  echo
  echo "Internetsiz kurulum: arsivi baska bir makinede indirip kopyala:"
  echo "  curl -fsSLO $REPO/archive/refs/heads/$DAL.tar.gz"
  echo "  scp $DAL.tar.gz root@bu-host:/tmp/ && tar xzf /tmp/$DAL.tar.gz -C /opt"
  echo "  cd /opt/pve-gdrive-backup-$DAL && ./install.sh"
  exit 1
fi

bilgi "bagimliliklar kontrol ediliyor"
eksik=()
command -v rclone >/dev/null || eksik+=(rclone)
command -v python3 >/dev/null || eksik+=(python3)
command -v curl >/dev/null || command -v wget >/dev/null || eksik+=(curl)
if [ ${#eksik[@]} -gt 0 ]; then
  bilgi "kuruluyor: ${eksik[*]}"
  export DEBIAN_FRONTEND=noninteractive
  apt-get -o Acquire::Retries=2 -o Acquire::http::Timeout=20 update -qq \
    || bilgi "apt update basarisiz, kurulu paketlerle devam ediliyor"
  apt-get install -y -qq "${eksik[@]}" >/dev/null \
    || hata "paketler kurulamadi: ${eksik[*]} (apt kaynaklarini kontrol et)"
fi
command -v rclone >/dev/null || hata "rclone kurulamadi"

bilgi "indiriliyor: $REPO ($DAL)"
gecici="$(mktemp -d)"
trap 'rm -rf "$gecici"' EXIT
url="$REPO/archive/refs/heads/${DAL}.tar.gz"
if command -v curl >/dev/null; then
  curl -fsSL --connect-timeout 15 --max-time 180 "$url" | tar xz -C "$gecici" \
    || hata "indirilemedi: $url"
else
  wget -q --timeout=15 --tries=2 -O- "$url" | tar xz -C "$gecici" \
    || hata "indirilemedi: $url"
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
# Terminali yalnizca install.sh'e ver; bizim stdin'imiz boru olarak kalir.
if [ "$TTY_VAR" = "1" ] && [ -z "${PGD_SESSIZ:-}" ]; then
  ./install.sh </dev/tty
else
  ./install.sh
fi
