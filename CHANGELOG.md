# Değişiklik Günlüğü

Bu projede yapılan tüm iddialar ölçülerek doğrulanmıştır; her sürümde nasıl doğrulandığı yazılıdır.

## 1.7.3 — 2026-08-08

### Gözat penceresi ZFS klasörünü göremiyordu

Gezinti kökleri sabit listeydi (`/var/lib/vz`, `/mnt/pve`, `/mnt`, `/srv`).
ZFS havuzu `/USB_4T_R1` gibi **kökte** bağlandığında hiçbirinin altına düşmüyor
ve kullanıcı kendi oluşturduğu yedek klasörünü gözattan bulamıyordu.

Kökler artık ortamdan türer: ayardaki kökler **+ Proxmox depo yolları
+ ZFS bağlama noktaları**. Zaten kapsanan yol tekrar eklenmez. Köklerin dışına
çıkma yasağı aynen duruyor — testle korunuyor.

Gözat, yazılı olan yoldan açılıyor; her seferinde ilk kökten başlamıyor.

### Başlıkta oturum bloğu sola kayıyordu

Başlık `flex-wrap`; dar ekranda oturum bloğu alt satıra inip **sola** yapışıyordu.
`margin-left:auto` ile alt satırda da sağda kalıyor.

### Çelişen dosya sayısı

Kaynak alanının altında iki sayı vardı: plan kaydedildiği andaki (`0 dosya
bulundu`) ve canlı analizden gelen (`1 yedek dosyası`). Bayat olan kaldırıldı;
klasör yoksa yalnızca uyarı çıkıyor.

## 1.7.2 — 2026-08-08

### Güncelleme kaynağı: release varlığı

`raw.githubusercontent.com` ~5 dakika önbellekleniyor ve sorgu parametresiyle
kırılmıyor — ölçüldü: release varlığı **1.7.1** verirken raw hâlâ **1.6.2**
dönüyordu. Yani yeni sürüm yayınlandıktan sonra "Güncelle" dediğinde araç
eskisini indirip *"zaten güncel"* diyebiliyordu.

Varsayılan artık `releases/latest/download/pve_gdrive.py`: yayınlandığı an taze.
Eski **varsayılanı** kullanan kurulumlar açılışta sessizce taşınır; kendi
adresini yazmış olanlara dokunulmaz.

## 1.7.1 — 2026-08-08

### Proxmox linki iki not alanına da yazılır

Proxmox'ta iki ayrı Notes var ve kolayca karıştırılıyor:
`Datacenter → Notes` (`/cluster/options`) ve `Node → Notes`
(`/nodes/<ad>/config`). Link yalnızca Datacenter'a yazılıyordu; Node'un altına
bakan "eklenmemiş" sanıyordu. Artık ikisine birden yazılıyor, durum satırı
hangisinde ekli olduğunu tek tek gösteriyor, kaldırma da ikisinden birden.

## 1.7.0 — 2026-08-08

### Kaynak seçici artık ortamı anlıyor

İkinci sunucuda `USB_4T_R1` adlı 4 TB'lık ZFS havuzu yedek için seçilemiyordu ve
arayüz **sebebini söylemiyordu** — liste boş görünüyordu, o kadar.

Sebep Proxmox'un kısıtı: vzdump çıktısı düz dosyadır, `zfspool` ise dataset/zvol
sunar; içerik listesine `backup` eklenemez. Ama araç bunu açıklamak yerine
depoyu sessizce eliyordu: `pve_storages()` `path` anahtarı olmayan **ve**
`backup` içermeyen her depoyu atıyordu. ZFS havuzunun `path` anahtarı yok
(`pool` var), yani iki kere görünmezdi.

Artık:

- **`backup` işaretli her depo listelenir** — `dump/` klasörü henüz olmasa bile
  (Proxmox onu ilk yedekte oluşturur; elemek yanlıştı). Boş alan ve mevcut yedek
  sayısı da gösterilir.
- **Yedek alamayanlar da listelenir, sebebiyle**: "ZFS havuzu dosya değil
  dataset/zvol sunar", "LVM-Thin blok aygıt sunar", "içerik listesinde backup yok".
- **Tek tıkla düzeltme.** ZFS havuzunda *Yedek alanı oluştur* → `zfs create
  <havuz>/yedek` + o dataset'i `dir` deposu olarak ekler (`--is_mountpoint yes`,
  içerik `backup`). İçeriğinde `backup` olmayan dizin deposunda *Yedek içeriğini
  aç* → mevcut içerikleri **koruyarak** `backup` ekler.
- **`nodes` kısıtı okunuyor.** `storage.cfg` küme genelindedir; başka düğüme
  kısıtlı bir depo bu makinede yoktur, kullanılabilir göstermek yanıltıcıydı.
- **Klasör analizi**: seçilen yol için dosya sistemi, bağlama noktası, boş alan,
  yazılabilirlik ve mevcut yedek sayısı. Kök dosya sistemindeyse uyarır —
  host diskini doldurma tuzağı.

`--is_mountpoint yes` bilinçli: havuz bağlı değilse Proxmox kök diske yazmaya
başlamaz, hata verir.

### Kurulum çıktısı

`proxmox-link.sh` artık arayüzde (*Ayarlar → Bakım ve taşıma*); çıktı önce onu
söylüyor. Kaynak klasör görünmezse ne yapılacağı da yazılı.

Test: 92 → 96. Yeni `depolar` grubu.

## 1.6.0 — 2026-08-08

### Telegram bildirimi

Mail bazen geç gelir ya da spam'e düşer. Telegram anlık ve ek bağımlılık
gerektirmiyor — Bot API düz bir HTTPS çağrısı.

Mesaj düşen olaylar: yedek bitişi, haftalık rapor, systemd birimi çökmesi,
bütünlük uyarısı. Plan bazında kapatılabilir, plan kendi sohbetine yazabilir.

Jeton **sır olarak** ele alınıyor: dışa aktarımda, durum API'sinde ve logda
görünmez; Bot API jetonu URL'e koyduğu için hata mesajlarında `<jeton>` olarak
maskelenir. Arayüz jetonu geri göstermez — boş bırakılırsa mevcut korunur
(yoksa her ayar kaydında bildirim bozulurdu). Hepsi testle korunuyor.

### Betik bütünlüğü

"Bu betiği kimse değiştiremez di mi?" sorusunun tam cevabı: izinler yalnızca
root'a açık, ama root olan değiştirebilir. Artık betiğin sha256'sı saklanıp
**her tick'te** karşılaştırılıyor. Değişiklik olursa arayüzde kırmızı kutu,
mail, Telegram ve haftalık raporda uyarı — **bir kez**, her turda değil.
Meşru güncelleme referansı kendiliğinden yeniliyor.
`pve-gdrive butunluk [--sabitle]`.

### CLI işleri arayüze taşındı

- **Ayarları dışa/içe aktar** — indir/yükle düğmeleri. Dosya sır içermez;
  planlar kapalı gelir.
- **Proxmox Notes linki** — ekle/kaldır düğmesi, mevcut durum gösterimi.
- **Açık oturumlar** — hangi cihaz, hangi adres, kalan süre; tek tek veya
  "bu tarayıcı hariç hepsi" kapatma.

Test: 86 → 90.

## 1.5.0 — 2026-08-08

### Kurulum betiği eksik kalmıştı

İkinci bir sunucuya kurulum hazırlanırken üç boşluk çıktı:

- **`/var/lib/pve-gdrive` `0755` oluşuyordu.** İçinde OAuth jetonu, oturum
  dosyası ve config yedekleri var. Artık `0700`.
- **Kısa ad yoktu.** `ln -sfn` ile `pve-gdrive` komutu kuruluyor; güncelleme
  `realpath` kullandığı için bağlantı güvenli.
- **`pve_gdrive.py version` diye bir komut yoktu**, ama kurulum çıktısı onu
  çağırıyor ve koca kullanım metnini basıyordu. Komut eklendi.

Kurulum çıktısına teşhis komutları ve "beni hatırla" notu eklendi: çıkış yapınca
hatırlanan oturum silinir (tasarım gereği), çerez adrese bağlıdır.

### Bu sınıfı yakalayan test

Yeni bir systemd birimi veya dizin eklenip kurulum betiğini güncellemeyi
unutmak kolay — ilk sunucuda çalışır, ikinciye kurunca eksik çıkar. İki test
eklendi: `systemd/` altındaki her birim kurulum globlarıyla eşleşiyor mu, ve
kurulum çıktısında tavsiye edilen her komut programda gerçekten tanımlı mı.

Test: 84 → 86.

## 1.4.5 — 2026-08-08

### Sunucu oturum dosyasını yalnızca açılışta okuyordu

Ölçüldü: başka bir süreçte oluşturulup diske yazılan **geçerli** bir oturum için
çalışan sunucu **401** döndürüyordu. Dosya yalnızca `serve()` başlarken
okunuyordu; sonradan dosyaya giren hiçbir oturum tanınmıyordu.

Artık bellekte bulunamayan bir jeton için dosyanın damgasına bakılıyor;
değiştiyse oturumlar tazeleniyor. Damga aynıysa disk hiç okunmuyor, yani her
isteğe maliyet binmiyor. Aynı sınav: **401 → 200**.

### "Beni hatırla çalışmıyor" teşhisi

Şikâyeti ikiye ayırmak için iki kayıt eklendi:

- Tarayıcı çerez gönderdiği hâlde sunucu tanımıyorsa:
  `TESHIS: tarayici oturum cerezi gonderdi ama sunucu tanimiyor (adres, host, jeton, bellekteki oturum sayisi)`.
  Bu satır **yoksa** çerez hiç gelmiyordur — sorun tarayıcı veya adres tarafındadır.
- Giriş kaydına `host=` eklendi. Çerez **host'a bağlıdır**: bir gün
  `192.168.2.252:8787`, ertesi gün `pve.marmaralastik.local:8787` ile girilirse
  tarayıcı iki ayrı çerez kavanozu kullanır ve oturum "unutulmuş" görünür.

Test: 83 → 84.

## 1.4.4 — 2026-08-08

### "Beni hatırla" — üç ayrı kusur

Hâlâ çalışmadığı bildirildi. Uçtan uca ölçüldü ve üç şey bulundu.

**1. İkinci bir süreç oturum dosyasını eziyordu.** `kalicilari_yaz()` yalnızca
kendi belleğindekini yazıyordu; dosyayı hiç yüklememiş bir süreç (tick, CLI,
yeni açılan servis) yazdığında diğer oturumları **siliyordu**. Ölçüldü: ikinci
süreç kalıcı oturum açınca dosya 250 bayttan 243 bayta düştü — öncekinin yerine
geçmişti. Artık dosya `flock` altında okunup **birleştiriliyor**; çıkış yapılan
ve süresi dolan oturumlar birleştirmede geri dirilmiyor.

**2. Yazma hataları sessizce yutuluyordu.** `yut()` ile susturulmuştu, yani
hatırlama çalışmadığında logda hiçbir iz kalmıyordu. Artık her yazma
`oturum deposu yazildi: N kalici oturum (sebep)` olarak, her başarısızlık
`UYARI: oturum deposu yazilamadi` olarak loga düşüyor.

**3. `SameSite=Strict`.** Strict, başka bir sayfadan gelen üst düzey gezinmede
çerezi **göndermez** — Proxmox arayüzündeki bir bağlantıdan geldiğinde oturumun
açık olmasına rağmen giriş ekranı görürsün. Varsayılan `Lax` oldu
(`cookie_samesite` ile ayarlanabilir); CSRF'ye açık cross-site POST'ta yine
gönderilmez, ayrıca zaten CSRF jetonu var.

### Teşhis

Yeni komut: `pve_gdrive.py oturumlar` — kayıtlı oturumları, kalan sürelerini ve
ilgili ayarları (`remember_enabled`, `session_ip_bind`, `cookie_samesite`)
gösterir.

### Eksik olan test

1.3.1'de yalnızca oturum **deposu** test edilmişti, giriş **akışı** değil. Artık
gerçek HTTP sunucusuna "beni hatırla" ile giriş yapılıyor, `Set-Cookie` başlığı
(Max-Age, HttpOnly, SameSite) doğrulanıyor, servis yeniden başlatılıyor ve
**aynı çerezin hâlâ geçerli olduğu** ölçülüyor.

Test: 81 → 83.

## 1.4.3 — 2026-08-08

Güvenlik sertleştirmesi. "Bu betiği kimler değiştirebilir?" sorusu sorulunca
güncelleme yolu baştan incelendi ve dört kusur bulundu.

### Güncelleme adresi artık serbest değil

Güncelleme, indirdiği dosyayı **root olarak çalışan betiğin üzerine** yazar.
`update_url` ayarlardan serbestçe değiştirilebiliyordu ve şema/host kısıtı yoktu:
arayüze giren biri adresi kendi sunucusuna çevirip root kod çalıştırabilirdi.

- Yalnızca `https` ve `update_izinli_hostlar` listesindeki hostlar kabul ediliyor
  (varsayılan: GitHub). Kendi deposunu kullanan listeye ekler.
- İsteğe bağlı `update_sha256`: doluysa indirilen dosyanın özeti tutmuyorsa
  kurulmaz. Sözdizimi kontrolü dosyanın *çalışabilir* olduğunu gösterir,
  *doğru* dosya olduğunu değil.
- Adres değişikliği loga denetim kaydı olarak yazılıyor.

### OAuth jetonu dünyaya okunabilir yerde duruyordu

`/tmp/pve-gdrive-auth.out` `0644` olarak oluşuyordu ve içinde erişim jetonu
vardı. `/tmp` dünyaya yazılabilir olduğu için önceden yerleştirilmiş bir
sembolik bağlantı, root'a başka bir dosyaya yazdırabilirdi. Dosya artık
`/var/lib/pve-gdrive/auth.out`, `0600` ve `O_NOFOLLOW|O_EXCL` ile açılıyor.
Açılışta eski `/tmp` dosyası varsa siliniyor.

### Güncelleme yedekleri ve sembolik bağlantı

- Yedek dizini `0700`, config kopyaları `0600` oldu — içlerinde UI şifre hash'i
  ve SMTP parolaları düz metin duruyor, varsayılan umask ile `0755`/`0644`
  oluşuyorlardı.
- `betik_yolu()` artık `realpath` kullanıyor. Kısa ad (`pve-gdrive`) gerçek
  dosyaya sembolik bağlantı; yol çözülmediği için güncelleme bağlantının
  *kendisini* düz dosyayla değiştirebilir, systemd eski hedefi çalıştırmaya
  devam eder ve "kuruldu" denmesine rağmen hiçbir şey değişmezdi.

### Test koşucusu: süreç geneli sızıntı nöbetçisi

Bir test `smtplib.SMTP` yamasını geri koymamıştı ve hatasını sonraki testlere
devrediyordu. Her testten sonra kritik global nesneler denetleniyor; sızdıran
test adıyla raporlanıyor. Nöbetçi eklenir eklenmez iki eski sızıntı daha
ortaya çıktı. Yamalar artık `o.yamala()` ile yapılıp `temizle()` ile geri
alınıyor — hatırlamak testin değil yapının işi.

Test: 76 → 81.

## 1.4.0 — 2026-08-08

### Yedek hedefler: plan başına N hesap

Bir plan artık birden fazla hedefe sahip olabilir. Birincil hedefe yazamazsa
sırayla yedekler denenir, ilk başarılı olan kullanılır. Farklı sağlayıcı da
olabilir.

**En kritik davranış** — projenin en önemli güvenlik kuralının uzantısı:

> Retention **yalnızca yüklemenin gerçekten başarılı olduğu hedefte** çalışır.

Yedek hedefe düştüğün gün birincideki eski yedeklere dokunulmaz; hesap
düzeldiğinde orada duruyor olurlar. Bütün hedefler başarısız olursa hiçbir yerde
silme yapılmaz. Bu, adı büyük harfle yazılmış bir testle korunuyor
(`YEDEGE DUSUNCE BIRINCIDEKI YEDEKLER SILINMEZ`).

Arayüz, yedek hedefin birincil ile **aynı hesapta** olması durumunda uyarır —
o hesap kilitlenirse yedek de işe yaramaz. Yedeğe düşüldüğünde mail `HEDEFLER`
bölümü ve uyarı içerir; plan kartında "Son yazılan" satırı çıkar.

### Başka sağlayıcılar

Hesap ekleme artık sağlayıcı seçtiriyor: Google Drive, Dropbox, OneDrive, Box,
pCloud, Yandex Disk, Citrix ShareFile, HiDrive. Liste, hedef kurulumdaki
rclone 1.60.1 üzerinde `rclone authorize` ile tek tek denenerek çıkarıldı.

**Yalnızca Google Drive gerçek bir hesapla uçtan uca doğrulandı.** Diğerlerinin
OAuth akışı çalışıyor ama yükleme/saklama davranışı ölçülmedi; arayüzde
**(denenmedi)** olarak işaretli ve seçince uyarı çıkıyor.

Sağlayıcı tespiti sırasında bir hata bulundu ve düzeltildi: `rclone config
providers` çıktısı JSON'dur, satır deseni değil. İlk yazımda desen tutmuyordu ve
kural **kapalı tarafa** düşüyordu — Google Drive dahil hepsi "rclone tanımıyor"
görünüyor, hesap ekleme listesi tamamen boşalıyordu. Artık çıktı JSON olarak
ayrıştırılıyor, sonuç bir saat önbellekleniyor (~1 MB çıktı) ve çözülemezse
**hiçbir sağlayıcı gizlenmiyor**: tespit hatası çalışan bir sağlayıcıyı
saklamamalı.

Test: 70 → 76. Yeni `hedefler` grubu.

## 1.3.3 — 2026-08-08

### Plan formu artık boşuna "değişti" demiyor

Planı açıp hiçbir şey değiştirmeden kapatınca *"Kaydedilmemiş değişiklikler var,
kapatılsın mı?"* uyarısı çıkıyordu. Sebep: `bwAutoToggle()` koşulsuz olarak
"değişti" damgası bırakıyordu ve `openEditor` formu kurarken onu çağırıyordu —
yani **her plan açılışı anında kirli sayılıyordu**. Görünümü uygulamak
(`bwAutoUygula`) ile damga bırakmak artık ayrı.

### Çıkış düğmesi

`logout()` fonksiyonu vardı ama hiçbir yerden çağrılmıyordu; oturumu arayüzden
kapatmak mümkün değildi. Başlığa oturum sahibinin adı ve **⎋ Çıkış** düğmesi
eklendi. Kaydedilmemiş değişiklik varken çıkarken soruyor.

### Canlı akışta kullanıcı adı kayboluyordu

Akış paketi `public_status()` üretiyor; kullanıcı adı ve CSRF yalnızca
`/api/status` ile geliyor. Akıştan gelmeyen bu alanlar artık mevcut değerinden
korunuyor.

Test: 68 → 70.

## 1.3.2 — 2026-08-08

### Giriş ekranında sürüm ve sunucu bilgisi

Şifreyi yazmadan önce nereye girdiğini görüyorsun. Kartın altında:

```
pve-gdrive-backup  v1.3.2  ·  pve  ·  🔒 HTTPS
```

- **Sürüm** — güncelleme sonrası gerçekten yenisinin çalıştığını doğrulamak için.
- **Sunucu adı** — birden fazla Proxmox host'u varsa hangisine bağlandığını karıştırmamak için.
- **Bağlantı durumu** — TLS kapalıysa yeşil kilit yerine kırmızı `⚠ HTTP` çıkar.
  Parolayı şifresiz bir sayfaya girmeden önce görürsün.

Test: 67 → 68.

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
