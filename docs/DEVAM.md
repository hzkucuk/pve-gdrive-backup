# Kaldığımız yer — 2026-08-10 (son güncelleme 01:5x)

Yeni oturuma başlarken önce bunu oku. `CLAUDE.md` sözleşme, bu dosya **o günkü durum**.

## Hemen bakılacaklar

0. **v1.11.2 YAYINLANDI (latest) ve canlıda.** (2026-10-15 02:20)

   Güvenlik: hesap/hedef adı `-` ile başlarsa rclone bayrak okuyordu; tek doğrulayıcı
   (`hesap_adi_gecerli`/`hedef_gecerli`) tüm girişlerde. Dağıtım öncesi canlı adlar
   (`sametdrive`, `sametdrive:proxmox-yedek`) yeni kuraldan geçiyor diye ölçüldü.
   Commit `6472715`; yerel = release = çalışan: `07bea7b8b4470032…`.
   `origin/HEAD` yerelde tanımlandı: `/security-review` artık çalışır (gönderilmemiş
   değişikliğe bakar).

0. **v1.11.1 YAYINLANDI (latest) ve canlıda.** (2026-10-15 02:07)

   Yaş sınırı seçilince kaç GB gideceği yaş alanının altında, özet satırında ve misafir
   kartlarında yazıyor ("en fazla" — önizleme Drive'ı listelemez). Commit `a01376a`,
   `./dagit.sh` pid 3106521; yerel = release = çalışan: `8e6de5cbc34ce33a…`.
   `v1.10.2` arşiv sürümü olarak (latest DEĞİL) yayında; varlık üretimde çalışmış dosya.

   Küçük gözlem (düzeltilmedi): servis açılışında aynı hesap için İKİ eşzamanlı
   `rclone about` görüldü. Bekleyen yol (`bekle=True`) arka plan işini (`_KOTA_ISLER`)
   kontrol etmiyor; açık arayüz + açılış ölçümü çakışıyor olabilir. Ölçülmedi.

0. **v1.11.0 YAYINLANDI ve canlıda.** (2026-10-15 01:5x)

   GitHub release `v1.11.0` (varlık `pve_gdrive.py`), commit `fddb236`. `./dagit.sh`:
   pid 3066905, sha256 eşleşti. Yerel = release = çalışan: `7ce2dcf3f190f50b…`.
   1.10.2 de bu sürümün içinde (ayrı release yapılmadı).

   - Budanan kaydı: Drive'dan silinen set diskte dursa da yeniden yüklenmiyor; kayıt her
     koşuda bugünkü saklama kuralıyla sınanıyor (kural gevşerse yeniden yüklenir).
     **Canlı Drive'da ölçüldü:** çöpteki dosya normal listede yok, `copy --dry-run` onu
     yeniden yüklemeye kalkıyor. Deney dosyası `sametdrive:pgd-deneme/cop-denemesi.txt`
     Drive çöpünde duruyor (kalıcı silme yapılmadı; boş klasör elle silinebilir).
   - Yeni `max_age_days` ("Yalnızca son N gün"), misafir seçicinin altında.
   - rclone 1.60.1 (canlı) ile 1.75 kural sonundaki boşlukta FARKLI: 1.75 kırpar,
     1.60.1 kırpmaz. Bu yüzden böyle adlar budanan kaydına hiç alınmaz.

   ⏭ **Ürün sahibi test edecek (önümüzdeki günler):**
   - Arayüzde "Yalnızca son … gün" alanı, hızlı düğmeler, `eski` / `Drive'da silindi` rozetleri
   - Telefonda form taşması (biz 500 px'te ölçtük; Chrome daha dar pencereye izin vermedi)
   - Gündüz elle "Yedekle": en eski set yeniden yüklenmemeli, günlükte
     "N dosya saklama kuraliyla Drive'dan silinmisti, tekrar yuklenmeyecek" görünmeli
   - Canlı plan Ağustos'tan beri koşmamış (`last_run` 2026-08-09) — bilgi, risk maddesi değil.

0. **v1.10.2 host'a dağıtıldı — GitHub yayını HENÜZ YOK.** (2026-08-10 01:5x)

   Hesap kartı `hata / zaman aşımı` diyordu. Ölçüldü: `rclone about` 12 koşudan
   11'inde ~1 sn, 1 kez **50.1 sn**. Sebep `-vv` günlüğünde: Google, rclone'un
   herkese açık OAuth projesini (`project_number:202264815644`) hız sınırına
   takıyor (`403 rateLimitExceeded`), rclone üstel geri çekilmeyle 10 kez tekrar
   deniyor ve 45 sn'lik **sabit** sınır aşılıyor. Ağ/hesap sorunu yok.

   Düzeltilen: (1) başarısız ölçüm artık eldeki doğru değeri **silmiyor** —
   kart `⏳ ölçüm gecikti · N dk önceki değer` diyor; (2) başarısız sonuç 15 dk
   "taze" sayılmıyor, tekrar aralığı `quota_retry_sec`; (3) zaman aşımı
   parametrik: `quota_timeout_sec` 120 / `quota_wait_timeout_sec` 45.

   Dağıtım `./dagit.sh` ile doğrulandı (pid 2218957, sha256 eşleşti, sürüm
   1.10.2). Canlı dosyayla taklit edilen hata: `ok=True, bayat=True,
   149.0 GB / 2.0 TB, %7.3` — yani kırmızı kart yerine doğru sayı.
   Testler: **138 geçti, 0 kaldı.**

   ⏭ **Sırada:** GitHub release `v1.10.2` (güncelleme adresi hâlâ 1.10.1'i
   gösteriyor) ve kalıcı çözüm olarak kendi `client_id`/`client_secret`'ımız.

0. **v1.10.1 YAYINLANDI ve canlıda.** (2026-08-10 00:0x)

   GitHub release: `v1.10.1` — varlık `pve_gdrive.py`. Güncelleme adresi
   (`releases/latest/download/pve_gdrive.py`) indirilip karşılaştırıldı:
   `sha256 259224af1b9b3298…`, yereldeki ve host'taki dosyayla **birebir aynı**.
   Host'ta güncelleme denetimi koşuldu: `{'ok': True, 'surum': '1.10.1'}`.

   Önceki yayın v1.8.1'di; 1.9.0 hiç yayınlanmadı (arayüzü açılmıyordu), yani
   kullanıcılar bozuk sürümü hiç görmedi.

   **Kurulum betikleri de bu sürümde elden geçti.** Etkileşimsiz kurulumun
   yapılandırma üreticisi `install.sh` içinde gömülüydü; artık
   `tools/varsayilan-conf.py`. Testler iki kusur yakaladı: sihirbaz dalı
   yapılandırma yolunu sabit yazıyordu (`PVE_GDRIVE_CONF` yok sayılıyordu,
   `kurulum.py` ise tanıyordu) ve üretilen arayüz şifresi `/tmp`'de **0644**
   duruyordu. İkisi de düzeltildi.

   Gerçek Debian 12 kabında, sahte `systemctl` ile denendi:
   temiz kurulum (config 600, 4 birim, durum dizini 700, kaynak
   `/etc/pve/storage.cfg`'den bulundu) · yükseltme (mevcut config'e
   dokunulmuyor: 2 plan, filtre, `enabled`, `keep_days`, kullanıcı adı korundu) ·
   tek satırlık kurulum (`curl … | bash`, GitHub'dan 1.10.1 kurdu).

   ⚠️ **Yeni kural (CLAUDE.md 9b):** her iş bitiminde kurulum betikleri
   güncellenir ve gerçek Linux'ta test edilir. Ürün ile betikler ayrı yaşıyor
   ve sessizce ayrışıyor.

   Testler: **131 geçti, 0 kaldı.**

1. **1.10.1: hesap kotası ilk açılışta gelmiyordu.** (2026-08-09 23:5x)

   Hesap kotası ilk açılışta gelmiyordu, F5 gerekiyordu. Üç kusur birden:
   ölçüm sürerken ekranda kırmızı `⚠ kota okunamadı` yazıyordu (ortada arıza
   yok, `rclone about` 34 sn'ye kadar sürebiliyor); ölçüm bitince kimse haber
   vermiyordu (kota bellekte, `state.json` değişmiyor, izleyici yayın yapmıyor);
   SSE canlıyken yoklama 60 sn olduğu için ekran bir dakika yalan söylüyordu.

   Şimdi: `kota ölçülüyor…` ayrı bir durum, ölçüm bitince `durum` olayı
   itiliyor (ölçüldü: 3,1 sn), ve bekleyen hesap varken arayüz 3 sn'de bir
   kendi bakıyor. Host'tan çekilen dosyayla doğrulandı: kullanıcı hiçbir şeye
   dokunmadan `kota ölçülüyor…` → `sametdrive 7% · 148.0 GB / 2.0 TB`.

   `sha256 259224af1b9b3298…` — yerel ile host birebir aynı, HTTP 200.
   Testler: **128 geçti, 0 kaldı.**

2. **1.10.0: misafir seçici ve arayüzün açılış hatası.** (2026-08-09 23:1x)

   Commit `4bde2e0` → push → `./dagit.sh`. Doğrulama betiğin sözüne değil,
   çalışan sürecin açtığı dosyaya dayanıyor:

   ```
   çalışan dosya : /usr/local/bin/pve_gdrive.py
   sürüm         : 1.10.0
   sha256        : e6999c1d58a1d68a…   ← yereldekiyle birebir aynı
   servis        : active · timer active · arayüz HTTP 200
   plan gunluk   : KAPALI, sonraki 2026-08-10 03:00 (dokunulmadı)
   ```

   Ayrıca host'taki dosya **geri çekilip** Chrome headless ile açıldı:
   `plan_satiri=1, PLAN_ALANLARI=hazir(46), acildi=true`.

   🔴 **1.9.0 ARAYÜZÜ ÖLÜYDÜ — bu bilgi kaybolmasın.** 1.9.0 üç saat boyunca
   canlıda durdu ve arayüz hiç açılmıyordu: sayfa yükleniyor, HTML'de her şey
   var, plan listesi boş. `tsc` tek dosyaya birleştiriyor (`outFile`);
   `app.ts`'in sonundaki açılış satırı `alanTipleriniUygula()` çağırıyordu, o
   işlev `alanlar.ts`'teki `PLAN_ALANLARI` tablosunu okuyor ve `alanlar.ts`
   `app.ts`'ten **sonra** birleşiyor → `Cannot access 'PLAN_ALANLARI' before
   initialization` → paket o satırda ölüyor, `refresh()` hiç koşmuyor.

   Ölçüm (Chrome headless `--dump-dom`): 1.8.1 plan satırı **1**, 1.9.0 plan
   satırı **0**, 1.10.0 plan satırı **1**. Dosya sırasını değiştirmek çözüm
   değil — `alanlar.ts` de `app.ts`'teki `RX`'i kullanıyor, ters sırada `tsc`
   **TS2448** veriyor (denendi). Açılış artık `DOMContentLoaded` ile çağrılıyor.

   ⚠️ **Asıl ders doğrulamada, ve bu bir sınıf:** 1.9.0 "doğrulandı" diye
   raporlanmıştı — servis `active`, HTTP **200**, gömülü dosyada `id="e-inc"`,
   `alanTipleriniUygula`, `type="time"` dizeleri **vardı**. Hepsi doğruydu ve
   hiçbiri arayüzün çalıştığını göstermiyordu. Gömülü arayüzü değiştiren her
   işte artık `gomulu arayuz paketi bastan sona kosuyor` testi var: paketi
   gerçek tarayıcıda çalıştırıp en sonda tanımlanan tabloya erişiyor mu diye
   bakıyor. Test 1.9.0 kodunda tam o hatayla düşüyor (ölçüldü).

   ✅ **Misafir seçici geldi (1.10.0).** "Sadece 100 nolu misafiri yedekle"
   demek için desen bilmek gerekmiyor: 2. Kaynak adımında klasördeki misafirler
   set/dosya/boyutuyla listeleniyor, **yalnız** düğmesi tek tıkla filtreyi o
   misafire ayarlıyor, kutucuklar ikincisini ekliyor. Desen kutusu görünür ve
   düzenlenebilir kalıyor. Üretilen kalıp alan hedefli (`vzdump-lxc-100-*`);
   elle `*100*` yazana alt dize uyarısı ve tek tıkla **Düzelt** çıkıyor.

   Gerçek tarayıcıda ölçülen akış (60 gerçek dosya adı):
   `filtre boş → 60 yüklenir` · `yalnız lxc-100 → 12 / 48 dışarıda` ·
   `+ qemu-105 → 24 / 36` · `elle *100* → uyarı` · `Düzelt → vzdump-*-100-*`.

   ❌ **1.9.0 notlarındaki bir uyarı YANLIŞTI, silindi.** `.log` ve
   `.tar.zst.notes` dosyaları "saklama dışı" **değil**. `dump_regex` baştan
   eşleşiyor (`.match`), üçü de aynı sete giriyor ve arşivle **birlikte**
   siliniyor. Gerçek dosya adlarıyla ölçüldü: `*100*` → 12 dosya yüklenir,
   `saklama_disi = 0`, `collect_sets` 12 dosyanın 12'sini de bir sete koyuyor.
   Filtre kullanmak Drive'da dosya biriktirmiyor.

   Testler: **126 geçti, 0 kaldı** (1.9.0'da 122).

3. **215 GB'lik yükleme DURDURULDU, plan duraklatılmış durumda.**
   2026-08-09 00:41'de ürün sahibinin isteğiyle kesildi (29,8 GB / 215,9 GB,
   %14). rclone'a SIGTERM verildi; `prune_on_failure=False` olduğu önceden
   okunmuştu, **hiçbir yedek silinmedi** (log: `retention ATLANDI`). Hata maili
   ve Telegram bildirimi gitti — durdurma eski kodda `HATA` sayılıyordu.

   Plan `gunluk` şu an `enabled=False`. **Yeniden başlatma kararı ürün
   sahibinin.** Etkinleştirilirse gece 03:00'te başlar. Baştan başlamaz:
   tamamlanan 3 dosya Drive'da duruyor ve rclone onları `Unchanged skipping`
   diyip geçiyor (ölçüldü); yarıda kalan 2 dosya yeniden yüklenir.
   ```bash
   ssh -i ~/.ssh/pve_gdrive_key root@192.168.2.252 \
     '/usr/local/bin/pve-gdrive plans; tail -30 /var/log/pve-gdrive.log'
   ```

4. **İkinci sunucu yarım** — `10.0.0.253`. Yapılmayanlar:
   - Google hesabı bağlanmadı (tünel: `ssh -N -L 53682:127.0.0.1:53682 root@10.0.0.253`)
   - Telegram kurulmadı — **sunucu etiketi ver**, yoksa iki host aynı sohbete
     yazıp hangisi olduğu anlaşılmaz
   - Plan hâlâ **kapalı**
   - Kaynak hazır: `/USB_4T_R1/yedek/dump` (2.8 TB boş)

5. **`10.0.0.253` — vzdump 3 TB'lık mount point'i de alıyor.** CT 100
   (urbackupserver) `mp0 = /urbackupdisk, backup=1, size=3000G`. Log doğruladı:
   `including mount point mp0`. Üç sorun: (a) hedef `/USB_4T_R1/yedek/dump`
   **aynı havuzda**, 2,8 TB boş — sığmayabilir, (b) aynı havuza yedek gerçek
   yedek değil, (c) Google Drive kotası 2 TB, çok terabaytlık arşiv sığmaz.
   Öneri: `pct set 100 -mp0 ...,backup=0` (UrBackup'ın veri diski zaten
   başkalarının yedeği). Önce deneme yedeği alıp boyutu ölç.

6. **`10.0.0.253` — Proxmox Notes linki yok.** Marmaradaki link bu sohbetin
   başında `proxmox-link.sh` ile eklenmişti; yeni makinede hiç çalışmadı.
   Çözüm: `/opt/pve-gdrive-backup/proxmox-link.sh` ya da 1.7.1+ sürümde
   *Ayarlar → Bakım ve taşıma → Proxmox'a link ekle* (bu ikisine birden yazar).

7. **Google hesabı Windows 11 makinesinden bağlanacak.** Win10 1809+/Win11'de
   OpenSSH istemcisi kurulu gelir. Tünel o makinede açılacak, tarayıcı da orada
   olacak. Ağ izin listesi kontrol edilsin: `pve-gdrive aglar`.

## Bilinen durumlar

- **VPN aynı anda tek tarafa bağlanıyor.** Biri erişilebilirken öteki değil.
- `USB2TXFS` deposu (10.0.0.253) **arızalı** — kullanıcı "dikkate alma" dedi.
  Araç ona dokunmuyor; yalnızca listede bilgi olarak görünür.
- `192.168.2.252` üzerinde SSH root **parola ile açık** (`PermitRootLogin yes`).
  Kullanıcıya söylendi, karar onun. Benim anahtarım
  (`pve-gdrive-claude-20260807`) hâlâ ekli — iş bitince silinmesi önerildi.

## Doğrulanmamış

- **Otomatik güncelleme gerçek koşulda hiç denenmedi.** `10.0.0.253` üzerinde
  arayüzden güncelleme yapılıp yapılmadığı bilinmiyor. Yapıldıysa sonucu sor:
  bu, güncelleme yolunun ilk saha sınavı olacaktı.

## Açık kusur (küçük)

- Durdurulan koşunun özeti `yuklenen:0` diyor, oysa rclone 3 dosyayı
  tamamlamıştı. Sayaç kopyalama başarısız olunca sıfırlanıyor
  (`_asama_kopyala` ok=False iken 0 döndürüyor). Kozmetik ama yanıltıcı:
  durdurulan koşuda "kaç dosya kurtuldu" bilgisi kayboluyor.

## 2026-08-09 yapılanlar (1.8.0)

- **⏹ Yedeklemeyi durdur.** Önceden yalnızca "Planı duraklat" vardı ve o
  sadece zamanlayıcıyı kapatıyordu; süren yüklemeyi kesmenin hiçbir yolu yoktu.
  Sinyal değil dosya bayrağı (manuel koşu ayrı süreçte, zamanlanmış koşu
  servisin kendi sürecinde — `killpg` servisi de öldürürdü).
  **Durdurulan koşuda `prune_on_failure` açık olsa bile silme yapılmaz.**
- **Soğuma süresi** `cooldown_hours`, varsayılan 6 saat. Slot atlanır,
  ertelenmez (erteleme gündüz kendiliğinden başlatırdı).
- **Kaynak klasördeki dosyalar listelenebiliyor.** Ortaya çıkardığı gerçek:
  `dump_regex`'e uymayan dosya yüklenir ama `do_prune` onu hiç görmez —
  Drive'da sonsuza kadar birikir. Listede `saklama dışı` diye işaretli.
- **Kilitli alan artık kilitli görünüyor.** Stil sayfasında tek bir `:disabled`
  kuralı yoktu; aynı kökten 3 kusur daha vardı (▶ Yedekle, ↑ taşı, çizelge
  ön ayar butonları).

Ölçümler: Drive'a karşı gerçek durdurma testi yapıldı — %2 akmışken kesildi,
**Drive'da yarım dosya kalmadı**, test klasörü silindi (Drive çöpüne
dokunulmadı). Tepki = `(0…durdur_kontrol_sn) + ~0,5 sn`. Görsel doğrulama
Chrome headless ile önce/sonra alındı. **112/112 test geçiyor.**

## Bugün yapılanlar (1.3.1 → 1.7.4, 12 sürüm)

Canlı olay akışı (SSE), sağ tık menüleri, F5 taslağı, host yapılandırma yedeği,
servis izleme (`OnFailure` + tick nabzı), Telegram, betik bütünlük denetimi,
yedek hedefler (N hesap, failover), çoklu sağlayıcı, kaynak seçicinin ortamı
anlaması (ZFS), bakım işlerinin arayüze taşınması.

Ayrıntı: `CHANGELOG.md`. **100/100 test geçiyor.**

## Bir daha yapılmayacaklar

- Yanlış dosyaya dağıtım → `./dagit.sh` kullan (hedefi systemd biriminden okur)
- `state.json`'daki `last_run`'ı elle boşaltma → zamanlayıcı yedeği o an başlatır
- "Test ettim" demeden önce **kullanıcının gireceği yolu** çalıştır
- Gömülü arayüz değiştiyse **sayfayı gerçekten aç** — HTTP 200 ve "dosyada şu
  dize var" arayüzün çalıştığını göstermez (1.9.0: paket açılışta ölmüştü)
- Oturum sonunda `tools/transcript-yedekle.sh`
- Canlıda süreç öldürmeden önce `prune_on_failure`'ı **oku** — açıksa silme yapar
- Ölçüm aracını da sına: `Ortam` PATH'e sahte rclone koyar, gerçek ölçüm için
  tam yolla gerçek ikili çağrılmalı; yerel diskte rclone sunucu tarafı klonlama
  yapar, `--disable Copy` olmadan bwlimit/kesinti ölçülemez
- `rclone cleanup` **tüm** Drive çöpünü boşaltır — test temizliğinde kullanma
