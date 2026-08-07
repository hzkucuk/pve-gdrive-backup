# Geliştirme

## Mimari

```
pve_gdrive.py          tek dosya: CLI + zamanlayıcı + HTTP sunucu + gömülü arayüz
├── config/durum       /etc/pve-gdrive.conf, /var/lib/pve-gdrive/state.json
├── çekirdek           do_copy, do_prune, do_purge_trash, do_run, do_tick
├── web                H(BaseHTTPRequestHandler) + oturum/captcha/CSRF
└── HTML               build_ui.py tarafından gömülür  ← ELLE DÜZENLEME

ui/                    arayüz kaynağı (TypeScript)
├── index.html         işaretleme
├── styles.css         stiller
├── src/types.ts       API veri yapıları
└── src/app.ts         uygulama mantığı
```

**Neden Python arka uç + TypeScript arayüz?** Proxmox host'unda Python 3 hazır gelir, Node.js
gelmez. Arka ucu Python'da tutmak hipervizöre ek çalışma zamanı ve `node_modules` koymamayı
sağlar. Arayüz TypeScript'te yazılır, geliştirme makinesinde derlenir, çıktı Python'a gömülür.
Sunucuya tek dosya gider.

## Derleme

```bash
cd ui
npm install            # bir kez
npm run build          # tsc + build_ui.py: pve_gdrive.py içine gömer
npm run check          # sadece tip kontrolü
npm run watch          # sürekli derleme
```

Doğrudan:

```bash
python3 build_ui.py             # tsc'yi kendi çalıştırır
python3 build_ui.py --no-tsc    # mevcut dist/app.js'i gömer
```

`build_ui.py` `index.html` + `styles.css` + `dist/app.js` birleştirip `pve_gdrive.py` içindeki
`# --- UI BUNDLE START/END ---` blokları arasına yazar ve sonucu `py_compile` ile doğrular.
Parçalarda `'''` varsa Python ham dizesini bozacağı için derleme durur.

TypeScript `strict` modda derlenir: `noImplicitAny`, `strictNullChecks`, `noUnusedLocals`,
`noImplicitReturns`.

## Test

Testler sahte bir `rclone` ile çalışır — gerçek Drive'a dokunmaz. Mock durum tutar ve
`copy`, `lsjson` (`--drive-trashed-only` dahil), `deletefile`, `delete`, `about`, `cleanup`,
`listremotes` destekler. `MOCK_FAIL=copy|lstrash` ile hata senaryosu üretilir.

```bash
PVE_GDRIVE_CONF=<test.conf> PATH=<mock-bin>:$PATH MOCK_DB=<db.json> \
  python3 pve_gdrive.py run --plan <id>
```

Kapsanan senaryolar:

| Alan | Doğrulanan |
|---|---|
| Retention | Gün sınırı + adet tabanı birlikte doğru set seçiyor |
| Güvenlik | Yükleme hatasında hiçbir dosya silinmiyor |
| Güvenlik | Çöp listelenemezse takip kaydı düşürülmüyor |
| Çakışma | vzdump süreci/kilidi/taze `.dat` varken tur atlanıyor |
| Çakışma | Bayat `.dat` planı bloklamıyor, sadece yüklemeden dışlanıyor |
| Zamanlama | Vakti gelmeyen çalışmıyor, gelen bir kez çalışıyor, kapalı atlanıyor |
| Göç | Eski tek planlı config plan listesine dönüşüyor |
| Bellek | 200.000 satır rclone çıktısında artış 0.05 MB |
| Bellek | Log `log_max_mb` eşiğinde dönüyor, en fazla `log_keep`+1 dosya |
| Giriş | Captcha, CSRF, oturum, kilitlenme, güvenlik başlıkları |
| API | Plan/hesap/profil CRUD, kök dışına çıkamayan gezgin |

## Kod kuralları

- İletişim, yorum, log ve arayüz metinleri **Türkçe**
- Sabit kodlanmış değer yok: her eşik config'e, arayüze ve belgeye
- Silme yolunu değiştiren her değişiklikte cevaplanacak soru:
  *"Yükleme başarısız olursa eski yedekler silinir mi?"* — cevap her zaman **hayır**
- Listeleme hatası ile "gerçekten boş" asla karıştırılmaz
- Uzun süre çalışan serviste bellek ve disk sınırsız büyümez

Ayrıntılı çalışma direktifleri için depo kökündeki `CLAUDE.md`.

## Sürüm çıkarma

```bash
cd ui && npm run build && cd ..
python3 -m py_compile pve_gdrive.py
git add -A && git commit && git push
```

Sunucuya dağıtım:

```bash
scp pve_gdrive.py root@<host>:/usr/local/bin/pve_gdrive.py
ssh root@<host> systemctl restart pve-gdrive-ui
```
