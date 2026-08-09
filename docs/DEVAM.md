# Kaldığımız yer — 2026-08-09 (son güncelleme 20:2x)

Yeni oturuma başlarken önce bunu oku. `CLAUDE.md` sözleşme, bu dosya **o günkü durum**.

## Hemen bakılacaklar

0. **1.9.0 KOD HAZIR, DAĞITILMADI — commit de edilmedi.** (2026-08-09 20:1x)

   Çalışma ağacında duruyor, host'ta hâlâ **1.8.1** çalışıyor. İki yeni özellik:

   - **Dahil etme filtresi** (`include_patterns`): boş = hepsi, doluysa yalnızca
     desene uyanlar yüklenir. Sihirbaz *2. Kaynak* adımında, dosya listesinin
     üstünde; desen yazınca liste anında yenilenir. Filtre dışı kalanlar
     **gizlenmez**, `filtre dışı` rozetiyle işaretlenir.
   - **Tipli girdi kontrolleri**: `run_at` / `report_at` artık `type="time"`.
     Girdi tipi de artık alan tablosundan türüyor (`alanTipleriniUygula`) —
     sayıya `min`/`max`/`step`, saate dakika hassasiyeti otomatik.

   🔴 **Ölçülmüş tuzak — bu bilgi kaybolmasın:** filtreyi `--include` ile kurmak
   YANLIŞ. rclone `--include` ve `--exclude` birlikte verilince
   *"the order they are parsed in is indeterminate"* hatası basıyor ve iki farklı
   sırada da `*.tmp` dosyası filtreden **geçti** — yani filtre açık bir planda
   `skip_patterns` garantisi kayboluyor ve yazılmakta olan yarım bir yedek
   Drive'a çıkabiliyordu. Ürün bu yüzden sıralı `--filter` üretiyor
   (`filtre_kurallari`): önce atlama, sonra dahil etme, en sonda `- *`.
   Guard testi bunu gerçek rclone ile doğruluyor ve naif kurgu geri konduğunda
   **düşüyor** (denendi).

   Doğrulama: `python3 tests/run_tests.py` → **122 geçti, 0 kaldı** (önce 118).
   Host'ta rclone **v1.60.1-DEV** ile gerçek `/mnt/pve/Usb1Tb/dump` klasöründe
   (60 dosya) `rclone lsf` ile de ölçüldü, hiçbir şey yazılmadı:

   ```
   filtresiz (yalnız atlama)          60
   *100*        (alt dize)            12
   vzdump-*-100-*  (alan hedefli)     12
   *100*.tar.zst  (yalnız arşiv)       4
   ```

   **Sıradaki adımlar:**
   ```bash
   cd ~/source/repos/pve-gdrive-backup
   python3 tests/run_tests.py        # 122 beklenir
   python3 build_ui.py               # gömülü arayüzü tazele
   git add -A && git commit          # 1.9.0
   ./dagit.sh                        # elle scp YOK (kural 6b)
   ```

   ✅ **Tick timer sorusu cevaplandı (ölçüldü 2026-08-09 20:2x).** `dagit.sh`
   timer'a **dokunmuyor**, yalnız `pve-gdrive-ui`'yi yeniden başlatıyor — ve bu
   yeterli:

   - `$HEDEF.new` ile `$HEDEF` aynı dizinde, `/usr/local/bin` → `rpool/ROOT/pve-1`
     (ölçüldü). Yani `mv` tek dosya sistemi içinde **atomik rename**: dağıtım
     sırasında başlayan bir tick ya eski inode'u ya yeni dosyayı görür, **yarım
     dosya çalıştırılmaz**.
   - `systemctl restart pve-gdrive-ui` yalnız arayüzü döndürür; tick ayrı bir
     birim, süren bir yedek kesilmez.
   - Plan başına `flock` var (`/tmp/pve-gdrive-<plan>.lock`), üst üste koşu olmaz.
   - 1.9.0 şema göçü içermiyor: yalnız güvenli varsayılanlı yeni bir plan alanı
     (`include_patterns: []`) eklendi, eski planlar olduğu gibi yüklenir.

   Dağıtım anı için host durumu (2026-08-09 20:2x): rclone **çalışmıyor**,
   `pve-gdrive-tick.service` inactive, plan `gunluk` **KAPALI**, sonraki slot
   2026-08-10 03:00. Yani pencere tamamen sakin.

   Ürün sahibine önerilen ama henüz yapılmayan: arayüzdeki desen örneklerine
   `vzdump-*-100-*` ve `*100*.tar.zst` eklensin. Sebebi ölçüldü — `*100*` alt dize
   eşleşmesi olduğu için `.log` ve `.tar.zst.notes` dosyalarını da alıyor; bunlar
   `dump_regex`'e uymadığı için **"saklama dışı"** olur: yüklenir ama hiç silinmez.

   Host durumu (2026-08-09 20:14): `pve-gdrive-ui.service` active/enabled,
   `/usr/local/bin/pve_gdrive.py serve`, sha256 `26ad07f5…`, SURUM 1.8.1;
   `pve-gdrive-tick.timer` active; PVE 8.4.19, 31 gündür ayakta.

1. **215 GB'lik yükleme DURDURULDU, plan duraklatılmış durumda.**
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

2. **İkinci sunucu yarım** — `10.0.0.253`. Yapılmayanlar:
   - Google hesabı bağlanmadı (tünel: `ssh -N -L 53682:127.0.0.1:53682 root@10.0.0.253`)
   - Telegram kurulmadı — **sunucu etiketi ver**, yoksa iki host aynı sohbete
     yazıp hangisi olduğu anlaşılmaz
   - Plan hâlâ **kapalı**
   - Kaynak hazır: `/USB_4T_R1/yedek/dump` (2.8 TB boş)

3. **`10.0.0.253` — vzdump 3 TB'lık mount point'i de alıyor.** CT 100
   (urbackupserver) `mp0 = /urbackupdisk, backup=1, size=3000G`. Log doğruladı:
   `including mount point mp0`. Üç sorun: (a) hedef `/USB_4T_R1/yedek/dump`
   **aynı havuzda**, 2,8 TB boş — sığmayabilir, (b) aynı havuza yedek gerçek
   yedek değil, (c) Google Drive kotası 2 TB, çok terabaytlık arşiv sığmaz.
   Öneri: `pct set 100 -mp0 ...,backup=0` (UrBackup'ın veri diski zaten
   başkalarının yedeği). Önce deneme yedeği alıp boyutu ölç.

4. **`10.0.0.253` — Proxmox Notes linki yok.** Marmaradaki link bu sohbetin
   başında `proxmox-link.sh` ile eklenmişti; yeni makinede hiç çalışmadı.
   Çözüm: `/opt/pve-gdrive-backup/proxmox-link.sh` ya da 1.7.1+ sürümde
   *Ayarlar → Bakım ve taşıma → Proxmox'a link ekle* (bu ikisine birden yazar).

5. **Google hesabı Windows 11 makinesinden bağlanacak.** Win10 1809+/Win11'de
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
- Oturum sonunda `tools/transcript-yedekle.sh`
- Canlıda süreç öldürmeden önce `prune_on_failure`'ı **oku** — açıksa silme yapar
- Ölçüm aracını da sına: `Ortam` PATH'e sahte rclone koyar, gerçek ölçüm için
  tam yolla gerçek ikili çağrılmalı; yerel diskte rclone sunucu tarafı klonlama
  yapar, `--disable Copy` olmadan bwlimit/kesinti ölçülemez
- `rclone cleanup` **tüm** Drive çöpünü boşaltır — test temizliğinde kullanma
