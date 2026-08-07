#!/usr/bin/env python3
"""
Arayuzu derleyip pve_gdrive.py icine gomer.

  cd ui && npm run build          (tsc + bu betik)
  veya: python3 build_ui.py       (tsc'yi kendi calistirir)

Boylece Proxmox host'unda Node.js gerekmez: sunucuya yalnizca derlenmis
tek dosyalik pve_gdrive.py gider.
"""
import os, re, subprocess, sys

ROOT = os.path.dirname(os.path.abspath(__file__))
UI = os.path.join(ROOT, "ui")
TARGET = os.path.join(ROOT, "pve_gdrive.py")
BASLA = "# --- UI BUNDLE START (build_ui.py uretir, elle duzenleme) ---"
BITIR = "# --- UI BUNDLE END ---"

def tsc():
    binp = os.path.join(UI, "node_modules", ".bin", "tsc")
    if not os.path.exists(binp):
        print("HATA: typescript kurulu degil -> cd ui && npm install", file=sys.stderr)
        sys.exit(1)
    r = subprocess.run([binp, "-p", UI], capture_output=True, text=True)
    if r.returncode != 0:
        print("TypeScript derleme hatasi:\n" + (r.stdout or "") + (r.stderr or ""), file=sys.stderr)
        sys.exit(1)
    print("  tsc: derlendi")

def oku(p):
    with open(p, encoding="utf-8") as f: return f.read()

def main():
    if "--no-tsc" not in sys.argv: tsc()
    css = oku(os.path.join(UI, "styles.css"))
    body = oku(os.path.join(UI, "index.html"))
    js = oku(os.path.join(UI, "dist", "app.js"))

    for parca, ad in ((css, "styles.css"), (body, "index.html"), (js, "dist/app.js")):
        if "'''" in parca:
            print(f"HATA: {ad} icinde ''' var, Python ham dizesini bozar", file=sys.stderr)
            sys.exit(1)

    html = ('<!doctype html><html lang="tr"><head><meta charset="utf-8">\n'
            '<meta name="viewport" content="width=device-width,initial-scale=1">\n'
            "<title>Proxmox → Drive Yedek</title>\n<style>\n" + css + "\n</style></head><body>\n"
            + body + "\n<script>\n" + js + "\n</script></body></html>\n")

    src = oku(TARGET)
    yeni = BASLA + "\nHTML = r'''" + html + "'''\n" + BITIR
    if BASLA in src:
        src = re.sub(re.escape(BASLA) + r".*?" + re.escape(BITIR), lambda m: yeni, src, flags=re.S)
    else:
        m = re.search(r"^HTML = r'''.*?'''", src, re.S | re.M)
        if not m:
            print("HATA: pve_gdrive.py icinde HTML blogu bulunamadi", file=sys.stderr); sys.exit(1)
        src = src[:m.start()] + yeni + src[m.end():]
    with open(TARGET, "w", encoding="utf-8") as f: f.write(src)

    subprocess.run([sys.executable, "-m", "py_compile", TARGET], check=True)
    print(f"  gomuldu: {len(html)} bayt HTML -> pve_gdrive.py")
    print(f"  css {len(css)}B · html {len(body)}B · js {len(js)}B")

if __name__ == "__main__": main()
