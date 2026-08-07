# pve-gdrive-backup

Proxmox VE `vzdump` yedeklerini **rclone** ile Google Drive'a yükler, gün bazlı kurallarla
temizler ve hepsini TypeScript ile yazılmış bir **web arayüzünden** yönetir.

```
Proxmox dump klasörü  ──copy──▶  Drive'da N gün  ──▶  Google çöpünde M gün  ──▶  kalıcı silinir
```

- **Sunucuda sıfır bağımlılık** — Python 3 stdlib + `rclone`. Node.js, Docker, paket kurulumu yok.
- **Çok planlı** — her plan kendi kaynağı, hedef Google hesabı, süreleri, saati ve mail alıcısıyla bağımsız
- **Veri güvenliği önce** — yükleme başarısızsa hiçbir yedek silinmez
- **Proxmox dostu** — kendi `vzdump` işinle çakışmaz, hattı başka yedekleme yazılımlarıyla paylaşır

## Belgeler

| Belge | İçerik |
|---|---|
| [docs/KURULUM.md](docs/KURULUM.md) | Adım adım kurulum, Google yetkilendirme, Proxmox entegrasyonu |
| [docs/OZELLIKLER.md](docs/OZELLIKLER.md) | Tüm özellikler ve ayarların tam listesi |
| [docs/GELISTIRME.md](docs/GELISTIRME.md) | TypeScript derleme, test paketi, mimari |
| [CHANGELOG.md](CHANGELOG.md) | Sürüm geçmişi ve ölçülmüş doğrulamalar |

## Hızlı başlangıç

```bash
apt install -y rclone                      # Proxmox host'ta
git clone https://github.com/hzkucuk/pve-gdrive-backup
cd pve-gdrive-backup && ./install.sh
```

Arayüz `http://<host-ip>:8787` — varsayılan `admin` / `degistir-beni`
(ilk açılışta şifre otomatik olarak pbkdf2 ile hash'lenir; **hemen değiştir**).

Sonra sırayla: **⚙ Ayarlar** → şifre · **👤 Hesaplar** → Google hesabı ekle ·
**✉ Mail** → SMTP profili · **+ Yeni Plan** → ilk planın.

Ayrıntı için [kurulum kılavuzuna](docs/KURULUM.md) bak.

## Ne yapar

**Yedekleme.** Proxmox'un ürettiği dump dosyalarını Drive'a kopyalar. Yedeği kendisi almaz —
Proxmox'un kendi zamanlayıcısı üretir, bu araç yaşam döngüsünü yönetir.

**Saklama.** `keep_days` günden eski setler Google çöp kutusuna gönderilir, `drive_trash_days`
sonra kalıcı silinir. `keep_count` bir güvenlik tabanıdır: misafir başına en yeni N set,
gün sınırına bakılmadan korunur — uzun süre yedeklenmeyen bir VM'in tüm yedekleri kaybolmaz.

**Çakışmama.** `vzdump` çalışırken yükleme başlamaz; yazılmakta olan dosyalar `--min-age` ve
desen dışlamalarıyla atlanır; yükleme başarısızsa retention hiç çalışmaz.

**Hat paylaşımı.** Sabit sınır, saatlik çizelge veya **otomatik mod**: hattaki diğer trafiği
ölçüp yükleme hızını çalışırken ayarlar. Sunucuda UrBackup gibi başka bir yedekleme varsa
o yüklerken geri çekilir, hat boşalınca hızlanır.

**Bildirim.** Çok profilli SMTP, plan başına gönderici seçimi, başarılı/hata/atlandı için ayrı
tercihler ve haftalık özet raporu (misafir bazında son yedek tarihi + uyarılar).

**Arayüz.** Canlı ilerleme (yüzde, hız, başlangıç, tahmini bitiş), klasör gezgini,
form doğrulama, ipuçları, hazır senaryolar, sistem/plan ayrı logları, F5 koruması.

**Güvenlik.** Oturum tabanlı giriş, SVG captcha, kaba kuvvet kilidi, CSRF koruması,
pbkdf2 şifre saklama, güvenlik başlıkları.

## Durum

Gerçek bir Proxmox VE 8.4 host'unda ve gerçek Google Drive hesabında uçtan uca doğrulandı:
yükleme, çöpe taşıma, çöpten kalıcı silme, canlı hız ayarı. Ayrıntılı ölçümler
[CHANGELOG.md](CHANGELOG.md) içinde.

## Lisans

MIT
