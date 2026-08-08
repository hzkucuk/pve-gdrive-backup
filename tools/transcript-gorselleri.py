#!/usr/bin/env python3
"""Sohbet transcript'lerindeki gorselleri gercek dosyalara cikarir.

NEDEN VAR
---------
Claude Code her oturumu ~/.claude/projects/<proje>/*.jsonl altinda saklar ve
paylasilan ekran goruntulerini base64 olarak o dosyaya gomer. Gorsel kaybolmuyor
ama baglam ozetlendiginde gorunmez oluyor ve kimse o 22 MB'lik dosyayi acip
bakmiyor.

Bu projede kararlarin cogu ekran goruntusunden cikti: saklama ipuclarindaki
sabit "14 gun", tarayici dialoglari, giris ekranindaki kayik damga, ZFS
havuzunun kaynak listesinde hic gorunmemesi, bitmis isin tekrar onerilmesi.
Hepsi bir goruntude isaretlenip gosterildi. O goruntuler kaybolursa "neden
boyle yapmistik" sorusunun cevabi da kaybolur.

GUVENLIK -- BU PROJEDE ONEMLI
-----------------------------
Cikti docs/_transcripts/gorseller/ altina yazilir ve o klasor .gitignore'da.
Sebep somut: bu projede konusulan seyler arasinda Telegram bot jetonu, Proxmox
root parolasi ve arayuz sifresi var; ekran goruntuleri de token, IP ve dosya
yolu gosteriyor. Depo PUBLIC.

Repoda kalmasini istedigin bir gorseli docs/gorseller/ altina ELLE kopyala.
Boylece neyin yayinlandigi her zaman bilincli bir karar olur.

KULLANIM
--------
  tools/transcript-gorselleri.py                 # tum oturumlar
  tools/transcript-gorselleri.py --son 1         # yalniz en son oturum
  tools/transcript-gorselleri.py --hedef <dizin>
"""

import argparse
import base64
import json
import os
import sys
from datetime import datetime
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_DEST = REPO_ROOT / "docs" / "_transcripts" / "gorseller"

EXTENSIONS = {
    "image/png": "png",
    "image/jpeg": "jpg",
    "image/gif": "gif",
    "image/webp": "webp",
}


def project_dir() -> Path:
    """Claude Code'un bu repo icin kullandigi klasor: yoldaki '/' ve bosluklar '-' olur."""
    slug = str(REPO_ROOT).replace("/", "-").replace(" ", "-")
    return Path.home() / ".claude" / "projects" / slug


def find_images(node, found):
    """Kaydin neresinde olursa olsun base64 gorselleri toplar.

    Yol sabit degil: gorseller kullanici mesajinin content'inde de gelebiliyor,
    ayri bir 'attachment' kaydinda da (2026-08-01'de ikincisiydi). Sekli varsaymak
    yerine agaci geziyoruz -- format degisirse betik sessizce bos donmez.
    """
    if isinstance(node, dict):
        source = node.get("source")
        if node.get("type") == "image" and isinstance(source, dict) and source.get("data"):
            found.append((source.get("media_type", "image/png"), source["data"]))
            return
        for value in node.values():
            find_images(value, found)
    elif isinstance(node, list):
        for value in node:
            find_images(value, found)


def nearby_text(record) -> str:
    """Gorselin yanindaki kullanici metni -- dosya adini anlamli kilan sey."""
    texts = []

    def walk(node):
        if isinstance(node, dict):
            if node.get("type") == "text" and isinstance(node.get("text"), str):
                texts.append(node["text"])
            for value in node.values():
                walk(value)
        elif isinstance(node, list):
            for value in node:
                walk(value)

    walk(record)
    return " ".join(texts).strip()


def slugify(text: str, limit: int = 48) -> str:
    keep = []
    for ch in text.lower():
        if ch.isalnum():
            keep.append(ch)
        elif ch in " -_" and (not keep or keep[-1] != "-"):
            keep.append("-")
    return "".join(keep).strip("-")[:limit] or "gorsel"


def main() -> int:
    parser = argparse.ArgumentParser(description="Transcript gorsellerini dosyaya cikarir.")
    parser.add_argument("--hedef", type=Path, default=DEFAULT_DEST)
    parser.add_argument("--son", type=int, default=0, help="Yalniz en son N oturum.")
    args = parser.parse_args()

    source = project_dir()
    if not source.is_dir():
        print(f"HATA: transcript klasoru bulunamadi: {source}", file=sys.stderr)
        return 1

    sessions = sorted(source.glob("*.jsonl"), key=os.path.getmtime, reverse=True)
    if args.son > 0:
        sessions = sessions[: args.son]

    args.hedef.mkdir(parents=True, exist_ok=True)

    written = 0
    skipped = 0
    index_lines = []

    for session in sessions:
        with session.open(encoding="utf-8", errors="replace") as handle:
            for line_no, line in enumerate(handle, start=1):
                if '"type":"image"' not in line:
                    continue
                try:
                    record = json.loads(line)
                except json.JSONDecodeError:
                    continue

                found = []
                find_images(record, found)
                if not found:
                    continue

                stamp = record.get("timestamp", "")
                try:
                    when = datetime.fromisoformat(stamp.replace("Z", "+00:00")).astimezone()
                    when_name = when.strftime("%Y%m%d-%H%M")
                    when_human = when.strftime("%Y-%m-%d %H:%M")
                except ValueError:
                    when_name = f"satir{line_no}"
                    when_human = "(zaman damgasi yok)"

                label = slugify(nearby_text(record))

                for index, (media_type, data) in enumerate(found, start=1):
                    extension = EXTENSIONS.get(media_type, "bin")
                    name = f"{when_name}_{session.stem[:8]}_{index}_{label}.{extension}"
                    target = args.hedef / name

                    # Ayni gorseli her calistirmada yeniden yazmayalim: bu betik bir git
                    # hook'undan da cagrilabilir ve o zaman her commit'te 20 dosya dokunur.
                    if target.exists():
                        skipped += 1
                        continue

                    try:
                        target.write_bytes(base64.b64decode(data))
                    except (ValueError, OSError) as error:
                        print(f"  atlandi ({name}): {error}", file=sys.stderr)
                        continue

                    written += 1
                    index_lines.append(f"| {when_human} | `{name}` | {nearby_text(record)[:90]} |")

    if index_lines:
        index = args.hedef / "INDEX.md"
        header = (
            "# Transcript gorselleri\n\n"
            "> Otomatik uretildi: `tools/transcript-gorselleri.py`.\n"
            "> Bu klasor `.gitignore`'da -- bir ekran goruntusu sir gosteriyor olabilir.\n"
            "> Repo'da kalmasini istedigin gorseli `docs/gorseller/` altina **elle** kopyala.\n\n"
            "| Ne zaman | Dosya | Yanindaki metin |\n|---|---|---|\n"
        )
        existing = index.read_text(encoding="utf-8") if index.exists() else ""
        rows = "\n".join(index_lines) + "\n"
        index.write_text((existing or header) + rows if existing else header + rows, encoding="utf-8")

    print(f"{written} gorsel yazildi, {skipped} zaten vardi -> {args.hedef}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
