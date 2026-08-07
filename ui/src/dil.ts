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
