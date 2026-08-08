# Değişiklik Günlüğü

Bu projede yapılan tüm iddialar ölçülerek doğrulanmıştır; her sürümde nasıl doğrulandığı yazılıdır.

## 1.3.1 — 2026-08-08

### rclone hesap yazımı doğrulanıyor

Bir hesap eklendi, `rclone` çıkış kodu 0 döndü, log **"hesap eklendi"** yazdı —
ve hesap yapılandırma dosyasında yoktu. Saatler sonra fark edildi; hangi yazmanın
düşürdüğü geriye dönük kanıtlanamadı çünkü hiçbir kopya yoktu.

- `remote_create` artık çıkış kodunu kanıt saymıyor: yazdıktan sonra dosyadan
  geri okuyup hesabın gerçekten göründüğünü doğruluyor. Görünmüyorsa **hata**
  dönüyor ve `RCLONE_CONFIG` yolunu loga yazıyor. `remote_delete` de aynı
  şekilde silindiğini doğruluyor.
- `rclone.conf`, değiştiren her işlemden **önce** zaman damgalı olarak
  kopyalanıyor (`/var/lib/pve-gdrive/rclone-yedek/`, `0600`, dizin `0700`).
  Kaç kopya tutulacağı: `rclone_conf_yedek_tut` (varsayılan 20).

Test: 65 → 67.

## 1.3.0 — 2026-08-08

Bu sürümün büyük kısmı, gerçek kurulumda ortaya çıkan sorunların düzeltilmesidir.

### Canlı arayüz

- **Sunucu → tarayıcı olay akışı (SSE).** Arayüz artık birkaç saniyede bir
  `/api/status` çekmiyor; durum, ilerleme ve log satırları değiştiği anda düşüyor.
  WebSocket yerine SSE: sunucu saf Python stdlib, `EventSource` kendiliğinden
  yeniden bağlanıyor ve ters vekilden sorunsuz geçiyor. Akış kurulamazsa arayüz
  eski yoklama moduna dönüyor; başlıktaki gösterge hangi modda olduğunu söylüyor.
- **Sağ tık menüleri.** Plan kartı, Google hesabı, SMTP profili, log paneli,
  yedek/çöp tablo satırları ve boş alan için bağlama duyarlı menüler. Klavyeyle
  gezilebilir, ekran dışına taşmaz, dokunmatikte uzun basmayla açılır.
- **F5 taslağı.** Açık plan formu aralıklarla yerel taslağa yazılıyor; dönünce
  geri yükleme öneriliyor. Şifre alanları taslağa girmez, sunucuya hiçbir şey gitmez.
- **Form hataları** ilk hatalı alana kaydırıyor, odağı veriyor ve kısa süre vurguluyor.
- Hiç çalışmamış plan artık `ATLANDI` değil **`BEKLİYOR`** gösteriyor.

### Host yapılandırma yedeği

`vzdump` yalnızca disk yedeği alır. Host çökerse elinde diskler olur ama onları
nereye geri yükleyeceğini anlatan hiçbir şey olmaz. Her çalışmada tarihli bir
arşiv üretilip yükleniyor: `/etc/pve`, `/etc/network/interfaces`, `/etc/fstab`,
apt kaynakları. Ölçüm: **38 dosya, tar.gz 7 KB + JSON 15 KB.**

**Özel anahtarlar alınmaz.** `/etc/pve/priv` varsayılan olarak yasak, izin
listesiyle çalışır; yalnızca `authorized_keys` ve `known_hosts` (açık anahtar
listeleri) girer. Ayrıntı ve geri yükleme adımları: `docs/GERI-YUKLEME.md`.

### Bant genişliği

- **Ölçüm yanlış arayüzden yapılıyordu.** Varsayılan rota Proxmox'ta `vmbr0`'dan
  geçer; köprünün sayaçları VM↔VM yerel trafiği de sayar ve o trafik internete hiç
  çıkmaz. Ölçüldü (aynı an): `bond0` 12,2 KB/sn, `vmbr0` 19,1 KB/sn. Artık
  köprünün altındaki uplink seçiliyor — önce bond, sonra fiziksel.
- **Hat kapasitesi artık tahmin değil ölçüm.** Arayüzün bağ hızı internet yükleme
  hızını göstermez; 4×1 Gbit bond'un arkasında 60 Mbit'lik hat olabilir. Öğrenme
  kipinde, kendi sınırına dayanılmayan anlarda fiilen ulaşılan en yüksek sürekli
  hız ölçülüp kalıcı yazılıyor.

### Servis izleme

- `pve-gdrive-bildir@.service` ve iki birimde `OnFailure=`. Çöken birimin systemd
  durumu ve son 40 günlük satırı maile giriyor.
- Her tick "yaşıyorum" damgası bırakıyor; damga eskirse arayüzde uyarı çıkıyor ve
  haftalık rapora giriyor. Yeni komut: `pve_gdrive.py saglik`.

### Mail

Düz metin gövde korunuyor, üstüne **Outlook uyumlu HTML** alternatif üretiliyor:
tablo tabanlı, satır içi CSS, `mso` koşullu bloğu, flex/grid yok. Durum rengi
başarılı/HATA/atlandı'ya göre değişiyor.

### Düzeltmeler

- **"Beni hatırla" çalışmıyordu.** Oturumlar yalnızca bellekteydi; her servis
  yeniden başlatması herkesi çıkış yaptırıyordu. Kalıcı oturumlar artık 0600 izinli
  dosyada saklanıp açılışta geri yükleniyor. Oturum adres bağlama kipi parametrik:
  `ip | ag | yok`.
- **Rapor butonu çalışmıyordu** — `run_action("report")` sunucuda hiç tanımlı değildi.
- **Arayüzde ham `C(...)` çağrıları görünüyordu** (`C(3 gün · min 3 set`, Drive
  kullanım satırı, tırnaksız `title=` öznitelikleri). Eski bir toplu kelime
  değiştirmenin kalıntısıydı; temizlendi ve testle korumaya alındı.
- **Saklama ipuçları sabit "14 gün" diyordu**, alanda 3 yazarken bile. Artık
  girilen değerden türüyor.
- Mail gövdelerinde "MISAFIR" → "VM/CT".

### Dağıtım

`dagit.sh`: hedef yolu systemd biriminin `ExecStart`'ından okur, doğrulamayı çalışan
sürecin `/proc/<pid>/cmdline` ile açtığı dosyanın sha256'sı üzerinden yapar, eski
sürümü saklar. (Bir gün boyunca yanlış dosyaya dağıtım yapılıp "doğrulandı" denmesi
bu betiğin sebebidir.)

### Test

42 → **64**. Yeni gruplar: `canli`, `izleme`, `bantgenisligi`, `yapilandirma`.
Test koşucusuna gerçek HTTP sunucusu başlatan yardımcı eklendi; SSE uçtan uca
gerçek bağlantı üzerinden ölçülüyor.

## 1.2.0 — 2026-08-08

### Eklenenler

- **Plan sihirbazı.** Yeni plan 8 adımda kurulur (Plan → Kaynak → Hedef → Saklama →
  Zamanlama → Aktarım → Bildirim → Özet). Google hesabı yetkilendirmesi **3. adımın
  içindedir**; ayrı ekrana gitmek gerekmez. Hiçbir şey adım adım kaydedilmez, son adımda
  özet onaylanır. Mevcut planı düzenlerken tek sayfa form açılır.
- **Kapasite planlayıcı.** Kaynak klasör ölçülüp seçilen hesabın kotasına göre projeksiyon
  gösterilir: günlük üretim, seçilen saklama süresiyle gereken alan, kota doluluğu ve
  misafir dağılımı. "Önerilen süreyi uygula" düğmesi boş alanın güvenli bir kısmına
  (`oneri_pay_pct`, varsayılan %60) sığan en uzun süreyi seçer.
- **Doğrudan HTTPS.** Python'un `ssl` modülü ile; ters vekil veya ek paket gerekmez.
  Varsayılan Proxmox'un kendi sertifikasıdır. Sertifika okunamazsa servis düz HTTP ile
  ayakta kalır ve sebebini loglar.
- **Ağ kısıtlaması** (`allow_networks`). Yalnızca listedeki ağlardan erişilir, diğerleri
  403 alır. Firewall gerekmez; SSH ve Proxmox arayüzü etkilenmez.
- **"Beni hatırla"** girişte. İşaretlenirse çerez kalıcı olur ve oturum hareketsizlik
  yüzünden düşmez; yine de IP'ye bağlıdır.
- **Otomatik güncelleme.** İndirilen sürüm önce derlenir, sonra **mevcut config ile
  çalıştırılıp doğrulanır**, ancak ondan sonra kurulur. Program ve config yedeklenir,
  "önceki sürüme dön" düğmesi vardır. Çalışan yedek varken güncelleme yapılmaz.
- **Hesap kotaları ana ekranda.** %75 üzeri sarı, %90 üzeri kırmızı. Kota sorgusu
  önbelleklenir (`quota_cache_min`): ölçüldü, 2.7 sn → 0.2 sn.
- **Test paketi.** `python3 tests/run_tests.py` — 35 test, sahte rclone ve pgrep ile;
  gerçek Drive'a dokunmaz.

### Düzeltmeler

- `RE_STATS` "Transferred:" önekini zorunlu tutuyordu; gerçek `--stats-one-line` çıktısı
  bu öneki içermiyor. **İlerleme takibi gerçek rclone'a karşı hiç çalışmamıştı.** İlerleme
  artık `rclone rc core/stats` yapısal API'sinden okunur.
- Otomatik bant genişliği salınıyordu: kendi hızımız rclone'un *ortalama* hızından
  alınıyordu. Artık aktarılan bayt farkından anlık hesaplanır, üstel yumuşatma ve
  değişim eşiği eklendi.
- `run_at: "99:99"` kabul ediliyor ve `%24`/`%60` ile sessizce 03:39'a dönüşüyordu.
  (Test paketi buldu.)
- Kaba kuvvet kilidi hiç devreye girmiyordu: `locked_out()` süresi dolmuş kaydı tamamen
  siliyor, böylece sayaç her istekte sıfırlanıyordu.
- vzdump tespiti `pgrep -x vzdump` kullanıyordu; vzdump bir perl betiği olduğu için süreç
  adı `perl` görünür ve tespit gerçek sunucuda hiç eşleşmezdi.
- Çöp temizliği dosya başına ayrı rclone çağrısı yapıyordu (Drive'da 5-8 sn/dosya).
  Toplu çağrıya çevrildi: ölçüldü, 10 dosya 8 sn.
- Kopyalama öncesi yapılan "kaç dosya vardı" listelemesi kaldırıldı (tek başına 20+ sn).

### İç yapı

- 56 sessiz `except` isimlendirildi, `debug` ayarıyla loglanır.
- `do_run` aşamalara bölündü (82→31 satır). Retention güvenlik kuralı
  `_retention_calissin_mi()` içinde ve 8 kombinasyonu sınayan testi var.
- Rapor üreticileri ortak `_bolum_*` fonksiyonlarına bölündü.
- Arayüzde alan tablosu (`ui/src/alanlar.ts`): `validatePlan` 43→16, `savePlan` 33→17.
- Global durum sınıflara alındı (`GuvenlikDeposu`, `GuncellemeDurumu`).
- rclone artık `nice`/`ionice` ile çalışır; systemd birimlerinde `CPUWeight`, `IOWeight`,
  `MemoryMax`, `TasksMax` ve sertleştirme var.

## 1.0.0 — 2026-08-08

Gerçek bir Proxmox VE 8.4.19 host'unda ve gerçek Google Drive hesabında uçtan uca doğrulandı.

### Çekirdek

- **Çok planlı mimari.** Her plan kendi kaynak klasörü, hedef Google hesabı, saklama süreleri,
  çalışma saati, bant genişliği ve mail alıcısıyla bağımsız çalışır.
- **Gün bazlı saklama** (`keep_days`) + misafir başına **adet güvenlik tabanı** (`keep_count`).
- **İki aşamalı silme:** Drive'da N gün → Google çöp kutusunda M gün → kalıcı.
- **Zamanlama uygulamada.** `pve-gdrive-tick.timer` 5 dakikada bir bakar; saat ve gün planın
  içindedir, systemd dosyası düzenlemek gerekmez. Kaçırılan çalışma telafi edilir.
- Eski tek planlı config otomatik göç eder (`trash_grace_days` → `drive_trash_days`).

### Veri güvenliği

- **Yükleme başarısızsa retention hiç çalışmaz.** Aksi halde yeni yedek Drive'a çıkmadan
  eskiler silinip hem yerelde hem Drive'da yedeksiz kalma riski doğardı.
- **Çöp listelenemezse takip kaydı düşürülmez** — silinmemiş dosya silinmiş sanılmaz.
- **vzdump çakışma koruması:** kilit dosyası, süreç araması ve kaynak klasördeki taze geçici
  dosya olmak üzere üç bağımsız sinyal. Bekleme süresi dolarsa tur atlanır, hiçbir şey silinmez.
- **Yarım dosya yüklenmez:** `--min-age` ve `*.dat`/`*.tmp`/`*.part` dışlamaları.
- Çöken bir yedekten kalan bayat `.dat` dosyası planı sonsuza kadar bloklamaz.

### Bant genişliği

- Plan başına sabit sınır, **saatlik çizelge** (`08:00,2M 19:00,30M 23:00,off`) ve
  **otomatik mod**.
- Otomatik mod `/proc/net/dev`'den hattaki diğer trafiği ölçer, `rclone rc core/bwlimit` ile
  sınırı **yükleme kesilmeden** ayarlar. Sunucuda UrBackup gibi başka bir yedekleme yazılımı
  varken hat paylaşılır.
- Sınır yalnızca yüklemeye uygulanabilir (`bwlimit_upload_only`).

### Arayüz

- **TypeScript** ile yazılır, `tsc` strict modda derlenir, çıktı Python'a gömülür.
  Proxmox host'unda Node.js gerekmez.
- Canlı ilerleme: yüzde, aktarılan/toplam, hız, başlangıç, geçen süre, **tahmini bitiş zamanı**.
- Klasör gezgini + `/etc/pve/storage.cfg`'den yedek depolarının otomatik keşfi.
- Form doğrulama (regex), alan bazlı hata mesajları, tooltip ipuçları, örnek senaryolar,
  hazır plan şablonları.
- Sistem ve plan logları ayrı sekmelerde.
- F5 koruması: kaydedilmemiş değişiklikte uyarı, seçili plan ve log sekmesi yenilemede korunur.
  Çalışan yedek sunucu tarafında sürdüğü için sayfa yenilemekten etkilenmez.

### Güvenlik

- Oturum tabanlı giriş, `HttpOnly` + `SameSite=Strict` çerez, IP'ye bağlı oturum.
- **SVG captcha** (dış bağımlılık yok), kaba kuvvet kilidi, CSRF koruması.
- Şifre `pbkdf2_sha256` 200.000 tur ile saklanır; düz metin şifre ilk açılışta hash'lenir.
- `X-Frame-Options`, `X-Content-Type-Options`, `Referrer-Policy`, `Content-Security-Policy`.
- Otomasyon için `Authorization: Bearer` API jetonu.
- Google hesapları `drive.file` kapsamıyla eklenir: rclone yalnızca kendi oluşturduğu
  dosyaları görür.

### Bildirim

- Çok profilli SMTP, plan başına gönderici seçimi, sağlayıcı şablonları (Gmail, Outlook,
  Yandex, Yahoo).
- Başarılı / hata / atlandı için ayrı bildirim tercihleri.
- Detaylı çalışma maili ve **haftalık rapor**: misafir bazında son yedek tarihi, kaynakta olup
  Drive'a çıkmamış misafirler, kota, uyarılar.

### Bellek ve dayanıklılık

- rclone çıktısı akışla okunur, sadece son N satır tutulur.
- Log kendi kendine döner (`log_max_mb`, `log_keep`), logrotate gerekmez.
- Durum dosyasındaki satırlar kırpılır, toplamlar tam liste üzerinden hesaplanır.
- Bir planın hatası diğerlerini durdurmaz; API 500 yerine anlamlı mesaj döner.

## Ölçümler

| Ne | Nasıl ölçüldü | Sonuç |
|---|---|---|
| rclone çıktısı bellek | 200.000 satırlık çıktı, RSS farkı | **0.05 MB** (tümünü yakalayan yöntem: 39.41 MB) |
| Çöpten toplu kalıcı silme | Gerçek Drive, 10 dosya | **8 sn** (dosya başına ayrı çağrıda ~50-80 sn) |
| Kapsamlı kalıcı silme | Gerçek Drive, `--drive-trashed-only --drive-use-trash=false` | **Çalışıyor**, hesap geneli `cleanup` gerekmiyor |
| Canlı hız değiştirme | Gerçek yükleme sırasında `rclone rc core/bwlimit` | 1Mi → 5Mi **anında uygulandı** |
| Otomatik bant genişliği | Gerçek yükleme, `vmbr0` ölçümü | Diğer trafik 3.28 K/sn iken tavana (8M) çıktı |
| Log döndürme | 4.000 satır, eşik 51 KB | En fazla `log_keep`+1 = 3 dosya |
| Giriş güvenliği | 20 senaryo | Tümü geçti |
| Kaba kuvvet kilidi | 3 hatalı deneme + süre dolumu | 3. denemeden sonra kilit, süre dolunca açılıyor |
| Retention | Sahte rclone, 7 günlük set | Gün sınırı + adet tabanı doğru |
| Hatada silme | `MOCK_FAIL=copy` | Hiçbir dosya silinmedi |
| TypeScript | `tsc --noEmit`, strict | Hatasız |

### Geliştirme sırasında bulunan ve düzeltilen hatalar

- `do_run` kopyalama başarısız olsa bile retention'ı çalıştırıyordu — veri kaybı riski.
- "Yüklenen" sayısı retention'dan sonra hesaplandığı için çöpe taşınan dosyalar düşülüyordu.
- `locked_out()` süresi dolmuş kaydı tamamen siliyor, bu yüzden hatalı deneme sayacı her
  istekte sıfırlanıyor ve kaba kuvvet kilidi hiç devreye girmiyordu.
- vzdump tespiti `pgrep -x vzdump` kullanıyordu; vzdump bir perl betiği olduğu için süreç adı
  `perl` görünür ve tespit gerçek sunucuda hiç eşleşmezdi. `pgrep -f` ile düzeltildi.
- Çöp temizliği dosya başına ayrı rclone çağrısı yapıyordu; Drive API'sinde her çağrı 5-8 sn
  sürdüğü için toplu çağrıya çevrildi.
