#!/usr/bin/env python3
"""Bu repoya dokunan TUM Claude Code oturumlarini bulur.

NEDEN VAR
---------
Claude Code oturumlari calisma dizinine gore klasorluyor:
~/.claude/projects/<yol-slug>/*.jsonl. Ama is her zaman o dizinden yapilmiyor --
baska bir projede acilmis bir oturum `cd ~/source/repos/pve-gdrive-backup` deyip
bu repoda calisabiliyor ve cogu zaman calisiyor da.

2026-08-09'da tam bu oldu: 1.9.0'in tamami (kod, test, commit, dagitim) baska bir
projenin oturumunda yapildi. O oturumda rclone'un --include/--exclude sirasini
belirsiz saydigi ve filtrenin skip_patterns garantisini bozdugu OLCULDU; yedekleme
betigi ise yalnizca bu projenin klasorune baktigi icin o kayit yedeklenmiyordu.

Kayit kaybolursa "neden boyle yaptik" sorusunun cevabi da kaybolur.

NASIL BULUYOR
-------------
Yabanci bir oturum dosyasinin icinde repo yolunun GECMESI aranir (calisma dizini,
okunan dosya yollari, komutlar). Yalnizca proje ADINI aramak yanlis eslesme
uretirdi -- "pve-gdrive-backup" lafi gecen ama repoya hic dokunmamis bir sohbet
de yakalanirdi.

Ortam degiskeni:
  PGD_PROJELER_KOK   oturum klasorlerinin koku (varsayilan ~/.claude/projects)
"""

import os
import sys
from pathlib import Path

REPO_KOK = Path(__file__).resolve().parent.parent
OKUMA_PARCASI = 1 << 20          # 1 MB: 22 MB'lik dosyayi bellege almadan tara


def projeler_kok() -> Path:
    return Path(os.environ.get("PGD_PROJELER_KOK") or (Path.home() / ".claude" / "projects"))


def proje_slug(yol: Path = REPO_KOK) -> str:
    """Claude Code'un klasor adi: yoldaki '/' ve bosluklar '-' olur."""
    return str(yol).replace("/", "-").replace(" ", "-")


def _iceriyor(dosya: Path, igne: bytes) -> bool:
    """Dosyada igne geciyor mu. Parca parca okur; 22 MB'lik oturumlar var.

    Parcalar arasinda bolunen eslesmeyi kacirmamak icin her turda onceki
    parcanin son (len(igne)-1) baytini basa ekliyoruz.
    """
    kuyruk = b""
    try:
        with open(dosya, "rb") as f:
            while True:
                parca = f.read(OKUMA_PARCASI)
                if not parca:
                    return False
                if igne in kuyruk + parca:
                    return True
                kuyruk = parca[-(len(igne) - 1):] if len(igne) > 1 else b""
    except OSError:
        return False


def oturumlar(repo: Path = REPO_KOK):
    """[(proje_klasoru, dosya)] -- once bu projenin oturumlari, sonra yabancilar.

    Bu projenin klasorundeki her dosya kosulsuz alinir (zaten burada calisilmis).
    Diger klasorlerde yalnizca repo yolu GECEN dosyalar alinir.
    """
    kok = projeler_kok()
    if not kok.is_dir():
        return []
    kendi_ad = proje_slug(repo)
    igne = str(repo).encode()
    out = []
    kendi = kok / kendi_ad
    if kendi.is_dir():
        out += [(kendi_ad, p) for p in sorted(kendi.glob("*.jsonl"))]
    for d in sorted(kok.iterdir()):
        if not d.is_dir() or d.name == kendi_ad:
            continue
        for p in sorted(d.glob("*.jsonl")):
            if _iceriyor(p, igne):
                out.append((d.name, p))
    return out


def main() -> int:
    """Kabuk betigi icin: her satir '<proje-klasoru><TAB><dosya-yolu>'.

    --slug: yalnizca bu reponun kendi klasor adini basar (kabuk, yabanci
    oturumlari ayirt edebilsin diye buna ihtiyac duyuyor)."""
    if "--slug" in sys.argv:
        print(proje_slug())
        return 0
    for proje, yol in oturumlar():
        print(f"{proje}\t{yol}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
