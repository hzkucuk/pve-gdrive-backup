#!/usr/bin/env bash
# Sunucuya dagitim.
#
# Neden betik: hedef yol elle yazildiginda yanlis dosyaya kopyalanabiliyor.
# 2026-08-08'de tam olarak bu oldu — systemd /usr/local/bin/pve_gdrive.py
# calistirirken /usr/local/bin/pve-gdrive dosyasina kopyalandi ve gun boyu
# yapilan degisikliklerin hicbiri canliya gecmedi, ustelik "dagitildi" diye
# dogrulandi. Artik hedef yol systemd biriminden okunuyor ve dogrulama
# calisan surecin gercekten actigi dosya uzerinden yapiliyor.

set -euo pipefail

SUNUCU="${PGD_SUNUCU:-root@192.168.2.252}"
ANAHTAR="${PGD_ANAHTAR:-$HOME/.ssh/pve_gdrive_key}"
KAYNAK="${1:-pve_gdrive.py}"
SSH=(ssh -i "$ANAHTAR" -o BatchMode=yes -o StrictHostKeyChecking=accept-new "$SUNUCU")

[[ -f "$KAYNAK" ]] || { echo "kaynak yok: $KAYNAK" >&2; exit 1; }
python3 -c "import ast,sys;ast.parse(open(sys.argv[1]).read())" "$KAYNAK"

# 1) Hedef yolu tahmin etme: birimin ExecStart'indan oku
HEDEF=$("${SSH[@]}" 'systemctl show pve-gdrive-ui -p ExecStart --value' \
        | tr " ;" "\n\n" | grep -m1 "^/usr.*pve[-_]gdrive" || true)
[[ -n "$HEDEF" ]] || { echo "ExecStart'tan hedef yol okunamadi" >&2; exit 1; }
echo "hedef  : $HEDEF"

BOYUT=$(wc -c < "$KAYNAK")
OZET=$(python3 -c "
import hashlib,sys;print(hashlib.sha256(open(sys.argv[1],'rb').read()).hexdigest())" "$KAYNAK")
echo "kaynak : $KAYNAK ($BOYUT bayt)"

scp -i "$ANAHTAR" -o BatchMode=yes -q "$KAYNAK" "$SUNUCU:$HEDEF.new"

"${SSH[@]}" HEDEF="$HEDEF" OZET="$OZET" 'bash -s' <<'UZAK'
set -euo pipefail
python3 -c "import ast;ast.parse(open('$HEDEF.new').read())"
gelen=$(sha256sum "$HEDEF.new" | cut -d" " -f1)
[[ "$gelen" == "$OZET" ]] || { echo "aktarim bozuk: $gelen != $OZET" >&2; exit 1; }

install -d /var/lib/pve-gdrive/surumler
[[ -f "$HEDEF" ]] && cp -a "$HEDEF" "/var/lib/pve-gdrive/surumler/$(basename "$HEDEF").$(date +%Y%m%d-%H%M%S)"
# En son 5 yedegi tut, gerisini birak
ls -1t /var/lib/pve-gdrive/surumler/ 2>/dev/null | tail -n +6 \
  | while read -r e; do rm -f "/var/lib/pve-gdrive/surumler/$e"; done

mv "$HEDEF.new" "$HEDEF"; chmod 755 "$HEDEF"
# Ikinci bir kopya karisikliga yol acmasin: kisa ad artik baglanti
kisa="$(dirname "$HEDEF")/pve-gdrive"
[[ "$kisa" != "$HEDEF" ]] && ln -sfn "$HEDEF" "$kisa"

systemctl restart pve-gdrive-ui
sleep 2
UZAK

# 2) Dogrulamayi calisan surecin actigi dosyadan yap
"${SSH[@]}" OZET="$OZET" 'bash -s' <<'UZAK'
set -euo pipefail
durum=$(systemctl is-active pve-gdrive-ui)
pid=$(systemctl show -p MainPID --value pve-gdrive-ui)
[[ "$durum" == "active" && "$pid" != "0" ]] || {
  echo "servis ayakta degil ($durum)"; journalctl -u pve-gdrive-ui -n 15 --no-pager; exit 1; }

# Surecin gercekten actigi betik: cmdline'daki .py argumani
calisan=$(tr "\0" "\n" < "/proc/$pid/cmdline" | grep -m1 "pve[-_]gdrive")
gercek=$(sha256sum "$calisan" | cut -d" " -f1)
echo "servis : $durum (pid $pid)"
echo "acilan : $calisan"
[[ "$gercek" == "$OZET" ]] \
  && echo "ozet   : eslesti ✓" \
  || { echo "ozet   : ESLESMEDI ✗ ($gercek)"; exit 1; }
echo "surum  : $(grep -m1 '^SURUM' "$calisan" | cut -d'"' -f2)"
echo "parcaciklar: $(ls /proc/$pid/task | wc -l)"
UZAK
