# Kurulum Kılavuzu

Bu belge kurulumun tamamını kapsar. Değişiklik oldukça güncellenir.

## 1. Ön koşullar

| Gereksinim | Not |
|---|---|
| Proxmox VE 7 veya 8 | Doğrulandığı sürüm: 8.4.19 |
| Python 3.9+ | Proxmox ile hazır gelir (Debian 12'de 3.11) |
| `rclone` | `apt install -y rclone` — Debian 12'de 1.60.1, yeterli |
| root erişimi | systemd birimleri ve `/etc` yazımı için |
| Google hesabı | Yedeklerin sığacağı boş alan |

Bu araç yedeği **almaz**. Proxmox'un kendi yedekleme işi dosyaları üretir
(`Datacenter → Backup`), bu araç onları Drive'a taşır ve yaşam döngüsünü yönetir.
Önce Proxmox tarafında çalışan bir yedek işin olduğundan emin ol.

## 2. rclone kurulumu

```bash
apt install -y rclone
rclone version
```

## 3. Kurulum

```bash
git clone https://github.com/hzkucuk/pve-gdrive-backup
cd pve-gdrive-backup
./install.sh
```

`install.sh` şunları yapar:

| Adım | Sonuç |
|---|---|
| Script kopyalar | `/usr/local/bin/pve_gdrive.py` (arayüz içine gömülü, tek dosya) |
| systemd birimleri | `pve-gdrive-ui.service`, `pve-gdrive-tick.timer` |
| Config oluşturur | `/etc/pve-gdrive.conf`, mod 600 |
| Durum dizini | `/var/lib/pve-gdrive` |
| Eski sürümü temizler | `pve-gdrive-backup.timer` (zamanlama artık plan bazlı) |

Kurulumdan sonra arayüz: `http://<host-ip>:8787`

## 4. İlk giriş ve şifre

Varsayılan: `admin` / `degistir-beni`

İlk açılışta şifre otomatik olarak `pbkdf2_sha256` ile hash'lenir ve config'e geri yazılır —
düz metin şifre dosyada kalmaz. Giriş yaptıktan sonra **⚙ Ayarlar → Yeni şifre** ile hemen değiştir.

Giriş ekranında SVG captcha vardır ve arka arkaya hatalı denemede adres geçici olarak kilitlenir
(`login_max_attempts`, `login_lockout_min`).

## 5. Google hesabı ekleme

Arayüzde **👤 Hesaplar → Yeni hesap ekle**. İki yol var.

### Yol A — Tarayıcıyla yetkilendir (önerilen)

Google, onay sonrası tarayıcıyı **senin bilgisayarındaki** `127.0.0.1:53682` adresine
yönlendirir. Bu yüzden önce kendi bilgisayarında bir tünel açman gerekir:

```bash
ssh -N -L 53682:127.0.0.1:53682 root@<proxmox-ip>
```

Terminali açık bırak, arayüzde hesap adını yaz ve **Başlat**'a bas. Çıkan adresi tarayıcında
aç, hedef Google hesabıyla giriş yap, izni onayla. Jeton otomatik alınır ve hesap oluşur.

> Google "bu uygulamayı doğrulamadı" uyarısı verebilir — rclone'un kendi istemci kimliği için
> normaldir, **Gelişmiş → devam et** ile geç.

### Yol B — Hazır jetonu yapıştır

Başkasının hesabını ekleyeceksen, o kişi kendi bilgisayarında şunu çalıştırıp çıkan JSON'u
sana gönderebilir — şifresini paylaşması gerekmez:

```bash
rclone authorize "drive" --drive-scope drive.file
```

Arayüzde **Hazır jetonu yapıştır** sekmesine yapıştır.

### İzin kapsamı

Hesaplar `drive.file` kapsamıyla eklenir: rclone yalnızca **kendi oluşturduğu** dosyaları
görür ve silebilir. Drive'ındaki diğer hiçbir şeye erişemez. Kota okuma bu kapsamla çalışır
(gerçek hesapta doğrulandı).

## 6. Mail profili

**✉ Mail → Yeni profil.** Sağlayıcı şablonu seçince sunucu, port ve güvenlik otomatik dolar.

| Sağlayıcı | Sunucu | Port | Not |
|---|---|---|---|
| Gmail | smtp.gmail.com | 587 | Hesap şifresi **çalışmaz**, uygulama şifresi üret |
| Outlook / M365 | smtp.office365.com | 587 | Kurumsal hesapta SMTP AUTH kapalı olabilir |
| Yandex | smtp.yandex.com | 465 | Uygulama şifresi gerekir |
| Yahoo | smtp.mail.yahoo.com | 465 | Uygulama şifresi zorunlu |

Kaydettikten sonra **Test maili** ile doğrula.

## 7. İlk plan

**+ Yeni Plan.** Hazır senaryolardan biriyle başlayıp üzerinde oynayabilirsin.

Doldurman gerekenler:

1. **Plan adı** — listede ve mail konularında görünür
2. **Yedek klasörü** — **📁 Gözat** ile seç. Form, `/etc/pve/storage.cfg` okunarak bulunan
   yedek depolarını da tek tıkla sunar. Klasördeki tanınan dosya sayısı gösterilir;
   sıfır görüyorsan yanlış klasördesin.
3. **Hesap ve klasör** — hedef Google hesabı ve içindeki klasör.
   **Her plan farklı klasöre yazmalı**, yoksa planlar birbirinin yedeğini siler.
4. **Saklama süreleri** — aşağıdaki tabloya bak
5. **Saat** — Proxmox'un kendi yedek işi bittikten sonrasını seç
6. **Bildirim** — profil, alıcı ve hangi sonuçta mail isteyeceğin

### Saklama nasıl hesaplanır

```
gereken alan ≈ günlük yedek boyutu × keep_days
```

Örnek: günde 54 GB üretiliyorsa ve `keep_days=14` ise Drive'da ~750 GB gerekir.
Çöpte bekleyen bir günlük set de kotadan yer kaplar, onu da ekle.

## 8. Zamanlayıcıyı açma

Kurulumda `pve-gdrive-tick.timer` etkinleşir ve **5 dakikada bir** hangi planın vaktinin
geldiğine bakar. Saat ve gün ayarı planın içindedir; systemd dosyasına dokunman gerekmez.

```bash
systemctl list-timers | grep pve-gdrive     # sonraki tetikleme
pve_gdrive.py plans                         # planlar ve sonraki çalışma zamanları
```

İlk yükleme büyükse (yüzlerce GB) elle başlatıp izlemek mantıklı: plan kartında **▶ Yedekle**.
Arayüzde yüzde, hız, geçen süre ve tahmini bitiş zamanı canlı görünür.

## 9. Proxmox arayüzünden erişim

### Datacenter → Notes'a link (upgrade-güvenli)

```bash
./proxmox-link.sh                # veya: ./proxmox-link.sh 10.0.0.5
```

Proxmox'un not alanı Markdown render eder; `Datacenter → Notes` altında tıklanabilir link
çıkar. Proxmox'un hiçbir dosyasına dokunulmaz, güncellemede bozulmaz.

### nginx ters vekil (aynı origin + TLS)

`nginx-pve-gdrive.conf.example` dosyasını kullan:

```bash
apt install -y nginx
cp nginx-pve-gdrive.conf.example /etc/nginx/sites-available/pve.conf
ln -s /etc/nginx/sites-available/pve.conf /etc/nginx/sites-enabled/
nginx -t && systemctl reload nginx
```

Sonuç: `https://<host>/` Proxmox, `https://<host>/yedek/` yedek arayüzü. Proxmox'un kendi
sertifikası kullanılır. Bu kurulumda arayüzü dışarı kapat:

```json
{ "ui_bind": "127.0.0.1", "cookie_secure": true, "trust_proxy_header": true }
```

`trust_proxy_header` açıkken oturum ve kilitleme `X-Forwarded-For` başlığındaki gerçek
istemci adresini kullanır.

> Proxmox arayüzüne gerçek bir menü sekmesi eklemek `pvemanagerlib.js` yamalamayı gerektirir;
> her `pve-manager` güncellemesinde yama silinir. Yedekleme aracının sessizce kaybolması
> riskine değmez, bu yüzden depoda yer almıyor.

## 10. Güncelleme

```bash
cd pve-gdrive-backup && git pull
./install.sh                       # mevcut config ve planlara dokunmaz
systemctl restart pve-gdrive-ui
```

Arayüzde değişiklik yaptıysan (TypeScript kaynağı) önce derle:

```bash
cd ui && npm install && npm run build
```

Derleme yalnızca **geliştirme makinende** gerekir; sunucuya gömülü tek dosya gider.

## 11. Yedekten dönme

Drive'daki dosyayı indirip Proxmox'un dump klasörüne koy, arayüzden `Restore` yap:

```bash
rclone copy gdrive:proxmox-yedek/vzdump-qemu-105-2026_08_07-03_00_00.vma.zst \
  /mnt/pve/Usb1Tb/dump/ --progress
```

Yanlışlıkla silinen bir yedek çöp süresi dolmadıysa Drive arayüzünden geri alınabilir.

## 12. Sorun giderme

```bash
tail -f /var/log/pve-gdrive.log            # canlı log, plan adı köşeli parantezde
journalctl -u pve-gdrive-ui -f             # arayüz servisi
journalctl -u pve-gdrive-tick -n 50        # zamanlayıcı
pve_gdrive.py plans                        # planlar ve sonraki çalışma
pve_gdrive.py status | head -40            # durum JSON
```

| Belirti | Bak |
|---|---|
| `rclone bulunamadi` | `apt install -y rclone` |
| Arayüz açılmıyor | `systemctl status pve-gdrive-ui`, port firewall'da açık mı |
| Giriş yapamıyorum | Kilitlenmiş olabilir, `login_lockout_min` kadar bekle; log'da `GUVENLIK` satırına bak |
| Plan hiç çalışmıyor | Plan etkin mi, `weekdays` doğru mu, `systemctl status pve-gdrive-tick.timer` |
| Durum sürekli `⏸ ATLANDI` | vzdump uzun sürüyor: `vzdump_wait_min` artır veya plan saatini kaydır |
| `RETENTION ATLANDI` | Yükleme başarısız — koruma amaçlı. rclone hatasını çöz, kendiliğinden düzelir |
| Eski yedekler silinmiyor | `keep_count` tabanı devrede olabilir; dosya adları `dump_regex`'e uyuyor mu |
| Çöp boşalmıyor | Log'da `UYARI: ... Drive copunde kaldi` var mı; son çare `allow_account_cleanup` |
| Mail gitmiyor | Uygulama şifresi kullan; **✉ Mail → Test maili** ile dene, log'da `mail HATA` |
| Yükleme çok yavaş | Otomatik mod açıksa hat meşgul olabilir; log'da `bant genisligi ->` satırlarına bak |
