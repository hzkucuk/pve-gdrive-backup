# pve-gdrive-backup

Proxmox VE `vzdump` yedeklerini **rclone** ile Google Drive'a yükler, eski sürümleri
gün bazlı kurallarla temizler ve hepsini bir **web arayüzünden** yönetir.

- Ek Python bağımlılığı **yok** (Python 3 stdlib + `rclone`)
- **Çok planlı**: her plan kendi kaynak klasörü, hedefi, gün ayarları, saati ve mail alıcısıyla bağımsız
- Gün cinsinden her değer parametrik, tamamı arayüzden düzenlenebilir
- Tek dosya: `pve_gdrive.py` (CLI + zamanlayıcı + web UI)

> Bu araç yedeği kendisi **almaz**. Proxmox'un kendi yedekleme işi `/var/lib/vz/dump`
> gibi bir klasöre dosyaları üretir; bu araç onları Drive'a taşır ve yaşam döngüsünü yönetir.

## Yedeğin yaşam döngüsü

```
Proxmox dump klasörü
        │  rclone copy
        ▼
   Drive'da normal ────────── keep_days gün ──────────┐
   (en yeni keep_count set gün sınırından muaf)       │
        │                                             │
        ▼  rclone deletefile (varsayılan: çöpe)        │
   Google çöp kutusu ──────── drive_trash_days gün ────┘
        │
        ▼  --drive-trashed-only --drive-use-trash=false
   kalıcı silindi (kota boşalır)
```

Google çöp kutusundaki dosyalar **2TB kotandan yer kaplar**, bu yüzden çöp süresi
dolduğunda kalıcı silme yapılır. Süre dolmadan Drive arayüzünden geri yükleyebilirsin.

`keep_count` bir güvenlik tabanıdır: bir VM uzun süre yedeklenmese bile misafir başına
en yeni N set gün sınırına bakılmadan korunur, yani elinde hiç yedek kalmaz durumu oluşmaz.

## Proxmox'un kendi yedeğiyle çakışma

Proxmox `vzdump` ile yerel yedeği alırken kendi retention'ını da çalıştırır. Bu araç aynı
anda o klasörden Drive'a yükleme yapıyorsa iki taraf birbirini bozabilir. Dört ayrı koruma var:

**1. vzdump çalışırken yükleme başlamaz** — `wait_for_vzdump`

Üç bağımsız sinyalden herhangi biri yeterli: vzdump kilit dosyasının (`/run/vzdump.lock`)
kilitli olması, çalışan bir vzdump süreci, veya planın kaynak klasöründe şu an yazılan dosya
bulunması. `vzdump_wait_min` kadar beklenir; süre dolarsa **tur atlanır** (durum: `⏸ ATLANDI`)
ve sonraki 5 dakikalık kontrolde yeniden denenir — hiçbir şey silinmez.

> Süreç tespiti `pgrep -f` ile tam komut satırında arama yapar. `pgrep -x vzdump` işe yaramaz:
> vzdump bir perl betiğidir, süreç adı `perl` görünür.

**2. Yarım dosya yüklenmez** — `min_age_min` + `skip_patterns`

`rclone copy` yalnızca son `min_age_min` dakikadır değişmemiş dosyaları alır (`--min-age`) ve
`*.dat`, `*.tmp`, `*.part` gibi vzdump'ın yazarken kullandığı geçici dosyaları hiç görmez (`--exclude`).

Çöken bir yedekten kalan bayat `.dat` dosyası planı sonsuza kadar bloklamasın diye, "yazılıyor"
sayılması için dosyaya son `min_age_min` dakika içinde dokunulmuş olması gerekir.

**3. Yükleme başarısızsa hiçbir şey silinmez** — `prune_on_failure` (varsayılan kapalı)

En kritik koruma. `rclone copy` hata verirse veya Drive listelenemezse retention **hiç çalışmaz**,
log'a `RETENTION ATLANDI` düşer. Aksi halde yeni yedek Drive'a çıkmadan eskiler silinip
hem yerelde hem Drive'da yedeksiz kalma riski doğar.

**4. Çöp listelenemezse takip kaydı düşürülmez**

Kalıcı silme sonrası doğrulama listesi alınamazsa hiçbir kayıt "silindi" sayılmaz;
dosya sonraki çalışmada yeniden denenir. Silinmemiş dosyayı silinmiş sanmak yok.

Aynı plana ait iki çalışma `flock` ile zaten çakışamaz; farklı planlar birbirini beklemez
(her planın kendi kilidi var), o yüzden aynı kaynak klasörü iki plana vermekten kaçın.

## Bellek kullanımı

Uzun süre çalışan bir serviste bellek ve disk sınırsız büyümemeli, hepsi sınırlandı:

| Nerede | Nasıl sınırlandı |
|---|---|
| `rclone copy` çıktısı | Satır satır okunur, yalnızca son `rclone_tail_lines` (40) satır tutulur. 200.000 satırlık çıktıda ölçüm: **0.05 MB** (tümünü yakalayan yöntem: 39 MB) |
| rclone'un kendi RAM'i | `drive_chunk` × `transfers`. Varsayılan 64M × 2 = **~128 MB**. Yavaş bağlantıda düşür |
| `state.json` ve `/api/status` | Yedek/çöp satırları `snapshot_max_rows` (200) ile kırpılır; kartlardaki toplamlar tam liste üzerinden hesaplanıp ayrıca saklanır |
| Çalışma geçmişi | `history_max` (50) kayıt |
| Log dosyası | `log_max_mb` (5 MB) aşılınca döndürülür, `log_keep` (2) eski dosya saklanır. logrotate gerekmez |
| Drive listeleme | Her çalışmada tek `lsjson` çağrısı; sayım ve retention aynı listeyi kullanır |

## Gereksinimler

Proxmox VE (root erişimi) ve yapılandırılmış bir rclone Google Drive remote'u:

```bash
apt install -y rclone
rclone config        # yeni remote: ad "gdrive", tip "drive"
rclone lsd gdrive:   # test
```

## Kurulum

```bash
git clone <repo> && cd pve-gdrive-backup
./install.sh
```

Sonra `http://<host-ip>:8787` adresine gir (varsayılan `admin` / `degistir-beni`),
sağ üstten **⚙ Ayarlar** ile şifreyi ve SMTP bilgilerini gir, **+ Yeni Plan** ile ilk planını oluştur.

`install.sh` şunları yapar:

| Adım | Sonuç |
|---|---|
| Script kopyalar | `/usr/local/bin/pve_gdrive.py` |
| systemd birimleri | `pve-gdrive-ui.service`, `pve-gdrive-tick.timer` |
| Config oluşturur | `/etc/pve-gdrive.conf` (mod 600) |
| Durum dizini | `/var/lib/pve-gdrive` |
| Eski sürümü temizler | `pve-gdrive-backup.timer` (zamanlama artık plan bazlı) |

Eski tek planlı config otomatik göç eder: düz ayarlar "Varsayilan plan" olur,
`trash_grace_days` → `drive_trash_days` olarak taşınır.

## Zamanlama

`pve-gdrive-tick.timer` 5 dakikada bir `pve_gdrive.py tick` çağırır; hangi planın vakti
geldiğine kod karar verir. Böylece saat/gün değiştirmek için systemd dosyası düzenlemek
gerekmez — hepsi arayüzden ayarlanır.

- `run_at` — günün saati (`03:00`)
- `weekdays` — `[1..7]`, 1=Pazartesi. Boş bırakılırsa her gün.
- Kaçırılan çalışma (host kapalıydı vs.) açılışta telafi edilir.

## Plan ayarları

| Anahtar | Varsayılan | Açıklama |
|---|---|---|
| `name` / `enabled` | — | Plan adı; kapalıysa zamanlayıcı atlar |
| `src_dir` | `/var/lib/vz/dump` | Proxmox'taki dump klasörü (UI'dan gözat ile seçilir) |
| `remote` | `gdrive:proxmox-yedek` | rclone hedefi. **Her plan farklı klasöre yazmalı** |
| `keep_days` | `14` | Drive'da normal duracağı gün |
| `keep_count` | `3` | Misafir başına gün sınırından muaf en yeni set sayısı |
| `drive_trash_days` | `1` | Google çöp kutusunda bekleyeceği gün (0 = hemen kalıcı sil) |
| `run_at` / `weekdays` | `03:00` / her gün | Zamanlama |
| `bwlimit` / `transfers` / `checkers` / `drive_chunk` | `30M` / `2` / `4` / `128M` | Aktarım ayarları |
| `rclone_extra` | `[]` | Ham rclone argümanları, ör. `["--exclude","*.log"]` |
| `wait_for_vzdump` | `true` | Proxmox yedeği çalışırken yükleme başlatma |
| `vzdump_wait_min` | `60` | En fazla bu kadar dakika bekle, sonra turu atla (0 = hiç bekleme) |
| `min_age_min` | `10` | Sadece bu kadar dakikadır değişmemiş dosyaları yükle |
| `skip_patterns` | `["*.dat","*.tmp","*.part"]` | Yazılmakta olan dosyalar, hiç yüklenmez |
| `prune_on_failure` | `false` | Yükleme başarısızsa retention çalışsın mı. **Kapalı bırak** |
| `mail_to` / `notify_on` | — / `always` | Bildirim (`always` \| `failure` \| `never`) |

## Ortak ayarlar

| Anahtar | Varsayılan | Açıklama |
|---|---|---|
| `ui_bind` / `ui_port` | `0.0.0.0` / `8787` | Web arayüzü |
| `ui_user` / `ui_pass` | `admin` / `degistir-beni` | Basic Auth — **mutlaka değiştir** |
| `ui_refresh_sec` | `5` | Arayüzün kendini tazeleme aralığı |
| `smtp_*` / `mail_from` | — | Gmail için uygulama şifresi kullan |
| `browse_roots` | `/var/lib/vz`, `/mnt/pve`, `/mnt`, `/srv` | Klasör seçici bu köklerin dışına çıkamaz |
| `dump_regex` | vzdump kalıbı | Dosya adı deseni. Grup 1=set, 2=tip, 3=id, 4=tarih |
| `history_max` / `log_tail_lines` | `50` / `250` | Saklanan geçmiş / UI'da gösterilen log |
| `rclone_timeout_min` | `0` | rclone zaman aşımı, 0 = sınırsız |
| `rclone_tail_lines` | `40` | rclone çıktısından bellekte tutulan son satır sayısı |
| `snapshot_max_rows` | `200` | `state.json`'a yazılan azami yedek/çöp satırı |
| `log_max_mb` / `log_keep` | `5` / `2` | Log döndürme eşiği ve saklanan eski dosya sayısı |
| `allow_account_cleanup` | `false` | Aşağıya bak |

### `allow_account_cleanup` uyarısı

Kalıcı silme normalde yalnızca planın kendi remote yoluna uygulanır
(`--drive-trashed-only --drive-use-trash=false --include <dosya>`). Bu yöntem bir dosyayı
silemezse ve bu ayar açıksa `rclone cleanup` çalışır. **`rclone cleanup` path argümanı almaz,
Drive hesabındaki TÜM çöpü siler** — yedeklerle ilgisi olmayan, elle sildiğin dosyalar dahil.
Bu yüzden varsayılanı `false`. Kapalıyken silinemeyen dosya çöpte kalır ve sonraki
çalışmada yeniden denenir; log'a `UYARI` satırı düşer.

## Kullanım

```bash
pve_gdrive.py plans                 # planları ve sonraki çalışma zamanlarını listeler
pve_gdrive.py tick                  # vakti gelen planları çalıştırır (timer bunu çağırır)
pve_gdrive.py run --plan gunluk     # planı hemen çalıştır (--plan yoksa: tüm etkin planlar)
pve_gdrive.py serve                 # web arayüzü
pve_gdrive.py snapshot [--plan ID]  # Drive durumunu tazeler
pve_gdrive.py status                # durum JSON
pve_gdrive.py prune [--plan ID]     # sadece retention
pve_gdrive.py purgetrash [--plan ID]# sadece çöp temizliği
```

Farklı config ile: `PVE_GDRIVE_CONF=/yol/test.conf pve_gdrive.py plans`

## Proxmox arayüzünden erişim

Proxmox'un resmi bir eklenti API'si yok, o yüzden üç seçenek var:

**1. Datacenter → Notes'a link (önerilen, upgrade-güvenli)**

```bash
./proxmox-link.sh              # veya: ./proxmox-link.sh 10.0.0.5
```

Proxmox'un not alanı Markdown render eder. Proxmox arayüzünde `Datacenter → Notes`
altında tıklanabilir bir link çıkar. Proxmox'un hiçbir dosyasına dokunulmaz, güncellemede bozulmaz.

**2. Aynı origin + TLS (nginx ters vekil)**

`nginx-pve-gdrive.conf.example` dosyasını kullan: `https://<host>/` Proxmox,
`https://<host>/yedek/` yedek arayüzü olur. Tek port, Proxmox'un kendi sertifikası,
düz HTTP yok. Bu durumda `ui_bind` değerini `127.0.0.1` yapıp UI'ı dışarı kapat.

**3. Arayüzün içine gömmek (önerilmez)**

Proxmox arayüzüne gerçek bir menü sekmesi eklemek `/usr/share/pve-manager/js/pvemanagerlib.js`
dosyasını yamalamayı gerektirir. Her `pve-manager` güncellemesinde yama silinir ve
tekrar uygulanması gerekir. Kırılgan olduğu için bu depoda yer almıyor.

## Güvenlik

Arayüz düz HTTP ve Basic Auth kullanır; şifre her istekte base64 ile gider. Yalnızca
güvendiğin ağda aç. Dışarıdan erişeceksen VPN (WireGuard/Tailscale) veya yukarıdaki
nginx + TLS kurulumunu kullan, ya da `ui_bind: 127.0.0.1` yapıp SSH tüneli aç:

```bash
ssh -N -L 8787:127.0.0.1:8787 root@<host>    # sonra: http://localhost:8787
```

Klasör seçici yalnızca `browse_roots` altını gösterir, dışına çıkma denemesi kök dizine geri çekilir.
Config dosyası SMTP ve UI şifresi içerdiği için `chmod 600`.

## Sorun giderme

```bash
tail -f /var/log/pve-gdrive.log            # canlı log (plan adı köşeli parantezde)
journalctl -u pve-gdrive-ui -f             # arayüz servisi
journalctl -u pve-gdrive-tick -n 50        # zamanlayıcı
systemctl list-timers | grep pve-gdrive    # sonraki tetikleme
pve_gdrive.py plans                        # planlar ve sonraki çalışma
rclone lsjson gdrive:proxmox-yedek --drive-trashed-only   # çöpte ne var
```

| Belirti | Bak |
|---|---|
| `rclone bulunamadi` | `apt install -y rclone` |
| Plan hiç çalışmıyor | Plan etkin mi, `weekdays` doğru mu, `systemctl status pve-gdrive-tick.timer` |
| Yedek listesi boş | `remote` yolu doğru mu, `rclone lsd gdrive:` çalışıyor mu |
| Eski yedekler silinmiyor | Dosya adları `dump_regex`'e uyuyor mu; `keep_count` tabanı devrede olabilir |
| Durum sürekli `⏸ ATLANDI` | vzdump gerçekten uzun sürüyor olabilir: `vzdump_wait_min` artır veya plan saatini yedek penceresinin dışına al |
| `RETENTION ATLANDI` görüyorum | Yükleme başarısız olmuş — bu koruma amaçlı. Log'da rclone hatasına bak, düzelince retention kendiliğinden çalışır |
| Çöp boşalmıyor | Log'da `UYARI: ... Drive copunde kaldi` var mı; `allow_account_cleanup` |
| Mail gitmiyor | Gmail uygulama şifresi kullan; log'da `mail HATA` satırı |
| UI açılmıyor | Port firewall'da açık mı, `systemctl status pve-gdrive-ui` |
