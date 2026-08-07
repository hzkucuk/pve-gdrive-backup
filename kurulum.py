#!/usr/bin/env python3
"""
pve-gdrive-backup interaktif kurulum sihirbazi / interactive setup wizard.

  ./install.sh              -> terminal etkilesimliyse bu sihirbazi acar
  python3 kurulum.py        -> dogrudan
  python3 kurulum.py --en   -> Ingilizce basla / start in English

Bagimliligi yok. Ortami olcer, varsayilanlari onerir, hicbir seyi sormadan degistirmez.
No dependencies. Measures the environment, proposes defaults, changes nothing unasked.
"""
import json, os, re, secrets, subprocess, sys

CONF = os.environ.get("PVE_GDRIVE_CONF", "/etc/pve-gdrive.conf")
R = "\033[0m"; B = "\033[1m"; Y = "\033[33m"; G = "\033[32m"; C = "\033[36m"; K = "\033[31m"
GB = 1024 ** 3

M = {
 "tr": {
  "dil_sor": "Dil / Language",
  "baslik": "pve-gdrive-backup kurulum sihirbazı",
  "alt": "Sorulara Enter ile köşeli parantezdeki varsayılanı kabul edersin.",
  "olcum": "Ortam ölçülüyor",
  "pve_yok": "UYARI: /etc/pve yok — bu makine Proxmox host'u olmayabilir",
  "rclone_yok": "HATA: rclone kurulu değil. Önce: apt install -y rclone",
  "mevcut_conf": "{0} zaten var. Ne yapalım?",
  "mc_koru": "Dokunma, sadece programı güncelle (planların korunur)",
  "mc_yeni": "Baştan yapılandır (mevcut ayarlar yedeklenip değiştirilir)",
  "mc_cik": "Çık",
  "b_ag": "Erişim",
  "s_ag": "Hangilerine izin verilsin? (numaraları virgülle yaz, ya da CIDR yapıştır)",
  "ag_ipucu": "SSH ile {0} adresinden bağlandın",
  "ag_lan": "Sunucunun kendi yerel ağı ({0}) — zaten her zaman açık",
  "ag_lan_bilgi": "Sunucunun yerel ağı ({0}) her zaman açık kalır; kendini kilitleyemezsin.",
  "ag_ek": "Buna EK olarak hangi ağlardan erişilsin? (ör. VPN)",
  "ag_hepsi": "Kısıtlama yok — ağdaki herkes erişebilir",
  "ag_uyari": "DİKKAT: Buraya yazmadığın bir ağdan arayüze giremezsin (403).\n"
              "  Şimdi VPN'den bağlıysan ama sonra yerel ağdan da gireceksen İKİSİNİ de seç.",
  "ag_kurtarma": "Kilitlenirsen: sunucuda 'pve_gdrive.py aglar --ac' komutu kısıtlamayı kaldırır.",
  "ag_secildi": "seçilen: {0}",
  "b_arayuz": "Arayüz",
  "s_port": "Arayüz portu",
  "s_kul": "Kullanıcı adı",
  "s_sifre": "Şifre (boş bırakırsan rastgele üretilir)",
  "s_tls": "HTTPS açılsın mı? (Proxmox sertifikası bulundu)",
  "tls_yok": "Proxmox sertifikası bulunamadı, arayüz düz HTTP çalışacak.",
  "b_kaynak": "Kaynak",
  "s_kaynak": "Yedeklerin okunacağı klasör",
  "kaynak_bulundu": "{0} dosya bulundu ({1})",
  "kaynak_bos": "Bu klasörde tanınan vzdump dosyası yok.",
  "kaynak_yok": "Klasör bulunamadı.",
  "b_saklama": "Saklama",
  "olcum_sonuc": "Ölçüm: günde {0} üretiliyor ({1} günlük set, toplam {2})",
  "s_gun": "Drive'da kaç gün saklansın?",
  "s_cop": "Google çöp kutusunda kaç gün beklesin?",
  "s_taban": "Misafir başına en az kaç set korunsun? (gün sınırından muaf)",
  "yer": "{0} gün + {1} gün çöp → yaklaşık {2} yer gerekir",
  "yer_bilinmez": "Kota bilinmediği için doluluk hesaplanamadı (hesabı sonra ekleyeceksin).",
  "b_zaman": "Zamanlama",
  "s_saat": "Her gün saat kaçta çalışsın? (SS:DD)",
  "s_bekle": "Proxmox yedeği çalışıyorsa en fazla kaç dakika beklensin?",
  "b_ozet": "Özet",
  "onay": "Bu ayarlarla kurulsun mu?",
  "iptal": "Kurulum iptal edildi, hiçbir şey değişmedi.",
  "yaziliyor": "Yapılandırma yazılıyor",
  "bitti": "Kurulum tamam",
  "sifre_kaydet": "ŞİFRE: {0}   ← şimdi kaydet, bir daha gösterilmez",
  "sonraki": "Sıradaki adımlar",
  "adim1": "Google hesabı ekle. Kendi bilgisayarında bir terminalde tünel aç:",
  "adim2": "Sonra arayüzde: + Yeni Plan → 3. adım → '＋ Yeni hesap' → Başlat",
  "adim3": "Mail profili: Ayarlar → Mail profilleri",
  "adim4": "Proxmox arayüzüne link: ./proxmox-link.sh",
  "adim5": "Plan KAPALI oluşturuldu. Hesabı seçip gözden geçirdikten sonra etkinleştir.",
  "tasima": "Başka bir kurulumdan ayar taşımak için:",
  "evet": "e", "hayir": "h", "eh": "e/h",
  "gecersiz": "Geçersiz değer, tekrar dene.",
  "secim": "Seçim",
 },
 "en": {
  "dil_sor": "Dil / Language",
  "baslik": "pve-gdrive-backup setup wizard",
  "alt": "Press Enter to accept the default shown in brackets.",
  "olcum": "Inspecting environment",
  "pve_yok": "WARNING: /etc/pve not found — this may not be a Proxmox host",
  "rclone_yok": "ERROR: rclone is not installed. Run: apt install -y rclone",
  "mevcut_conf": "{0} already exists. What should we do?",
  "mc_koru": "Keep it, only update the program (your plans are preserved)",
  "mc_yeni": "Reconfigure from scratch (current settings are backed up first)",
  "mc_cik": "Quit",
  "b_ag": "Access",
  "s_ag": "Which ones to allow? (comma separated numbers, or paste a CIDR)",
  "ag_ipucu": "You connected over SSH from {0}",
  "ag_lan": "The server's own local network ({0}) — always allowed",
  "ag_lan_bilgi": "The server's local network ({0}) always stays open; you cannot lock yourself out.",
  "ag_ek": "Which ADDITIONAL networks should be allowed? (e.g. VPN)",
  "ag_hepsi": "No restriction — anyone on the network can reach it",
  "ag_uyari": "CAREFUL: You cannot reach the UI from a network you do not list here (403).\n"
              "  If you are on VPN now but will also connect from the LAN, pick BOTH.",
  "ag_kurtarma": "If locked out: run 'pve_gdrive.py aglar --ac' on the server to clear it.",
  "ag_secildi": "selected: {0}",
  "b_arayuz": "Web interface",
  "s_port": "Web interface port",
  "s_kul": "Username",
  "s_sifre": "Password (leave empty to generate one)",
  "s_tls": "Enable HTTPS? (Proxmox certificate found)",
  "tls_yok": "No Proxmox certificate found; the UI will run over plain HTTP.",
  "b_kaynak": "Source",
  "s_kaynak": "Folder to read backups from",
  "kaynak_bulundu": "found {0} files ({1})",
  "kaynak_bos": "No recognised vzdump files in this folder.",
  "kaynak_yok": "Folder not found.",
  "b_saklama": "Retention",
  "olcum_sonuc": "Measured: {0} produced per day ({1} daily sets, {2} total)",
  "s_gun": "Keep backups on Drive for how many days?",
  "s_cop": "Days to wait in Google trash before permanent deletion?",
  "s_taban": "Minimum sets kept per guest? (exempt from the day limit)",
  "yer": "{0} days + {1} days trash -> about {2} of space needed",
  "yer_bilinmez": "Quota unknown, usage not projected (add the account later).",
  "b_zaman": "Schedule",
  "s_saat": "What time should it run every day? (HH:MM)",
  "s_bekle": "If a Proxmox backup is running, wait at most how many minutes?",
  "b_ozet": "Summary",
  "onay": "Install with these settings?",
  "iptal": "Setup cancelled, nothing was changed.",
  "yaziliyor": "Writing configuration",
  "bitti": "Setup complete",
  "sifre_kaydet": "PASSWORD: {0}   <- save it now, it will not be shown again",
  "sonraki": "Next steps",
  "adim1": "Add a Google account. On your own computer, open a tunnel:",
  "adim2": "Then in the UI: + New Plan -> step 3 -> 'Add account' -> Start",
  "adim3": "Mail profile: Settings -> Mail profiles",
  "adim4": "Link inside the Proxmox UI: ./proxmox-link.sh",
  "adim5": "The plan was created DISABLED. Enable it after picking an account.",
  "tasima": "To carry settings over from another installation:",
  "evet": "y", "hayir": "n", "eh": "y/n",
  "gecersiz": "Invalid value, try again.",
  "secim": "Choice",
 },
}
L = "tr"
def t(k, *a):
    s = M[L].get(k, k)
    return s.format(*a) if a else s

def baslik(s):
    print(f"\n{B}{C}── {s} {'─' * max(0, 58 - len(s))}{R}")

def sor(mesaj, vars=None, dogrula=None, gizli=False):
    while True:
        ek = f" [{vars}]" if vars not in (None, "") else ""
        try: cevap = input(f"{Y}?{R} {mesaj}{ek}: ").strip()
        except (EOFError, KeyboardInterrupt): print(); sys.exit(1)
        if not cevap and vars is not None: cevap = str(vars)
        if dogrula:
            ok, mesaj2 = dogrula(cevap)
            if not ok:
                print(f"  {K}{mesaj2 or t('gecersiz')}{R}"); continue
        return cevap

def sor_eh(mesaj, vars=True):
    e, h = t("evet"), t("hayir")
    v = e if vars else h
    c = sor(f"{mesaj} ({t('eh')})", v).lower()
    return c.startswith(e)

def sor_secim(mesaj, secenekler):
    print(f"{Y}?{R} {mesaj}")
    for i, s in enumerate(secenekler, 1): print(f"    {B}{i}{R}) {s}")
    while True:
        c = sor(t("secim"), "1")
        if c.isdigit() and 1 <= int(c) <= len(secenekler): return int(c) - 1
        print(f"  {K}{t('gecersiz')}{R}")

def insan(n):
    n = float(n or 0)
    for u in ("B", "KB", "MB", "GB", "TB"):
        if n < 1024: return f"{n:.1f} {u}"
        n /= 1024
    return f"{n:.1f} PB"

def host_aglari():
    """Sunucunun kendi IPv4 aglari. Kurulumu VPN'den yapip sonra yerel agdan
    girmek isteyen kullanici kendini disarida birakmasin diye onerilir."""
    out = []
    try:
        r = subprocess.run(["ip", "-4", "-o", "addr", "show", "scope", "global"],
                           capture_output=True, text=True, timeout=10)
        for satir in r.stdout.splitlines():
            p = satir.split()
            if len(p) < 4: continue
            arayuz, adres = p[1], p[3]
            m = re.match(r"^(\d{1,3})\.(\d{1,3})\.(\d{1,3})\.\d{1,3}/(\d{1,2})$", adres)
            if not m: continue
            out.append((f"{m.group(1)}.{m.group(2)}.{m.group(3)}.0/24", arayuz))
    except Exception:
        pass
    return out

def dump_klasorleri():
    out, cur = [], None
    try:
        with open("/etc/pve/storage.cfg") as f:
            for line in f:
                m = re.match(r"^(\w+):\s*(\S+)", line)
                if m: cur = {"ad": m.group(2), "path": None, "content": ""}; out.append(cur)
                elif cur is not None:
                    m2 = re.match(r"\s+(\w+)\s+(.*)", line)
                    if m2 and m2.group(1) in ("path", "content"): cur[m2.group(1)] = m2.group(2).strip()
    except Exception: return []
    RE = re.compile(r"^vzdump-(qemu|lxc)-\d+-\d{4}_\d{2}_\d{2}-")
    r = []
    for s in out:
        if not s.get("path") or "backup" not in (s.get("content") or ""): continue
        d = os.path.join(s["path"], "dump")
        try: n = len([x for x in os.listdir(d) if RE.match(x)])
        except Exception: n = -1
        r.append({"ad": s["ad"], "yol": d, "n": n})
    return sorted(r, key=lambda x: -x["n"])

def klasor_olc(yol):
    RE = re.compile(r"^vzdump-(qemu|lxc)-(\d+)-(\d{4}_\d{2}_\d{2})-")
    try: dosyalar = os.listdir(yol)
    except Exception: return None
    gun, toplam, n = {}, 0, 0
    for f in dosyalar:
        m = RE.match(f)
        if not m: continue
        try: b = os.path.getsize(os.path.join(yol, f))
        except Exception: continue
        gun[m.group(3)] = gun.get(m.group(3), 0) + b; toplam += b; n += 1
    if not gun: return None
    return {"dosya": n, "set": len(gun), "toplam": toplam, "gunluk": toplam / len(gun)}

def main():
    global L
    if "--en" in sys.argv: L = "en"
    else:
        print(f"\n  {B}1{R}) Türkçe    {B}2{R}) English")
        try: L = "en" if input("  Dil / Language [1]: ").strip() == "2" else "tr"
        except (EOFError, KeyboardInterrupt): print(); sys.exit(1)

    print(f"\n{B}{t('baslik')}{R}\n{t('alt')}")

    if not any(os.access(os.path.join(d, "rclone"), os.X_OK)
               for d in os.environ.get("PATH", "").split(":") if d):
        print(f"{K}{t('rclone_yok')}{R}"); sys.exit(1)
    if not os.path.isdir("/etc/pve"): print(f"{Y}{t('pve_yok')}{R}")

    if os.path.exists(CONF):
        i = sor_secim(t("mevcut_conf", CONF), [t("mc_koru"), t("mc_yeni"), t("mc_cik")])
        if i == 2: sys.exit(1)
        if i == 0: print(json.dumps({"_kip": "koru"})); sys.exit(0)
        import shutil, datetime
        yd = CONF + "." + datetime.datetime.now().strftime("%Y%m%d-%H%M%S") + ".yedek"
        shutil.copy2(CONF, yd); print(f"  {G}✓{R} {yd}")

    baslik(t("b_ag"))
    adaylar = []      # (cidr, aciklama)
    ip = (os.environ.get("SSH_CLIENT") or os.environ.get("SSH_CONNECTION") or "").split()
    if ip and re.match(r"^\d{1,3}(\.\d{1,3}){3}$", ip[0]):
        o = ip[0].split(".")
        adaylar.append((f"{o[0]}.{o[1]}.{o[2]}.0/24", t("ag_ipucu", ip[0])))
    lan = host_aglari()
    lan_cidr = [c for c, _ in lan]
    adaylar = [(c, a) for c, a in adaylar if c not in lan_cidr]   # yereli aday listesinden cikar
    if lan:
        print(f"  {G}✓{R} " + t("ag_lan_bilgi", ", ".join(lan_cidr)))
    print(f"  {Y}{t('ag_uyari')}{R}")
    print(f"  {t('ag_kurtarma')}\n")
    for i, (cidr, ac) in enumerate(adaylar, 1):
        print(f"    {B}{i}{R}) {cidr:20} {ac}")
    print(f"    {B}0{R}) {t('ag_hepsi')}")
    def d_ag(v):
        v = v.strip()
        if not v: return False, None
        for x in [y.strip() for y in v.split(",") if y.strip()]:
            if x.isdigit():
                if not (0 <= int(x) <= len(adaylar)): return False, None
            elif not re.match(r"^\d{1,3}(\.\d{1,3}){3}(/\d{1,2})?$", x): return False, None
        return True, None
    varsayilan = ",".join(str(i) for i in range(1, len(adaylar) + 1)) if adaylar else "0"
    secim = sor(t("ag_ek") if lan else t("s_ag"), varsayilan, d_ag)
    aglar = []
    for x in [y.strip() for y in secim.split(",") if y.strip()]:
        if x == "0": aglar = []; break
        if x.isdigit(): aglar.append(adaylar[int(x) - 1][0])
        else: aglar.append(x)
    aglar = list(dict.fromkeys(aglar))
    print(f"  {G}✓{R} " + (t("ag_secildi", ", ".join(aglar)) if aglar else t("ag_hepsi")))

    baslik(t("b_arayuz"))
    port = int(sor(t("s_port"), "8787",
                   lambda v: (v.isdigit() and 1 <= int(v) <= 65535, None)))
    kul = sor(t("s_kul"), "admin", lambda v: (bool(v.strip()), None))
    sifre = sor(t("s_sifre"), "")
    uretildi = not sifre
    if uretildi: sifre = secrets.token_urlsafe(12)
    cert, key = "/etc/pve/local/pve-ssl.pem", "/etc/pve/local/pve-ssl.key"
    tls = False
    if os.path.exists(cert) and os.path.exists(key): tls = sor_eh(t("s_tls"), True)
    else: print(f"  {Y}{t('tls_yok')}{R}")

    baslik(t("b_kaynak"))
    ks = dump_klasorleri()
    vars_k = ks[0]["yol"] if ks and ks[0]["n"] > 0 else "/var/lib/vz/dump"
    for k in ks:
        if k["n"] >= 0: print(f"  · {k['yol']}  ({k['n']}, '{k['ad']}')")
    def d_k(v):
        if not os.path.isdir(v): return False, t("kaynak_yok")
        return True, None
    src = sor(t("s_kaynak"), vars_k, d_k)
    olcum = klasor_olc(src)
    if olcum: print(f"  {G}✓{R} " + t("kaynak_bulundu", olcum["dosya"], insan(olcum["toplam"])))
    else: print(f"  {Y}{t('kaynak_bos')}{R}")

    baslik(t("b_saklama"))
    if olcum:
        print("  " + t("olcum_sonuc", insan(olcum["gunluk"]), olcum["set"], insan(olcum["toplam"])))
    gun = int(sor(t("s_gun"), "14", lambda v: (v.isdigit() and 0 <= int(v) <= 3650, None)))
    cop = float(sor(t("s_cop"), "1", lambda v: (re.match(r"^\d+(\.\d+)?$", v) is not None, None)))
    taban = int(sor(t("s_taban"), "3", lambda v: (v.isdigit() and 0 <= int(v) <= 999, None)))
    if olcum:
        ger = olcum["gunluk"] * (gun + cop)
        print(f"  {G}→{R} " + t("yer", gun, cop, insan(ger)))
    else:
        print(f"  {Y}{t('yer_bilinmez')}{R}")

    baslik(t("b_zaman"))
    saat = sor(t("s_saat"), "03:00",
               lambda v: (re.match(r"^([01]?\d|2[0-3]):[0-5]\d$", v) is not None, None))
    bekle = int(sor(t("s_bekle"), "120", lambda v: (v.isdigit() and int(v) <= 1440, None)))

    baslik(t("b_ozet"))
    ozet = [("UI", f"{'https' if tls else 'http'}://<host>:{port}  ({kul})"),
            (t("b_ag"), ", ".join(aglar) if aglar else "—"),
            (t("b_kaynak"), src),
            (t("b_saklama"), f"{gun} + {cop} · min {taban}"),
            (t("b_zaman"), f"{saat} · vzdump {bekle} dk")]
    for k, v in ozet: print(f"  {k:12} {B}{v}{R}")
    if not sor_eh(t("onay"), True):
        print(f"\n{Y}{t('iptal')}{R}"); sys.exit(1)

    c = {"ui_bind": "0.0.0.0", "ui_port": port, "ui_user": kul, "ui_pass": sifre,
         "allow_networks": aglar, "ssl_cert": cert if tls else "", "ssl_key": key if tls else "",
         "cookie_secure": bool(tls), "lan_hep_acik": True, "smtp_profiles": [],
         "plans": [{"id": "gunluk", "name": "Gunluk yedek" if L == "tr" else "Daily backup",
                    "enabled": False, "src_dir": src, "remote": "gdrive:proxmox-yedek",
                    "keep_days": gun, "keep_count": taban, "drive_trash_days": cop,
                    "run_at": saat, "wait_for_vzdump": True, "vzdump_wait_min": bekle}]}
    print(json.dumps({"_kip": "yaz", "_conf": c, "_sifre": sifre if uretildi else "",
                      "_dil": L}, ensure_ascii=False))

if __name__ == "__main__":
    main()
