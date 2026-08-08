#!/usr/bin/env bash
# Sohbet transcript'lerini yedekler ve icindeki gorselleri cikarir.
#
# NEDEN VAR
# Claude Code her oturumu ~/.claude/projects/<proje>/*.jsonl altinda tutuyor.
# O klasor bizim degil: surum yukseltmesi, disk temizligi ya da bir hata onu
# goturebilir. Icinde yalnizca sohbet degil, paylasilan ekran goruntuleri ve
# uzerine cizilmis notlar da var -- bu projede kaynak seciciyi ve giris
# ekranini duzelten kararlarin cogu o goruntulerden cikti.
#
# NEDEN TARIHLI KLASOR DEGIL
# Her yedek onlarca MB. Ayni oturumun dosyasi ayni adi tasidigi icin uzerine
# yazmak dogru davranis: dosya yalnizca BUYUR, gecmisi degismez. Yeni oturum
# yeni dosya olarak eklenir.
#
# GUVENLIK -- BU PROJEDE ONEMLI
# Transcript ham sohbettir: bu projede Telegram bot jetonu ve Proxmox root
# parolasi konusuldu. Depo PUBLIC. Bu yuzden hedef klasor .gitignore'da ve
# betik her calismada gercekten yok sayildigini DOGRULAR; sayilmiyorsa
# kopyalamadan durur.
#
# Kullanim:  tools/transcript-yedekle.sh
# Ne zaman:  oturum sonunda, /clear veya /compact ONCESI ve SONRASI

set -euo pipefail

PROJE="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
KAYNAK="$HOME/.claude/projects/-Users-hzkucuk-source-repos-pve-gdrive-backup"
HEDEF="$PROJE/docs/_transcripts/yedek"

[ -d "$KAYNAK" ] || { echo "⚠ Transcript klasoru yok: $KAYNAK"; exit 0; }

# Once guvenlik: hedef gercekten yok sayiliyor mu? Sayilmiyorsa hicbir sey
# kopyalama -- 22 MB ham sohbeti public repoya sokmaktansa yedeksiz kal.
mkdir -p "$HEDEF"
if ! git -C "$PROJE" check-ignore -q "$HEDEF/deneme.jsonl" 2>/dev/null; then
  echo "HATA: $HEDEF git tarafindan yok sayilmiyor." >&2
  echo "  .gitignore'a 'docs/_transcripts/' ekle, sonra tekrar calistir." >&2
  echo "  (Transcript ham sohbettir: jeton ve parola icerebilir.)" >&2
  exit 1
fi

kopyalanan=0
for f in "$KAYNAK"/*.jsonl; do
  [ -e "$f" ] || continue
  hedef_dosya="$HEDEF/$(basename "$f")"
  # -nt: yalnizca kaynak daha yeniyse. Degismemis 20 MB'i her seferinde
  # yeniden yazmanin anlami yok.
  if [ ! -f "$hedef_dosya" ] || [ "$f" -nt "$hedef_dosya" ]; then
    cp "$f" "$hedef_dosya"
    chmod 600 "$hedef_dosya"        # ham sohbet, baskasina okunmasin
    kopyalanan=$((kopyalanan + 1))
  fi
done

toplam=$(find "$HEDEF" -name '*.jsonl' | wc -l | tr -d ' ')
boyut=$(du -sh "$HEDEF" | cut -f1)
echo "✓ Transcript: $kopyalanan guncellendi, toplam $toplam dosya ($boyut)"
echo "  -> docs/_transcripts/yedek/  (git tarafindan yok sayiliyor ✓)"

if [ -f "$PROJE/tools/transcript-gorselleri.py" ]; then
  python3 "$PROJE/tools/transcript-gorselleri.py" | tail -1
  echo "  ⚠ Repoda kalmasini istedigin gorseli docs/gorseller/ altina ELLE kopyala."
fi

# Son kontrol: yanlislikla izlenen bir sey kalmadi mi
izlenen=$(git -C "$PROJE" ls-files docs/_transcripts | head -3)
if [ -n "$izlenen" ]; then
  echo "UYARI: bu dosyalar git tarafindan IZLENIYOR, kaldir:" >&2
  echo "$izlenen" | sed 's/^/  /' >&2
  echo "  git rm --cached -r docs/_transcripts" >&2
fi
