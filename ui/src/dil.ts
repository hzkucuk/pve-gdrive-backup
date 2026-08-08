/**
 * İki dilli arayüz. Türkçe kaynak dildir; İngilizce çalışma anında uygulanır.
 *
 * Neden böyle: HTML'de 388, TypeScript'te 225 görünür metin var. Hepsini anahtara
 * çevirmek yerine Türkçe metnin kendisi anahtar olarak kullanılır. Böylece işaretleme
 * hiç değişmez, yeni metin eklerken sözlüğe satır eklemek yeter — unutulursa Türkçesi
 * görünür, arayüz bozulmaz.
 */

let DIL: "tr" | "en" = "tr";

/** Türkçe metni geçerli dile çevirir. Karşılığı yoksa olduğu gibi döner. */
function C(s: string): string {
  if (DIL === "tr") return s;
  const d = EN[s];
  if (d !== undefined) return d;
  // Sonundaki noktalama/boşluk farkını tolere et: "Kaydet…" -> "Kaydet"
  const kirp = s.trim();
  if (kirp !== s && EN[kirp] !== undefined) return s.replace(kirp, EN[kirp]);
  return s;
}

function dilAl(): "tr" | "en" { return DIL; }

function dilKur(d: string): void {
  DIL = d === "en" ? "en" : "tr";
  try { localStorage.setItem("pg_dil", DIL); } catch { /* localStorage kapali olabilir */ }
  document.documentElement.setAttribute("lang", DIL);
}

/** Sayfadaki duragan metinleri (metin dugumleri, title, placeholder) cevirir. */
function sayfayiCevir(kok?: HTMLElement): void {
  if (DIL === "tr") return;
  const k = kok || document.body;
  const yuru = document.createTreeWalker(k, NodeFilter.SHOW_TEXT);
  const dugumler: Text[] = [];
  let n = yuru.nextNode();
  while (n) { dugumler.push(n as Text); n = yuru.nextNode(); }
  for (const d of dugumler) {
    const ham = d.nodeValue || "";
    const kirp = ham.trim();
    if (!kirp) continue;
    const ust = d.parentElement;
    if (ust && (ust.tagName === "SCRIPT" || ust.tagName === "STYLE")) continue;
    const yeni = C(kirp);
    if (yeni !== kirp) d.nodeValue = ham.replace(kirp, yeni);
  }
  const oznitelikler: [string, string][] = [["title", "title"], ["placeholder", "placeholder"]];
  for (const [sec, oz] of oznitelikler) {
    Array.prototype.slice.call(k.querySelectorAll("[" + sec + "]")).forEach((e: HTMLElement) => {
      const v = e.getAttribute(oz);
      if (v) { const y = C(v); if (y !== v) e.setAttribute(oz, y); }
    });
  }
}

/** Dil secicisi degisince: kaydet, sayfayi cevir, arayuzu yeniden ciz. */
function dilDegistir(d: string): void {
  dilKur(d);
  // Sunucuya da bildir: mailler ve login sayfasi ayni dilde olsun.
  void fetch("/api/settings/save", {
    method: "POST",
    headers: { "X-CSRF-Token": csrf(), "Content-Type": "application/json" },
    body: JSON.stringify({ dil: d }),
  }).finally(() => location.reload());
}

/** Acilista kayitli dili uygula. */
function dilBaslat(): void {
  let d = "tr";
  try { d = localStorage.getItem("pg_dil") || "tr"; } catch { /* yok say */ }
  dilKur(d);
  const sec = document.getElementById("dilsec") as HTMLSelectElement | null;
  if (sec) sec.value = dilAl();
  sayfayiCevir();
}

/* ---------- uygulama ici diyaloglar ---------- */
/* Tarayicinin confirm/prompt kutulari "web sitesinin mesaji" diye gorunuyor ve
   arayuzle uyumsuz. Ayni isi yapan, uygulamanin kendi penceresi kullanilir. */
let onayCoz: ((d: string | null) => void) | null = null;

function onayKapat(evet: boolean): void {
  const girdiAcik = el("onay-girdi-sar").style.display !== "none";
  const deger = girdiAcik ? (el("onay-girdi") as HTMLInputElement).value : "";
  el("m-onay").classList.remove("show");
  const c = onayCoz; onayCoz = null;
  if (c) c(evet ? deger : null);
}

/** confirm() yerine. true/false doner.
 *  Buton etiketleri verilebilir: "Sil / Vazgec" gibi netlik gerektiren yerlerde
 *  "Tamam / Iptal" ne olacagini soylemiyordu. */
function onay(metin: string, baslik?: string, evetEtiket?: string,
              hayirEtiket?: string): Promise<boolean> {
  return new Promise((coz) => {
    setTxt("onay-baslik", baslik || C("Onay"));
    setTxt("onay-evet", evetEtiket || C("Tamam"));
    setTxt("onay-hayir", hayirEtiket || C("İptal"));
    setHtml("onay-metin", metin.split("\n").map((x) => esc(x)).join("<br>"));
    el("onay-girdi-sar").style.display = "none";
    onayCoz = (d) => coz(d !== null);
    el("m-onay").classList.add("show");
    (el("onay-evet") as HTMLButtonElement).focus();
  });
}

/** prompt() yerine. Girilen metni ya da iptalde null doner. */
function sorMetin(metin: string, varsayilan?: string, baslik?: string): Promise<string | null> {
  return new Promise((coz) => {
    setTxt("onay-baslik", baslik || C("Bilgi gerekli"));
    setTxt("onay-evet", C("Tamam")); setTxt("onay-hayir", C("İptal"));
    setHtml("onay-metin", metin.split("\n").map((x) => esc(x)).join("<br>"));
    el("onay-girdi-sar").style.display = "";
    (el("onay-girdi") as HTMLInputElement).value = varsayilan || "";
    onayCoz = coz;
    el("m-onay").classList.add("show");
    (el("onay-girdi") as HTMLInputElement).focus();
  });
}

/* ---------- pencereleri suruklenebilir yap ---------- */
function surukleKur(): void {
  Array.prototype.slice.call(document.querySelectorAll(".modal > h2")).forEach((bas: HTMLElement) => {
    const pencere = bas.parentElement as HTMLElement;
    if (!pencere || bas.dataset.suruklenir) return;
    bas.dataset.suruklenir = "1";
    let x = 0, y = 0, bx = 0, by = 0, aktif = false;
    bas.addEventListener("pointerdown", (e: PointerEvent) => {
      aktif = true; x = e.clientX; y = e.clientY;
      const m = /translate\((-?[\d.]+)px,\s*(-?[\d.]+)px\)/.exec(pencere.style.transform || "");
      bx = m ? parseFloat(m[1]) : 0; by = m ? parseFloat(m[2]) : 0;
      bas.setPointerCapture(e.pointerId);
    });
    bas.addEventListener("pointermove", (e: PointerEvent) => {
      if (!aktif) return;
      pencere.style.transform = `translate(${bx + e.clientX - x}px, ${by + e.clientY - y}px)`;
    });
    const birak = (e: PointerEvent) => {
      if (!aktif) return;
      aktif = false;
      try { bas.releasePointerCapture(e.pointerId); } catch { /* yok say */ }
    };
    bas.addEventListener("pointerup", birak);
    bas.addEventListener("pointercancel", birak);
    // cift tiklama: pencereyi ortala
    bas.addEventListener("dblclick", () => { pencere.style.transform = ""; });
  });
}
