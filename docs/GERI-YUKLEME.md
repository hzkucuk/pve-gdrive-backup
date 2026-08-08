# Geri yükleme

Bu belge, `pve-config-<düğüm>-<tarih>.tar.gz` arşivinin nasıl kullanılacağını anlatır.
Arşiv `vzdump` disk yedeklerinin **yanında** durur ve onların tek başına cevaplayamadığı
soruyu cevaplar: *"bu diskleri nereye geri yükleyeceğim?"*

## Arşivde ne var, ne yok

**Var** — geri yükleme için gereken her şey:

| Dosya | Ne işe yarar |
|---|---|
| `etc/pve/storage.cfg` | Depo tanımları (`Usb1Tb` gibi) — bunlar olmadan yedekleri koyacak yer yok |
| `etc/pve/qemu-server/*.conf`, `etc/pve/lxc/*.conf` | VM ve CT tanımları: CPU, RAM, disk, ağ kartı |
| `etc/pve/datacenter.cfg` | Veri merkezi ayarları |
| `etc/pve/user.cfg` | Kullanıcılar, gruplar, yetkiler |
| `etc/pve/firewall/*` | Güvenlik duvarı kuralları |
| `etc/pve/jobs.cfg`, `vzdump.cron` | Proxmox'un kendi yedek işleri |
| `etc/network/interfaces` | **Ağ yapılandırması — `/etc/pve` içinde değildir** |
| `etc/fstab` | `/mnt/pve/...` bağlamaları |
| `etc/apt/sources.list*` | Paket kaynakları |
| `etc/pve/priv/authorized_keys` | SSH erişimi (açık anahtar listesi) |

**Yok** — özel anahtarlar bilerek alınmaz:

`pve-root-ca.key`, `authkey.key`, `pve-ssl.key`, `pve-www.key`, `shadow.cfg`, `priv/acme/`

Bunlar şifresiz bir bulut hesabında durmamalı. Arşivin içindeki `OKUBENI.txt`
o çalışmada neyin atlandığını tek tek listeler.

## Eksik anahtarlar geri yüklemede sorun çıkarır mı?

**Tek düğümlü kurulumda hayır.** Proxmox bu anahtarları ilk açılışta kendisi üretir.
Pratikte karşılaşacakların:

1. **Tarayıcı sertifika uyarısı tekrar çıkar.** Sertifika zaten kendinden imzalıydı;
   yeni CA ile yeniden üretilir, bir kez daha kabul edersin.
2. **Açık oturumlar ve API biletleri geçersiz olur.** Yeniden giriş yaparsın. Veri kaybı yok.
3. **SSH anahtarlı erişim.** `authorized_keys` arşivde olduğu için geri yükleyebilirsin;
   ama onu yerine koyana kadar konsol veya parola erişimi gerekir.
4. **Let's Encrypt hesabı** varsa yeniden kaydolur (ücretsiz, otomatik).

Anahtarları yeniden üretmek gerekirse:

```bash
pvecm updatecerts -f      # düğüm sertifikalarını CA'dan yeniden üretir
systemctl restart pveproxy pvedaemon
```

**Kümede (birden fazla düğüm) durum farklıdır.** Corosync ve küme CA anahtarları
düğümlerin birbirine güvenmesi için gerekir; onlarsız düğüm kümeye geri katılamaz,
yeniden katmak gerekir. Kümeye geçersen bu planı gözden geçir.

## Geri yükleme sırası

```bash
# 1) Arşivi indir (hangi hesabı kullandığına göre remote adını değiştir)
rclone copy gdrive:proxmox-yedek/pve-config-pve-2026_08_09-03_00_00.tar.gz .

# 2) Önce İÇİNE BAK — üstüne yazmadan önce ne geleceğini gör
tar tzvf pve-config-pve-*.tar.gz | less
tar xzf  pve-config-pve-*.tar.gz -C /tmp/geri   # ayrı bir yere aç

# 3) Ağ ve depo tanımlarını elle karşılaştır, körlemesine kopyalama
diff /tmp/geri/etc/network/interfaces /etc/network/interfaces
diff /tmp/geri/etc/pve/storage.cfg    /etc/pve/storage.cfg
```

`/etc/pve` bir FUSE dosya sistemidir (pmxcfs). Üzerine doğrudan `tar x` ile açma;
dosyaları tek tek kopyala ve her adımda kontrol et:

```bash
cp /tmp/geri/etc/pve/storage.cfg /etc/pve/storage.cfg
cp /tmp/geri/etc/pve/lxc/100.conf /etc/pve/lxc/100.conf
```

Ağ yapılandırmasını değiştirdiysen makineye erişimi kaybetme ihtimaline karşı
konsol erişimin olduğundan emin ol:

```bash
cp /tmp/geri/etc/network/interfaces /etc/network/interfaces
ifreload -a
```

## Sonra VM/CT diskleri

Yapılandırma yerine oturduktan sonra `vzdump` arşivleri geri yüklenir:

```bash
rclone copy gdrive:proxmox-yedek/vzdump-qemu-105-2026_08_09-03_02_11.vma.zst /mnt/pve/Usb1Tb/dump/
qmrestore /mnt/pve/Usb1Tb/dump/vzdump-qemu-105-*.vma.zst 105    # VM
pct restore 100 /mnt/pve/Usb1Tb/dump/vzdump-lxc-100-*.tar.zst   # CT
```

## Tatbikat yap

Hiç denenmemiş bir yedek, yedek değil umuttur. Yılda birkaç kez:
bir CT'yi **yeni bir ID'ye** geri yükle (`pct restore 999 ...`), açıldığını gör, sil.
Üretimdeki CT'nin üstüne yazma.
