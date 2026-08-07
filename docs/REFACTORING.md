# Refactoring Analizi

Ölçüm tarihi: 2026-08-08 · Sürüm 1.1.0 · Tüm sayılar koddan ölçüldü, tahmin yok.

## Mevcut durum

| Dosya | Satır | Not |
|---|---|---|
| `pve_gdrive.py` | 4189 | 1694'ü gömülü arayüz, 50'si login sayfası |
| → gerçek Python kodu | **2445** | Asıl inceleme konusu |
| `ui/src/app.ts` | 1008 | TypeScript arayüz |
| `ui/index.html` | 402 | İşaretleme |
| `tests/run_tests.py` | 556 | 30 test |

**154 fonksiyon, ortalama 13.5 satır.** 60 satırı aşan yalnızca 6 fonksiyon var.

Bu kötü bir tablo değil. Fonksiyon granülaritesi iyi, testler geçiyor, kod gerçek
sunucuda çalışıyor. Aşağıdaki maddeler "yangın söndürme" değil, **teknik borç envanteri**.

## Bulgular — öncelik sırasıyla

### 1. Sessiz yutulan hatalar — 34 yerde `except Exception: pass`

En yüksek gerçek risk budur. Yedekleme aracında sessizce başarısız olan bir işlem,
hata veren bir işlemden tehlikelidir: kullanıcı her şeyin yolunda olduğunu sanır.

Örnekler: `set_progress`, `write_state`, `rotate_log`, `active_writes`, `vzdump_lock_held`.
Bunların bir kısmı gerçekten önemsiz (ilerleme dosyası yazılamadı), bir kısmı değil
(`write_state` başarısız olursa çöp takibi kaybolur).

**Öneri:** `except Exception: pass` yerine `except Exception as e: log_debug(...)`.
Ayrı bir `debug` seviyesi eklenip config'ten açılabilir. Kritik olanlar (`write_state`,
`put_pstate`) sessiz kalmamalı — en azından bir kez uyarı basmalı.

**Risk:** düşük · **Kazanç:** yüksek (teşhis edilebilirlik)

### 2. Modül düzeyi değişebilir global durum — 13 adet

`SESSIONS`, `CAPTCHAS`, `FAILS`, `_AUTH`, `GUNCELLEME_DURUMU`, `TLS_AKTIF`, `_CACHE`,
`_RE_CACHE`, `_ONEK_CACHE`.

Sorun: test yazarken sıfırlanmaları gerekiyor (şu an test paketi her test için
modülü yeniden yüklüyor — çalışıyor ama pahalı), ve süreç genelinde paylaşıldıkları
için eşzamanlılık varsayımları örtük.

**Öneri:** İlgili olanları küçük sınıflara topla: `OturumDeposu` (SESSIONS + CAPTCHAS +
FAILS, kilidiyle birlikte), `GuncellemeDurumu`, `Onbellek`. Davranış değişmez, test
edilebilirlik artar.

**Risk:** düşük-orta · **Kazanç:** orta

### 3. `do_run` — 82 satır, uygulamanın en kritik fonksiyonu

Silme kararlarının verildiği yer burası. Şu an tek fonksiyonda: kilit alma, vzdump
bekleme, kopyalama, listeleme, retention, çöp temizliği, anlık görüntü, geçmiş, mail.

**Öneri:** Aşamaları ayrı fonksiyonlara böl (`_asama_kopyala`, `_asama_retention`,
`_asama_ozet`), `do_run` yalnızca sırayı ve güvenlik koşullarını yönetsin.
Özellikle "yükleme başarısızsa retention çalışmaz" kuralı tek bir yerde ve
okunaklı kalmalı — bu kural projenin en önemli garantisi.

**Risk:** orta (kritik yol) · **Kazanç:** yüksek (okunabilirlik + test edilebilirlik)
**Şart:** her adımda mevcut 30 testin tamamı yeniden koşulmalı.

### 4. Rapor üreticileri — `build_report` (91) + `build_run_mail` (76)

İkisi de saf metin birleştirme, yan etkisiz. Ortak bölümleri var (Drive durumu,
misafir tablosu, uyarı listesi) ve bu bölümler kopyalanmış.

**Öneri:** `_bolum_drive()`, `_bolum_misafirler()`, `_bolum_uyarilar()` gibi küçük
üreticilere böl, iki rapor da bunları kullansın.

**Risk:** çok düşük (saf fonksiyonlar) · **Kazanç:** orta

### 5. Tek dosya mimarisi — 2445 satırda 8 farklı sorumluluk

`pve_gdrive.py` şunları barındırıyor: yapılandırma, durum, rclone sarmalayıcı,
zamanlama, yedekleme mantığı, mail, kimlik doğrulama/güvenlik, HTTP sunucu, güncelleme.

Tek dosya **bilinçli bir tercihti**: dağıtım `scp pve_gdrive.py` ile bitiyor,
hipervizöre paket kurulmuyor. Bu değer korunmalı.

**Öneri:** Kaynağı `src/*.py` olarak böl, `build_ui.py`'nin yaptığını genişletip
**tek dosyalık dağıtım çıktısını derleme adımında üret**. Arayüzde zaten yaptığımız
şeyin aynısı: modüler kaynak, tek dosya dağıtım.

```
src/ayarlar.py  durum.py  rclone.py  yedekleme.py  zamanlama.py
    posta.py    guvenlik.py  web.py   guncelleme.py
build.py  ->  pve_gdrive.py (dagitim ciktisi)
```

**Risk:** yüksek (her şeye dokunur) · **Kazanç:** uzun vadede yüksek
**Verdict:** Şimdilik **erteleyelim.** 2445 satır rahatsız edici ama yönetilemez değil
ve fonksiyon ortalaması 13.5 satır. Kod 4000 satırı geçerse veya ikinci bir kişi
katkı vermeye başlarsa bu madde birinci sıraya çıkar.

### 6. Arayüz: alan işlemleri elle tekrarlanıyor — 60 `setVal`/`setChk` çağrısı

`openEditor`, `savePlan` ve `validatePlan` aynı alan listesini üç kez, üç farklı
biçimde tekrarlıyor. Yeni bir alan eklerken üç yeri birden düzenlemek gerekiyor —
bu oturumda tam olarak bu yüzden birkaç kez alan eklemeyi unuttum.

**Öneri:** Alanları veri olarak tanımla:

```ts
const ALANLAR: Alan[] = [
  { id: "e-kd",  anahtar: "keep_days", tip: "sayi", min: 0, max: 3650, adim: 4 },
  { id: "e-runat", anahtar: "run_at", tip: "saat", adim: 5 },
  ...
];
```

`doldur()`, `topla()`, `dogrula()` bu tablodan türesin. Üç fonksiyon da tek kaynaktan
beslenir, yeni alan tek satır olur.

**Risk:** düşük (arayüz, testlerle korunuyor) · **Kazanç:** **yüksek** — bu oturumda
yaşanan hataların en sık nedeni buydu.

### 7. Küçük notlar

- `_get`/`_post` içindeki uzun `elif` zinciri (28 `self._json` çağrısı) bir yönlendirme
  tablosuna dönüşebilir: `{("GET","/api/status"): fn, ...}`
- `cfg().get(...)` 37 yerde çağrılıyor; sıcak döngülerde (bant genişliği izleyicisi)
  döngü başında bir kez okunmalı.
- `norm_plan` 60 satır ve tamamı doğrulama; madde 6'daki alan tablosu Python tarafında
  da kullanılabilir, böylece doğrulama kuralları tek yerde tanımlanır.

## Önerilen sıra

| # | İş | Risk | Kazanç | Not |
|---|---|---|---|---|
| 1 | Sessiz `except`leri logla | Düşük | Yüksek | Hemen yapılmalı |
| 2 | Arayüzde alan tablosu (madde 6) | Düşük | Yüksek | Hata kaynağını kurutur |
| 3 | Rapor üreticilerini böl | Çok düşük | Orta | Saf fonksiyonlar |
| 4 | `do_run` aşamalara bölünsün | Orta | Yüksek | Her adımda test koş |
| 5 | Global durumu sınıflara topla | Orta | Orta | |
| 6 | Modül bölme + derleme | Yüksek | Uzun vade | **Ertele** |

## Yapılmaması gerekenler

- **Büyük bir yeniden yazım.** Kod çalışıyor, 30 testi geçiyor ve gerçek sunucuda
  doğrulandı. Toptan yeniden yazmak kazanılmış güvenceyi çöpe atar.
- **Tek dosya dağıtımından vazgeçmek.** Hipervizöre paket/bağımlılık koymamak bu
  projenin en değerli özelliklerinden biri.
- **Test paketi olmadan refactor.** Her madde, mevcut 30 testin tamamı yeşil kalarak
  yapılmalı; kritik yola (madde 4) dokunmadan önce ek test yazılmalı.
