/* Sag tik menusu.
 *
 * Tarayicinin kendi menusu bu arayuzde ise yaramiyor: kullanicinin isteyecegi
 * seyler "yedegi baslat", "dosya adini kopyala", "bu plani disa aktar" gibi
 * uygulamaya ozel eylemler. Butonlari her karta dizmek yerine sik kullanilanlari
 * butonda, gerisini sag tikta topluyoruz.
 *
 * Tek bir delege dinleyici var; listeler her yenilendiginde tekrar baglamak
 * gerekmiyor (kartlar refresh ile bastan uretiliyor).
 */

interface MenuOge {
  etiket?: string;          // gosterilecek metin (C() ile cevrilir)
  simge?: string;           // basina konacak emoji
  ipucu?: string;           // title
  is?: () => void | Promise<void>;
  ayrac?: boolean;          // ayirici cizgi
  baslik?: boolean;         // tiklanamaz bolum basligi
  pasif?: boolean;          // gri ve tiklanamaz
  tehlike?: boolean;        // kirmizi (silme gibi)
}

type MenuUretici = (hedef: HTMLElement, olay: MouseEvent) => MenuOge[] | null;

const MENU_KAYIT: { secici: string; uret: MenuUretici }[] = [];
let menuEl: HTMLElement | null = null;
let menuOgeleri: MenuOge[] = [];
let menuSecim = -1;

function menuKapat(): void {
  if (!menuEl) return;
  menuEl.remove();
  menuEl = null; menuOgeleri = []; menuSecim = -1;
}

/** Menuyu imlecin yaninda acar; ekran disina tasarsa ice ceker. */
function menuAc(x: number, y: number, ogeler: MenuOge[]): void {
  menuKapat();
  const kutu = document.createElement("div");
  kutu.className = "ctx";
  kutu.setAttribute("role", "menu");
  menuOgeleri = ogeler;
  ogeler.forEach((o, i) => {
    if (o.ayrac) { kutu.appendChild(document.createElement("hr")); return; }
    const d = document.createElement("div");
    if (o.baslik) {
      d.className = "ctx-baslik";
      d.textContent = C(o.etiket || "");
      kutu.appendChild(d);
      return;
    }
    d.className = "ctx-oge" + (o.pasif ? " pasif" : "") + (o.tehlike ? " tehlike" : "");
    d.setAttribute("role", "menuitem");
    d.setAttribute("data-i", String(i));
    if (o.ipucu) d.title = C(o.ipucu);
    d.innerHTML = '<span class="ctx-simge">' + (o.simge || "") + "</span>"
      + '<span class="ctx-metin"></span>';
    (d.querySelector(".ctx-metin") as HTMLElement).textContent = C(o.etiket || "");
    if (!o.pasif) {
      d.onclick = (e) => { e.stopPropagation(); menuKapat(); void o.is?.(); };
      d.onmouseenter = () => menuVurgula(i);
    }
    kutu.appendChild(d);
  });
  // Once gorunmez yerlestir ki gercek olcusunu okuyabilelim
  kutu.style.left = "-9999px"; kutu.style.top = "-9999px";
  document.body.appendChild(kutu);
  const g = kutu.getBoundingClientRect();
  const bosluk = 6;
  let sol = x, ust = y;
  if (sol + g.width + bosluk > window.innerWidth) sol = Math.max(bosluk, x - g.width);
  if (ust + g.height + bosluk > window.innerHeight) ust = Math.max(bosluk, y - g.height);
  // Menu ekrandan uzunsa kendi icinde kaysin
  if (g.height + bosluk * 2 > window.innerHeight) {
    ust = bosluk;
    kutu.style.maxHeight = window.innerHeight - bosluk * 2 + "px";
    kutu.style.overflowY = "auto";
  }
  kutu.style.left = sol + "px"; kutu.style.top = ust + "px";
  menuEl = kutu;
}

function menuVurgula(i: number): void {
  if (!menuEl) return;
  Array.prototype.slice.call(menuEl.querySelectorAll(".ctx-oge"))
    .forEach((d: HTMLElement) => d.classList.remove("on"));
  menuSecim = i;
  const d = menuEl.querySelector('.ctx-oge[data-i="' + i + '"]') as HTMLElement | null;
  if (d) { d.classList.add("on"); d.scrollIntoView({ block: "nearest" }); }
}

/** Klavyeyle gezinirken atlanacak ogeleri (ayrac, baslik, pasif) es geçer. */
function menuGez(yon: number): void {
  const n = menuOgeleri.length;
  if (!n) return;
  let i = menuSecim;
  for (let adim = 0; adim < n; adim++) {
    i = (i + yon + n) % n;
    const o = menuOgeleri[i];
    if (!o.ayrac && !o.baslik && !o.pasif) { menuVurgula(i); return; }
  }
}

/** Bir secici icin sag tik menusu tanimlar. uret() null donerse menu acilmaz. */
function sagTik(secici: string, uret: MenuUretici): void {
  MENU_KAYIT.push({ secici, uret });
}

function menuTetikle(x: number, y: number, hedef: EventTarget | null, olay: MouseEvent): boolean {
  const e = hedef as HTMLElement | null;
  if (!e || !e.closest) return false;
  for (const k of MENU_KAYIT) {
    const kap = e.closest(k.secici) as HTMLElement | null;
    if (!kap) continue;
    const ogeler = k.uret(kap, olay);
    if (!ogeler || !ogeler.length) return false;
    menuAc(x, y, ogeler);
    return true;
  }
  return false;
}

function menuKur(): void {
  document.addEventListener("contextmenu", (e: MouseEvent) => {
    // Metin secilmisse tarayicinin kopyala menusu daha faydali; karisma.
    const secili = window.getSelection();
    if (secili && String(secili).length > 2 && !e.shiftKey) return;
    if (menuTetikle(e.clientX, e.clientY, e.target, e)) e.preventDefault();
    else menuKapat();
  });
  document.addEventListener("click", (e) => {
    if (menuEl && !(e.target as HTMLElement).closest(".ctx")) menuKapat();
  });
  document.addEventListener("keydown", (e: KeyboardEvent) => {
    if (!menuEl) return;
    if (e.key === "Escape") { e.preventDefault(); menuKapat(); }
    else if (e.key === "ArrowDown") { e.preventDefault(); menuGez(1); }
    else if (e.key === "ArrowUp") { e.preventDefault(); menuGez(-1); }
    else if (e.key === "Enter" && menuSecim >= 0) {
      e.preventDefault();
      const o = menuOgeleri[menuSecim];
      menuKapat(); void o?.is?.();
    }
  }, true);
  window.addEventListener("resize", menuKapat);
  window.addEventListener("blur", menuKapat);
  document.addEventListener("scroll", menuKapat, true);

  // Dokunmatik: uzun basma sag tik yerine gecer
  let zaman = 0, bx = 0, by = 0;
  document.addEventListener("touchstart", (e: TouchEvent) => {
    if (e.touches.length !== 1) return;
    const t = e.touches[0]; bx = t.clientX; by = t.clientY;
    const hedef = e.target;
    zaman = window.setTimeout(() => menuTetikle(bx, by, hedef, e as unknown as MouseEvent), 520);
  }, { passive: true });
  const iptal = (): void => { if (zaman) { clearTimeout(zaman); zaman = 0; } };
  document.addEventListener("touchmove", iptal, { passive: true });
  document.addEventListener("touchend", iptal, { passive: true });
}

/* ---------- ortak yardimcilar ---------- */

/** Panoya yazar. HTTPS/localhost disinda clipboard API yok; eski yola duser. */
async function panoyaYaz(metin: string, ne?: string): Promise<void> {
  try {
    if (navigator.clipboard && window.isSecureContext) {
      await navigator.clipboard.writeText(metin);
    } else {
      const t = document.createElement("textarea");
      t.value = metin;
      t.style.position = "fixed"; t.style.opacity = "0";
      document.body.appendChild(t); t.select();
      document.execCommand("copy");
      t.remove();
    }
    flash((ne ? C(ne) + " " : "") + C("panoya kopyalandı"), true);
  } catch {
    flash(C("kopyalanamadı — metni elle seçmen gerekiyor"), false);
  }
}

/** Metni dosya olarak indirir (log, plan disa aktarimi vb.). */
function dosyaIndir(ad: string, icerik: string, tip?: string): void {
  const b = new Blob([icerik], { type: tip || "text/plain;charset=utf-8" });
  const u = URL.createObjectURL(b);
  const a = document.createElement("a");
  a.href = u; a.download = ad;
  document.body.appendChild(a); a.click(); a.remove();
  window.setTimeout(() => URL.revokeObjectURL(u), 1000);
}
