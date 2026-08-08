# Kaldığımız yer — 2026-08-08

Yeni oturuma başlarken önce bunu oku. `CLAUDE.md` sözleşme, bu dosya **o günkü durum**.

## Hemen bakılacaklar

1. **İlk gerçek yükleme** — `192.168.2.252` üzerinde 2026-08-09 03:00'te 215 GB
   yükleme başlıyordu. Ölçülen hıza göre ~8 saat (≈10:55'te biter). Sonucu
   mail ve Telegram'a düşer. Sabah kontrol et:
   ```bash
   ssh -i ~/.ssh/pve_gdrive_key root@192.168.2.252 \
     '/usr/local/bin/pve-gdrive plans; tail -30 /var/log/pve-gdrive.log'
   ```
   Bakılacak: yükleme bitti mi, retention çalıştı mı, kapasite öğrenildi mi
   (`pve-gdrive` logunda "olculen yukleme kapasitesi").

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
