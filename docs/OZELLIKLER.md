# Özellikler ve Ayarlar

Tüm ayarlar `/etc/pve-gdrive.conf` içindedir ve arayüzden düzenlenebilir.
Sabit kodlanmış değer bırakılmamıştır.

## Plan ayarları

Her plan bağımsızdır: kendi kaynağı, hedef Google hesabı, süreleri, saati ve mail alıcısı vardır.

### Kimlik

| Anahtar | Varsayılan | Açıklama |
|---|---|---|
| `id` | addan türetilir | Log ve API'de kullanılan kimlik |
| `name` | — | Görünen ad, mail konusunda çıkar |
| `enabled` | `true` | Kapalıysa zamanlayıcı atlar; elle çalıştırılabilir |

### Kaynak ve hedef

| Anahtar | Varsayılan | Açıklama |
|---|---|---|
| `src_dir` | `/var/lib/vz/dump` | Proxmox dump klasörü. Arayüzde gözat ile seçilir |
| `remote` | `gdrive:proxmox-yedek` | `hesap:klasör`. **Her plan farklı klasöre yazmalı** |

### Saklama

| Anahtar | Varsayılan | Açıklama |
|---|---|---|
| `keep_days` | `14` | Drive'da normal duracağı gün. Eskiler çöpe gider |
| `keep_count` | `3` | VM/CT başına gün sınırından muaf en yeni set sayısı |
| `drive_trash_days` | `1` | Google çöp kutusunda bekleme. `0` = hemen kalıcı sil |

`keep_days` ve `keep_count` birlikte çalışır: bir set **ya** gün sınırı içindeyse **ya da**
misafirin en yeni `keep_count` seti arasındaysa korunur. İkisi birden `0` olamaz.

### Zamanlama

| Anahtar | Varsayılan | Açıklama |
|---|---|---|
| `run_at` | `03:00` | Günün saati |
| `weekdays` | `[]` | `1..7`, 1=Pazartesi. Boş = her gün |

Zamanlama systemd'de değil uygulamadadır: `pve-gdrive-tick.timer` 5 dakikada bir bakar,
hangi planın vaktinin geldiğine kod karar verir. Kaçırılan çalışma telafi edilir.

### Proxmox ile çakışma koruması

| Anahtar | Varsayılan | Açıklama |
|---|---|---|
| `wait_for_vzdump` | `true` | Proxmox yedeği çalışırken yükleme başlatma |
| `vzdump_wait_min` | `60` | En fazla bekleme; dolarsa tur atlanır. `0` = hemen atla |
| `min_age_min` | `10` | Sadece bu kadar dakikadır değişmemiş dosyaları yükle |
| `skip_patterns` | `["*.dat","*.tmp","*.part"]` | Yazılmakta olan dosyalar, hiç yüklenmez |
| `prune_on_failure` | `false` | Yükleme başarısızsa retention çalışsın mı. **Kapalı bırak** |

vzdump tespiti üç bağımsız sinyalle yapılır: `/run/vzdump.lock` kilidi, `pgrep -f` ile süreç
araması (vzdump bir perl betiği olduğu için `-x` işe yaramaz), ve planın kendi kaynak
klasöründe **yakın zamanda dokunulmuş** geçici dosya olması. Çöken bir yedekten kalan bayat
`.dat` dosyası planı sonsuza kadar bloklamaz.

### Aktarım ve bant genişliği

| Anahtar | Varsayılan | Açıklama |
|---|---|---|
| `bwlimit` | `30M` | Sabit hız sınırı. `off` = sınırsız |
| `bwlimit_schedule` | `""` | Saatlik çizelge, ör. `08:00,2M 19:00,30M 23:00,off` |
| `bwlimit_upload_only` | `true` | Sınır yalnızca yüklemeye uygulanır |
| `transfers` | `2` | Eşzamanlı transfer |
| `checkers` | `4` | Karşılaştırma işçisi |
| `drive_chunk` | `64M` | Drive parça boyutu. **RAM ≈ parça × transfer** |
| `rclone_extra` | `[]` | Ham rclone argümanları |

#### Otomatik bant genişliği

Sunucuda başka bir yedekleme yazılımı (UrBackup gibi) varsa sabit sınır yetmez.
Otomatik mod hattaki **diğer** trafiği ölçüp kendi hızını çalışırken ayarlar.

| Anahtar | Varsayılan | Açıklama |
|---|---|---|
| `bwlimit_auto` | `false` | Otomatik mod. Açıkken sabit sınır ve çizelge devre dışı |
| `bw_auto_link` | `100M` | Hattın toplam **yükleme** kapasitesi (bayt/sn) |
| `bw_auto_reserve_pct` | `30` | Hattın bu yüzdesi hep diğerlerine bırakılır |
| `bw_auto_min` | `1M` | Hat meşgulken inilecek taban |
| `bw_auto_max` | `""` | Tavan. Boşsa `bwlimit` tavan olur |
| `bw_auto_iface` | `""` | Ölçülecek arayüz. Boş = varsayılan rota arayüzü |
| `bw_auto_interval_sec` | `10` | Ölçüm ve ayar sıklığı |
| `bw_auto_smooth` | `0.4` | Ölçüm yumuşatma katsayısı (0-1). Düşük = daha sakin |
| `bw_auto_step_pct` | `25` | Bu yüzdeden küçük değişiklikler uygulanmaz |

Nasıl çalışır: `/proc/net/dev`'den arayüzün giden bayt sayacı örneklenir, rclone'un kendi
hızı çıkarılır, kalan "diğer trafik" olarak alınır. Hedef sınır
`link × (1 − reserve%) − diğer` olarak hesaplanıp taban/tavan arasına sıkıştırılır ve
`rclone rc core/bwlimit` ile **yükleme kesilmeden** uygulanır.

Kendi hızımız rclone'un bildirdiği *ortalama* hızdan değil, aktarılan bayt sayacının
farkından anlık olarak hesaplanır — ortalama geriden geldiği için kendi trafiğimizi
"başkasının" sanıp salınım yapıyorduk. Ölçüm ayrıca üstel hareketli ortalamayla
yumuşatılır (`bw_auto_smooth`) ve `bw_auto_step_pct`'ten küçük değişiklikler uygulanmaz.

Proxmox'ta köprü arayüzü (`vmbr0`) yalnızca host trafiğini görebilir; VM ve CT trafiğini de
saymak için fiziksel veya bond arayüzünü seç.

### Bildirim

| Anahtar | Varsayılan | Açıklama |
|---|---|---|
| `smtp_profile` | `""` | Gönderici profili. Boş = ilk profil |
| `mail_to` | `""` | Alıcı. Virgülle birden fazla |
| `notify_success` | `true` | Başarılı bitince mail |
| `notify_failure` | `true` | Hata alınca mail |
| `notify_skipped` | `false` | vzdump çakışması yüzünden atlanınca mail |

Çalışma maili şunları içerir: özet sayıları, yapılandırma, Drive durumu ve kota,
VM/CT bazında son yedek tarihi, en yeni yedekler, çöpte bekleyenler ve uyarılar.

### Haftalık rapor

| Anahtar | Varsayılan | Açıklama |
|---|---|---|
| `weekly_report` | `true` | Haftalık özet raporu |
| `report_day` | `1` | 1=Pazartesi .. 7=Pazar |
| `report_at` | `09:00` | Gönderim saati |
| `report_days` | `7` | Raporun kapsadığı dönem |
| `report_stale_days` | `2` | Bu kadar gündür başarılı yedek yoksa uyar |
| `report_quota_warn` | `90` | Kota bu yüzdeyi aşarsa uyar |
| `report_mail_to` | `""` | Boş = `mail_to` |

Rapor içeriği: dönem içi çalışma sayıları (başarılı/hata/atlandı), yüklenen ve silinen dosya
toplamları, Drive kotası, **VM/CT bazında son yedek tarihi ve yaşı**, kaynakta olup Drive'a
çıkmamış VM/CT'ler, ve uyarı listesi. Rapor planlar kapalıyken de gider — "hiç çalışmıyor"
uyarısının ulaşması için.

## Ortak ayarlar

### Arayüz ve güvenlik

| Anahtar | Varsayılan | Açıklama |
|---|---|---|
| `ui_bind` / `ui_port` | `0.0.0.0` / `8787` | Dinlenen adres ve port |
| `ui_user` / `ui_pass` | `admin` / — | Şifre pbkdf2_sha256, 200.000 tur ile saklanır |
| `ui_refresh_sec` | `5` | Arayüz tazeleme aralığı (çalışan plan varken 2 sn'ye iner) |
| `api_token` | `""` | Otomasyon için `Authorization: Bearer <token>` |
| `session_timeout_min` | `120` | Hareketsizlik süresi |
| `session_absolute_h` | `24` | Oturumun azami ömrü |
| `login_max_attempts` | `5` | Bu kadar hatalı denemeden sonra kilit |
| `login_lockout_min` | `15` | Kilit süresi |
| `captcha_enabled` | `true` | Giriş ekranında SVG captcha |
| `captcha_after_fails` | `0` | `0` = her giriş, `2` = 2 hatadan sonra |
| `ssl_cert` / `ssl_key` | Proxmox sertifikası | Doluysa arayüz **HTTPS** çalışır. Yüklenemezse HTTP'ye düşer, log'a sebep yazar |
| `allow_networks` | `[]` | Arayüze yalnızca bu ağlardan erişilir. Boş = kısıtlama yok |
| `cookie_secure` | `false` | TLS açıkken zaten zorunlu tutulur |
| `trust_proxy_header` | `false` | nginx arkasındaysa `true` (`X-Forwarded-For`) |

### HTTPS

`ssl_cert` ve `ssl_key` doluysa arayüz doğrudan HTTPS konuşur — ters vekil veya ek paket
gerekmez, Python'un kendi `ssl` modülü kullanılır. Varsayılan Proxmox'un kendi sertifikasıdır
(`/etc/pve/local/pve-ssl.pem`), yani tarayıcı uyarısı Proxmox arayüzüyle aynıdır.

Sertifika okunamazsa servis **düz HTTP ile ayakta kalır** ve log'a sebebi yazar; hatalı bir
yol yüzünden arayüze erişim kaybedilmez. TLS açıkken çerez otomatik olarak `Secure`
işaretlenir ve `Strict-Transport-Security` başlığı gönderilir.

### Ağ kısıtlaması

`allow_networks` doluysa yalnızca listedeki ağlardan gelen istekler işlenir, diğerleri
**403** alır ve log'a `GUVENLIK` satırı düşer. Kontrol uygulamanın içindedir: firewall
kurmak gerekmez, **SSH ve Proxmox arayüzü bu ayardan etkilenmez**, yanlış yazılırsa config
dosyasından geri alınır.

```json
{ "allow_networks": ["10.212.134.0/24"] }
```

Ters vekil arkasındaysan `trust_proxy_header` açık olmalı, yoksa tüm istekler vekilin
IP'sinden geliyor görünür.

Oturum çerezi `HttpOnly` ve `SameSite=Strict`'tir, IP'ye bağlanır. Tüm POST istekleri CSRF
jetonu ister. Yanıtlarda `X-Frame-Options`, `X-Content-Type-Options`, `Referrer-Policy` ve
`Content-Security-Policy` başlıkları gönderilir.

### Bellek ve dayanıklılık

| Anahtar | Varsayılan | Açıklama |
|---|---|---|
| `rclone_tail_lines` | `40` | rclone çıktısından bellekte tutulan satır |
| `snapshot_max_rows` | `200` | Durum dosyasına yazılan azami satır (toplamlar tam kalır) |
| `history_max` | `50` | Plan başına çalışma geçmişi |
| `log_tail_lines` | `250` | Arayüzde gösterilen log satırı |
| `log_max_mb` / `log_keep` | `5` / `2` | Log döndürme eşiği ve saklanan dosya |
| `rclone_timeout_min` | `0` | rclone zaman aşımı, `0` = sınırsız |
| `stats_interval_sec` | `5` | İlerleme bildirim sıklığı |
| `purge_batch` | `50` | Çöp temizliğinde tek çağrıda silinecek dosya |
| `purge_timeout_min` | `30` | Çöp temizliği zaman aşımı |

rclone çıktısı satır satır okunur ve yalnızca son N satır tutulur — 200.000 satırlık çıktıda
ölçülen bellek artışı 0.05 MB (tümünü yakalayan yöntemde 39 MB).

### Diğer

| Anahtar | Varsayılan | Açıklama |
|---|---|---|
| `browse_roots` | `/var/lib/vz`, `/mnt/pve`, `/mnt`, `/srv` | Klasör seçici bu köklerin dışına çıkamaz |
| `dump_regex` | vzdump kalıbı | Grup 1=set, 2=tip, 3=id, 4=tarih |
| `allow_account_cleanup` | `false` | Aşağıya bak |
| `smtp_profiles` | `[]` | Gönderici profilleri |

#### `allow_account_cleanup`

Kalıcı silme normalde yalnızca planın kendi remote yoluna uygulanır
(`--drive-trashed-only --drive-use-trash=false --include ...`). Bu yöntem gerçek Drive'da
doğrulandı ve normalde yeterlidir. Bir dosya böyle silinemezse ve bu ayar açıksa
`rclone cleanup` çalışır — **bu komut yol argümanı almaz, Drive'daki tüm çöpü siler.**
Hesap izni `drive.file` ise etkisi bu araca ait dosyalarla sınırlı kalır, yine de varsayılan `false`.

## Komut satırı

```bash
pve_gdrive.py plans                 # planlar ve sonraki çalışma zamanları
pve_gdrive.py tick                  # vakti gelenleri çalıştır (timer bunu çağırır)
pve_gdrive.py run [--plan ID]       # hemen çalıştır
pve_gdrive.py serve                 # web arayüzü
pve_gdrive.py snapshot [--plan ID]  # Drive durumunu tazele
pve_gdrive.py status                # durum JSON
pve_gdrive.py prune [--plan ID]     # sadece retention
pve_gdrive.py purgetrash [--plan ID]# sadece çöp temizliği
```

```bash
pve_gdrive.py version                 # sürüm
pve_gdrive.py update --check          # yeni sürüm var mı
pve_gdrive.py update                  # güncellemeyi kur
pve_gdrive.py update --rollback       # önceki sürüme dön
pve_gdrive.py aglar                   # izinli ağları göster
pve_gdrive.py aglar --ekle 10.0.0.0/24 # ağ ekle
pve_gdrive.py aglar --ac              # kısıtlamayı kaldır (kilitlenme kurtarması)
pve_gdrive.py disa-aktar              # plan/mail ayarlarını JSON olarak yaz
pve_gdrive.py disa-aktar --sirlarla   # SMTP şifreleri dahil
pve_gdrive.py ice-aktar < ayar.json   # ayar yükle (planlar kapalı gelir)
```

`PVE_GDRIVE_CONF` ortam değişkeniyle farklı config kullanılabilir.

## HTTP API

Oturum çerezi veya `Authorization: Bearer <api_token>` ile. POST istekleri `X-CSRF-Token` ister
(Bearer kullanımında gerekmez).

| Uç | Yöntem | İş |
|---|---|---|
| `/api/status` | GET | Planlar, durum, ayarlar, CSRF jetonu |
| `/api/log?src=all\|system\|<plan>` | GET | Log satırları |
| `/api/browse?path=` | GET | Klasör gezgini |
| `/api/storages` | GET | Proxmox yedek depoları |
| `/api/ifaces` | GET | Ağ arayüzleri ve sayaçları |
| `/api/remotes[?quota=1]` | GET | Google hesapları |
| `/api/action?do=&plan=` | POST | `backup`, `prune`, `purgetrash`, `refresh`, `report`, `testmail` |
| `/api/plan/save`, `/api/plan/delete` | POST | Plan yönetimi |
| `/api/smtp/save`, `/api/smtp/delete`, `/api/smtp/test` | POST | Mail profilleri |
| `/api/remote/add`, `/api/remote/delete`, `/api/remote/test` | POST | Hesap yönetimi |
| `/api/remote/auth/start\|status\|finish\|cancel` | POST/GET | OAuth akışı |
| `/api/settings/save` | POST | Ortak ayarlar |
| `/login`, `/logout` | POST | Oturum |

## Host yapılandırma yedeği

`vzdump` yalnızca disk yedeği alır. Bu arşiv, "diskleri nereye geri yükleyeceğim"
sorusunu cevaplar. Ölçülen boyut: 38 dosya, tar.gz 7 KB + JSON 15 KB.

| Anahtar | Varsayılan | Açıklama |
|---|---|---|
| `host_config_enabled` | `true` | Her plan çalışmasında tarihli arşiv üretilir |
| `host_config_json` | `true` | `pvesh` ile REST ağacının okunabilir görüntüsü |
| `host_config_keep_count` | `30` | Gün sınırından muaf arşiv tabanı |
| `host_config_paths` | `/etc/pve`, `/etc/network/interfaces`, `/etc/fstab`, … | Arşive girecek yollar |
| `host_config_exclude` | `*.key`, `*.pem`, `shadow.cfg`, … | Desen tabanlı dışlama |
| `host_config_priv_allow` | `authorized_keys`, `known_hosts` | `priv/` içinden **yalnızca** bunlar geçer |
| `host_config_pvesh` | 11 uç | JSON görüntüsüne girecek API yolları |

`/etc/pve/priv` **varsayılan olarak yasaktır**; izin listesiyle çalışır. Böylece
Proxmox oraya yeni bir sır koyarsa kural güncellenmese bile dışarıda kalır.
Geri yükleme adımları ve eksik anahtarların ne anlama geldiği: `docs/GERI-YUKLEME.md`.

## Bant genişliği

| Anahtar | Varsayılan | Açıklama |
|---|---|---|
| `bwlimit` | `30M` | Sabit sınır (bayt/sn; `30M` = 30 MiB/sn) |
| `bwlimit_schedule` | — | Saatlik çizelge: `08:00,2M 19:00,off` |
| `bwlimit_auto` | `false` | Hattaki diğer trafiğe göre canlı ayar |
| `bw_auto_link_mode` | `ogren` | `ogren` = fiilen ölçülen kapasite, `manuel` = elle |
| `bw_auto_link` | `100M` | Yalnızca `manuel` kipte kullanılır |
| `bw_auto_iface` | — | Boşsa otomatik: varsayılan rota, köprüyse altındaki uplink |
| `bw_auto_reserve_pct` | `30` | Hattın bu yüzdesi her zaman diğerlerine bırakılır |

**Neden köprü değil uplink:** Proxmox'ta varsayılan rota `vmbr0` gibi bir köprüden
geçer. Köprünün sayaçları VM↔VM yerel trafiği de sayar; o trafik internete hiç
çıkmaz ve yükleme hızıyla yarışmaz. Ölçüm (aynı an): `bond0` 12,2 KB/sn,
`vmbr0` 19,1 KB/sn.

**Neden bağ hızı kullanılmaz:** arayüzün bildirdiği hız internet yükleme hızını
göstermez — 4×1 Gbit bond'un arkasında 60 Mbit'lik bir ISS hattı olabilir.

## Canlı olay akışı (SSE)

| Anahtar | Varsayılan | Açıklama |
|---|---|---|
| `sse_enabled` | `true` | Kapatılırsa arayüz yoklama moduna döner |
| `sse_watch_ms` | `1000` | Diskteki değişikliğin taranma sıklığı |
| `sse_heartbeat_sec` | `20` | Ters vekil bağlantıyı kesmesin diye boş sinyal |
| `sse_ping_sec` | `5` | Kopan bağlantının fark edilme süresi |
| `sse_max_clients` | `16` | Eş zamanlı açık akış sınırı |

## Servis izleme

| Anahtar | Varsayılan | Açıklama |
|---|---|---|
| `failure_mail` | `true` | systemd birimi çökünce mail at |
| `failure_mail_to` | — | Boşsa ilk planın alıcısı kullanılır |
| `failure_smtp_profile` | — | Boşsa planın profili |
| `failure_mail_lines` | `40` | Maile eklenecek günlük satırı |
| `tick_uyari_dk` | `20` | Bu kadar dakikadır tick gelmediyse uyar |

## Oturum güvenliği

| Anahtar | Varsayılan | Açıklama |
|---|---|---|
| `remember_enabled` | `true` | Giriş ekranında "beni hatırla" |
| `remember_days` | `30` | Hatırlanan oturumun ömrü |
| `session_ip_bind` | `ip` | `ip` = birebir adres, `ag` = aynı ağ bloğu, `yok` = kontrol yok |

Hatırlanan oturumlar `0600` izinli bir dosyada saklanır ve servis yeniden
başladığında geri yüklenir. `session_ip_bind` yalnızca hatırlanan oturumlara
uygulanır; normal oturum her zaman birebir adrese bağlıdır.

## Yedek hedefler (çoklu hesap)

Bir plan birden fazla hedefe sahip olabilir. Birincil hedefe yazamazsa sırayla
yedekler denenir; ilk başarılı olan kullanılır.

| Anahtar | Varsayılan | Açıklama |
|---|---|---|
| `remote` | — | Birincil hedef, `hesap:klasör` |
| `yedek_hedefler` | `[]` | Sırayla denenecek yedekler. Birincilin tekrarı ve boş/geçersiz girdiler atılır |

**Silme davranışı — projenin en önemli güvenlik kuralının uzantısı:**

> Retention **yalnızca yüklemenin gerçekten başarılı olduğu hedefte** çalışır.

Yedek hedefe düştüğün gün birincideki eski yedeklere **dokunulmaz**. Hesap
düzeldiğinde orada duruyor olurlar. Bütün hedefler başarısız olursa hiçbir yerde
silme yapılmaz ve çalışma `HATA` ile biter.

Yedek hedef **farklı bir hesapta** olmalı; aynı hesabın başka klasörü hesap
kilitlendiğinde işe yaramaz. Arayüz bunu fark edip uyarır.

Yedek hedefe düşüldüğünde çalışma maili `HEDEFLER` bölümü ve bir uyarı içerir;
plan kartında "Son yazılan" satırı görünür.

## Sağlayıcılar

Hesaplar tarayıcı ile (OAuth) yetkilendirilir. Liste, hedef kurulumdaki
rclone 1.60.1 üzerinde tek tek denenerek çıkarıldı:

| Sağlayıcı | `rclone authorize` | Gerçek hesapla test |
|---|---|---|
| Google Drive | ✔ | **✔ uçtan uca doğrulandı** |
| Dropbox | ✔ | denenmedi |
| OneDrive | ✔ | denenmedi — bazı kurumsal hesaplar `drive_id`/`drive_type` ister |
| Box | ✔ | denenmedi |
| pCloud | ✔ | denenmedi |
| Yandex Disk | ✔ | denenmedi |
| Citrix ShareFile | ✔ | denenmedi |
| HiDrive | ✔ | denenmedi |

Denenmemiş sağlayıcılar arayüzde **(denenmedi)** olarak işaretlidir ve seçince
uyarı çıkar: OAuth akışı çalışıyor ama yükleme/saklama davranışı gerçek bir
hesapla ölçülmedi. Önce küçük bir planla dene.

`mega` ve `koofr` OAuth kullanmaz (kullanıcı adı/parola), `jottacloud` ve `zoho`
farklı bir akış ister — listede yoktur.

rclone'un tanımadığı bir sağlayıcı listede pasif görünür. Bu tespit
**açık tarafa** düşer: `rclone config providers` çıktısı okunamazsa hiçbir
sağlayıcı gizlenmez, çünkü bir tespit hatası çalışan sağlayıcıyı saklamamalı.
