/** pve-gdrive-backup web arayuzu. tsc ile derlenip pve_gdrive.py icine gomulur. */

let S: Status | null = null;
let sel: string | null = null;
let cur = "";
let REM: Remote[] = [];
let SMTP: SmtpProfile[] = [];
let EDIT: string | null = null;
let authTimer = 0;
let refTimer = 0;
let LOGSRC = "all";
let dirty = false;
let running = 0;

const WD = ["Pzt", "Sal", C("Çar"), "Per", "Cum", "Cmt", "Paz"];

/* ---------- DOM yardimcilari (tip guvenli) ---------- */
type Field = HTMLInputElement | HTMLSelectElement | HTMLTextAreaElement;

function el(id: string): HTMLElement {
  const e = document.getElementById(id);
  if (!e) throw new Error("eleman bulunamadi: " + id);
  return e;
}
function fld(id: string): Field { return el(id) as Field; }
function val(id: string): string { return fld(id).value; }
function setVal(id: string, v: unknown): void { fld(id).value = v === null || v === undefined ? "" : String(v); }
function chk(id: string): boolean { return (el(id) as HTMLInputElement).checked; }
function setChk(id: string, v: boolean): void { (el(id) as HTMLInputElement).checked = !!v; }
function setHtml(id: string, h: string): void { el(id).innerHTML = h; }
function setTxt(id: string, t: string): void { el(id).textContent = t; }

function esc(s: unknown): string {
  return String(s === null || s === undefined ? "" : s)
    .replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c] as string));
}
function hb(n: unknown): string {
  let v = Number(n) || 0;
  const u = ["B", "KB", "MB", "GB", "TB"];
  for (let i = 0; i < u.length; i++) { if (v < 1024) return v.toFixed(1) + " " + u[i]; v /= 1024; }
  return v.toFixed(1) + " PB";
}
function fmtDur(sec: number): string {
  const s = Math.max(0, Math.round(sec));
  const h = Math.floor(s / 3600), m = Math.floor((s % 3600) / 60), r = s % 60;
  return (h ? h + "s " : "") + (h || m ? m + "dk " : "") + r + "sn";
}
function flash(m: string, ok: boolean): void {
  const f = el("flash");
  f.textContent = m;
  f.style.background = ok ? "#238636" : "#b03636";
  f.classList.add("show");
  window.setTimeout(() => f.classList.remove("show"), 3200);
}
function closeM(id: string): void { el(id).classList.remove("show"); if (id === "m-edit") dirty = false; }
function openM(id: string): void { el(id).classList.add("show"); }

/* ---------- API ---------- */
function csrf(): string { return S && S.csrf ? S.csrf : ""; }

async function api<T = ApiResult>(url: string, opt?: RequestInit): Promise<T> {
  const o: RequestInit = opt || {};
  o.headers = Object.assign({ "X-CSRF-Token": csrf() }, o.headers || {});
  const r = await fetch(url, o);
  if (r.status === 401) { location.reload(); return { ok: false, msg: "oturum bitti" } as unknown as T; }
  const t = await r.text();
  try { return JSON.parse(t) as T; }
  catch { return { ok: false, msg: t.slice(0, 200) } as unknown as T; }
}

/* ---------- F5 / yenileme korumasi ---------- */
window.addEventListener("beforeunload", (e: BeforeUnloadEvent) => {
  if (dirty) { e.preventDefault(); e.returnValue = ""; return ""; }
  return undefined;
});
function markDirty(): void { dirty = true; taslakPlanla(); }

/* ---------- F5 taslagi ----------
 * beforeunload uyarisi yalnizca soruyor; "evet" dersen yazdiklarin gidiyordu.
 * Acik formu araliklarla yerel olarak sakliyoruz, donunce geri yuklemeyi oneriyoruz.
 * Sunucuya hicbir sey gitmez; taslak yalnizca bu tarayicida durur. */
const TASLAK = "pg_taslak";
let taslakZamanlayici = 0;

/** #m-edit icindeki tum girdilerin anlik degeri. Alan tablosuna bagimli degil:
 *  forma yeni bir alan eklendiginde taslak da kendiliginden kapsar. */
function formAnlik(): Record<string, unknown> {
  const o: Record<string, unknown> = {};
  const kap = document.getElementById("m-edit");
  if (!kap) return o;
  Array.prototype.slice.call(kap.querySelectorAll("input,select,textarea"))
    .forEach((g: HTMLInputElement) => {
      if (!g.id || g.type === "file" || g.type === "password") return;
      o[g.id] = g.type === "checkbox" || g.type === "radio" ? g.checked : g.value;
    });
  o.__gunler = Array.prototype.slice.call(el("e-wd").querySelectorAll("input:checked"))
    .map((c: HTMLInputElement) => c.value);
  return o;
}

function formGeriYukle(o: Record<string, unknown>): void {
  for (const k of Object.keys(o)) {
    if (k === "__gunler") continue;
    const g = document.getElementById(k) as HTMLInputElement | null;
    if (!g) continue;
    if (g.type === "checkbox" || g.type === "radio") g.checked = Boolean(o[k]);
    else g.value = String(o[k]);
  }
  const gunler = (o.__gunler as string[]) || [];
  Array.prototype.slice.call(el("e-wd").querySelectorAll("input"))
    .forEach((c: HTMLInputElement) => { c.checked = gunler.indexOf(c.value) >= 0; });
}

function taslakPlanla(): void {
  if (taslakZamanlayici) return;                 // saniyede bir yaz, her tusa basista degil
  taslakZamanlayici = window.setTimeout(() => {
    taslakZamanlayici = 0;
    if (!dirty || !el("m-edit").classList.contains("show")) return;
    try {
      localStorage.setItem(TASLAK, JSON.stringify({
        pid: EDIT, sihirbaz: wSihirbaz, adim: wAktif,
        zaman: Date.now(), alanlar: formAnlik(),
      }));
    } catch { /* kota dolu olabilir, taslak zorunlu degil */ }
  }, 1000);
}

function taslakSil(): void {
  try { localStorage.removeItem(TASLAK); } catch { /* yok say */ }
}

/** Acilista yarim kalmis duzenleme varsa geri yuklemeyi onerir. */
async function taslakSor(): Promise<void> {
  let t: { pid: string | null; sihirbaz: boolean; adim: number; zaman: number;
           alanlar: Record<string, unknown> } | null = null;
  try { t = JSON.parse(localStorage.getItem(TASLAK) || "null"); } catch { t = null; }
  if (!t || !t.alanlar) return;
  const yas = (Date.now() - (t.zaman || 0)) / 60000;
  if (yas > 60 * 24) { taslakSil(); return; }    // bir gunden eski taslak ise yaramaz
  if (t.pid && S && !S.plans.some((p) => p.id === t!.pid)) { taslakSil(); return; }
  const ad = String(t.alanlar["e-name"] || "").trim();
  const ne = t.pid ? C("Plan: ") + (ad || t.pid) : C("yeni plan");
  const sure = yas < 1 ? C("az önce") : Math.round(yas) + C(" dakika önce");
  if (!await onay(C("Yarım kalmış bir düzenleme var — ") + ne + " (" + sure + ").\n"
                  + C("Kaydedilmemişti. Geri yüklensin mi?"),
                  C("Yarım kalan düzenleme"), C("Geri yükle"), C("Sil"))) {
    taslakSil(); return;
  }
  openEditor(t.pid);
  formGeriYukle(t.alanlar);
  if (t.sihirbaz) { wAktif = Math.max(1, Math.min(ADIMLAR.length, t.adim || 1)); wGoster(); }
  ceviriUygula(); ramHint(); saklamaIpucu(); markDirty();
  flash(C("taslak geri yüklendi — kaydetmedikçe uygulanmaz"), true);
}

try {
  sel = localStorage.getItem("pg_sel") || null;
  LOGSRC = localStorage.getItem("pg_log") || "all";
} catch { /* localStorage kapali olabilir */ }

function remember(): void {
  try { localStorage.setItem("pg_sel", sel || ""); localStorage.setItem("pg_log", LOGSRC); }
  catch { /* yok say */ }
}

/* ---------- dogrulama ---------- */
const RX = {
  time: /^([01]?\d|2[0-3]):[0-5]\d$/,
  folder: /^[^:*?"<>|]*$/,
  acct: /^[A-Za-z0-9_-]+$/,
  bw: /^(off|\d+(\.\d+)?[KMGT]?)$/i,
  bwsched: /^([01]?\d|2[0-3]):[0-5]\d,(off|\d+(\.\d+)?[KMGT]?)$/i,
  chunk: /^\d+(\.\d+)?[KMG]$/i,
  mail: /^[^@\s,;]+@[^@\s,;]+\.[^@\s,;]{2,}$/,
  host: /^[A-Za-z0-9]([A-Za-z0-9.-]*[A-Za-z0-9])?$/,
  ip: /^(\d{1,3}\.){3}\d{1,3}$|^localhost$/,
};
function errBox(id: string): HTMLElement | null {
  // Kutu adlari iki bicimde: e-name -> err-name, a-name -> err-aname. Ikisini de dene.
  return document.getElementById("err-" + id.replace("-", ""))
      || document.getElementById("err-" + id.replace(/^[a-z]-/, ""));
}
/** Dogrulama turunda ilk hatali alanin id'si. hataOdakla() bunu kullanir. */
let ilkHataAlani: string | null = null;
function hataTuruBaslat(): void { ilkHataAlani = null; }

/** Hatali alan ekranin disinda kalabiliyordu: sihirbazda dogru adima gec,
 *  alani ortala ve odagi ver ki kullanici neyi duzeltecegini gorsun. */
function hataOdakla(): void {
  const id = ilkHataAlani;
  if (!id) return;
  const e = document.getElementById(id) as HTMLElement | null;
  if (!e) return;
  if (wSihirbaz) {
    const adim = ADIM_ALANLARI.findIndex((liste) => liste.indexOf(id) >= 0);
    if (adim >= 0 && adim + 1 !== wAktif) { wAktif = adim + 1; wGoster(); }
  }
  const kutu = e.closest("details") as HTMLDetailsElement | null;
  if (kutu && !kutu.open) kutu.open = true;
  window.setTimeout(() => {
    try { e.scrollIntoView({ block: "center", behavior: "smooth" }); }
    catch { e.scrollIntoView(); }
    try { (e as HTMLInputElement).focus({ preventScroll: true }); }
    catch { (e as HTMLInputElement).focus(); }
    e.classList.add("odak");
    window.setTimeout(() => e.classList.remove("odak"), 1400);
  }, 60);
}

function bad(id: string, msg: string): boolean {
  if (!ilkHataAlani) ilkHataAlani = id;
  fld(id).classList.add("bad");
  const e = errBox(id);
  if (e) { e.textContent = msg; e.classList.add("show"); }
  return false;
}
function good(id: string): boolean {
  fld(id).classList.remove("bad");
  const e = errBox(id);
  if (e) e.classList.remove("show");
  return true;
}
function vTxt(id: string, msg: string): boolean { return val(id).trim() ? good(id) : bad(id, msg); }
function vRx(id: string, rx: RegExp, msg: string, optional?: boolean): boolean {
  const v = val(id).trim();
  if (!v) return optional ? good(id) : bad(id, msg);
  return rx.test(v) ? good(id) : bad(id, msg);
}
function vNum(id: string, min: number, max: number | null, msg: string): boolean {
  const v = val(id).trim();
  if (v === "" || isNaN(Number(v))) return bad(id, msg);
  const n = Number(v);
  if (n < min || (max !== null && n > max)) return bad(id, msg);
  return good(id);
}
/** "08:00,2M 19:00,off" biciminde saatlik hiz cizelgesini dogrular. */
function vBwSched(id: string): boolean {
  const v = val(id).trim();
  if (!v) return good(id);
  const kotu = v.split(/\s+/).filter(Boolean).filter((x) => !RX.bwsched.test(x));
  if (kotu.length) return bad(id, "'" + kotu[0] + "' hatalı — SS:DD,hız olmalı (ör. 08:00,2M)");
  return good(id);
}
function bwPreset(v: string): void { setVal("e-bwsch", v); good("e-bwsch"); markDirty(); }

/** "30M" -> bayt/sn. Alt/ust sinir karsilastirmasi icin. */
function bwBytes(t: string): number {
  const s = String(t || "").trim();
  if (!s || s.toLowerCase() === "off") return 0;
  const m = s.match(/^([\d.]+)\s*([BKMGT]?)$/i);
  if (!m) return 0;
  const carp: Record<string, number> = { "": 1, B: 1, K: 1024, M: 1048576, G: 1073741824, T: 1099511627776 };
  return parseFloat(m[1]) * (carp[m[2].toUpperCase()] || 1);
}
/** Elle kapasite alani yalnizca manuel kipte gorunsun. */
function bwLinkKipi(): void {
  const satir = document.getElementById("bwlink-satir");
  if (satir) satir.style.display = val("e-bwlmode") === "manuel" ? "" : "none";
}

/** Yalnizca gorunumu ayarlar; "degisti" damgasi BIRAKMAZ.
 *  openEditor formu kurarken bunu cagirir. */
function bwAutoUygula(): void {
  const acik = chk("e-bwauto");
  el("bwauto-box").style.display = acik ? "" : "none";
  fld("e-bw").disabled = acik;
  fld("e-bwsch").disabled = acik;
}

/** Kullanici kutuyu tikladiginda. Onceden bu tek fonksiyon vardi ve openEditor
 *  da onu cagiriyordu: her plan acilisi aninda "kaydedilmemis degisiklik var"
 *  sayiliyor, hicbir sey degistirmeden kapatirken uyari cikiyordu. */
function bwAutoToggle(): void { bwAutoUygula(); markDirty(); }
interface IfaceBilgi {
  name: string; tx: number; rx: number;
  default: boolean; onerilen: boolean; kopru: boolean; hiz: number;
}

async function loadIfaces(secili: string): Promise<void> {
  try {
    const j = await api<{ default: string; onerilen: string; onerilen_neden: string;
                          ifaces: IfaceBilgi[] }>("/api/ifaces");
    const list = j.ifaces || [];
    const etiket = (i: IfaceBilgi): string => esc(i.name)
      + (i.kopru ? C("  · köprü") : "")
      + (i.hiz ? "  · " + i.hiz + " Mbit" : "")
      + "  · " + hb(i.tx) + C(" gönderilmiş")
      + (i.onerilen ? C("  ← önerilen") : "");
    setHtml("e-bwif", '<option value="">' + C("(otomatik: ") + esc(j.onerilen || "-")
      + ")</option>" + list.map((i) => '<option value="' + esc(i.name) + '">'
        + etiket(i) + "</option>").join(""));
    setVal("e-bwif", secili);
    setHtml("e-bwifhint", C("Otomatik seçim: ") + "<b>" + esc(j.onerilen || "-") + "</b> ("
      + esc(C(j.onerilen_neden || "")) + "). "
      + C("Köprü (vmbr0) sayaçları VM'ler arası yerel trafiği de sayar — o trafik "
        + "internete hiç çıkmaz, yükleme hızınla yarışmaz. Köprünün altındaki "
        + "bond/fiziksel arayüz doğru ölçümü verir."));
  } catch { /* yok say */ }
}

function vMails(id: string, optional?: boolean): boolean {
  const v = val(id).trim();
  if (!v) return optional ? good(id) : bad(id, "alıcı adresi gerekli");
  const kotu = v.split(",").map((x) => x.trim()).filter(Boolean).filter((x) => !RX.mail.test(x));
  return kotu.length ? bad(id, "geçersiz adres: " + kotu[0]) : good(id);
}

/* ---------- plan kartlari ---------- */
function pillOf(p: Plan): string {
  const s = p.state || ({} as PlanState);
  const ip = (t: string): string => ' title="' + esc(C(t)) + '"';
  if (p.running) return '<span class="pill run">● ÇALIŞIYOR</span>';
  if (!p.enabled) return '<span class="pill off"' + ip("Zamanlama kapalı; dosyalara dokunulmaz.")
    + ">KAPALI</span>";
  // Hic calismamis plan "ATLANDI" gibi gorunmesin: ikisi ayri sey.
  if (!s.last_run) return '<span class="pill idle"'
    + ip("Bu plan henüz hiç çalışmadı. İlk çalışma: " + (p.next_run || "-"))
    + ">⏳ BEKLİYOR</span>";
  if (s.status === "basarili") return '<span class="pill ok">✔ BAŞARILI</span>';
  if (s.status === "HATA") return '<span class="pill err">✖ HATA</span>';
  if (s.status === "atlandi") return '<span class="pill run"'
    + ip("Son denemede iş yapılmadı — genelde vzdump hâlâ çalışıyordu. "
       + "Sebep kartın altındaki özet satırında yazar. Sonraki turda tekrar denenir.")
    + ">⏸ ATLANDI</span>";
  return '<span class="pill idle">' + esc(s.status || "—").toUpperCase() + "</span>";
}
/** Zamanlayici durmussa bunu susarak geçmek olmaz: timer olurse hicbir yedek
 *  alinmaz ve tek belirtisi "sonraki calisma"nin gecmiste kalmasi olur. */
function saglikCiz(): void {
  const h = S && S.saglik;
  const kutu = document.getElementById("saglik");
  if (!kutu) return;
  const uyarilar: string[] = [];
  if (h && h.butunluk === "DEGISTI") {
    // Bu en agiri: calisan kod beklenenden farkli. Ustte ve kirmizi dursun.
    uyarilar.push('<b>🛑 ' + esc(C("Çalışan betik değişmiş")) + "</b>"
      + '<div class="small" style="margin-top:5px">' + esc(h.butunluk_mesaj || "") + "</div>"
      + '<div class="small" style="margin-top:5px">'
      + esc(C("Güncelleme yaptıysan normaldir; referansı yenile: "))
      + "<code>pve-gdrive butunluk --sabitle</code></div>");
  }
  if (h && h.tick !== "iyi") {
    uyarilar.push('<b>⚠ ' + esc(C(h.tick === "gecikmis" ? "Zamanlayıcı gecikmiş"
                                                        : "Zamanlayıcı hiç çalışmadı"))
      + "</b><div class=\"small\" style=\"margin-top:5px\">" + esc(h.tick_mesaj || "") + "</div>"
      + '<div class="small" style="margin-top:5px">'
      + esc(C("Kontrol et: ")) + "<code>systemctl status pve-gdrive-tick.timer</code></div>");
  }
  if (!uyarilar.length) { kutu.style.display = "none"; kutu.textContent = ""; return; }
  kutu.style.display = "";
  kutu.className = "card uyari-kutu";
  kutu.innerHTML = uyarilar.join('<hr style="border:0;border-top:1px solid #4a2222;margin:9px 0">');
}

function progOf(p: Plan): string {
  const g = p.weekdays && p.weekdays.length
    ? p.weekdays.map((d) => WD[d - 1]).join(",") : "her gün";
  return g + " " + esc(p.run_at);
}
function etaSec(t: string): number | null {
  const m = String(t).match(/(?:(\d+)h)?(?:(\d+)m)?(?:([\d.]+)s)?/);
  if (!m) return null;
  const v = (Number(m[1]) || 0) * 3600 + (Number(m[2]) || 0) * 60 + (Number(m[3]) || 0);
  return v || null;
}
function progBox(p: Plan): string {
  if (!p.running) return "";
  const g = p.progress;
  const lbl = (g && g.phase_label) || "Çalışıyor";
  const pct = g && typeof g.pct === "number" ? g.pct : null;
  const st = g && g.started ? new Date(g.started * 1000) : null;
  const gecen = g && g.started ? Date.now() / 1000 - g.started : 0;
  let h = '<div class="card" style="border-color:#4d3d1a;background:#1a1710;margin-bottom:10px">'
    + '<div style="display:flex;justify-content:space-between;gap:8px;flex-wrap:wrap">'
    + "<b>● " + esc(lbl) + '</b><span class="small">' + (pct !== null ? pct + "%" : "") + "</span></div>"
    + '<div class="bar" style="background:#2b2415"><i style="width:' + (pct !== null ? pct : 5)
    + '%;background:linear-gradient(90deg,#d29922,#3fb950)"></i></div>'
    + '<table style="margin-top:8px"><tbody>'
    + '<tr><td class="small">Başlangıç</td><td class="r">' + (st ? st.toLocaleTimeString("tr-TR") : "—")
    + '</td><td class="small">Geçen</td><td class="r">' + fmtDur(gecen) + "</td></tr>";
  if (g && g.total) {
    h += '<tr><td class="small">Aktarılan</td><td class="r">'
      + esc(g.done_h || hb(g.done)) + " / " + esc(g.total_h || hb(g.total))
      + '</td><td class="small">Hız</td><td class="r">' + esc(g.speed || "—") + "</td></tr>";
    let eta: string | null = g.eta && g.eta !== "-" ? g.eta : null;
    let bitis = "—";
    if (eta) {
      const sn = etaSec(eta);
      if (sn !== null) bitis = new Date(Date.now() + sn * 1000).toLocaleTimeString("tr-TR");
    } else if ((g.speed_bps || 0) > 0 && (g.total || 0) > (g.done || 0)) {
      const sn2 = ((g.total || 0) - (g.done || 0)) / (g.speed_bps || 1);
      eta = fmtDur(sn2);
      bitis = new Date(Date.now() + sn2 * 1000).toLocaleTimeString("tr-TR");
    }
    h += '<tr><td class="small">Kalan süre</td><td class="r">' + esc(eta || "—")
      + '</td><td class="small">Tahmini bitiş</td><td class="r">' + bitis + "</td></tr>";
  }
  h += '</tbody></table><div class="hintC(">Bu iş sunucuda çalışıyor — sayfayı yenilesen de '
    + ")kapatsan da devam eder.</div></div>";
  return h;
}
function planCard(p: Plan): string {
  const s = p.state || ({} as PlanState);
  return '<div class="plan' + (p.id === sel ? " sel" : "") + '" data-plan="' + esc(p.id)
    + "\" onclick=\"pick('" + p.id + "')\">"
    + "<h3>" + esc(p.name) + pillOf(p) + "</h3>"
    + progBox(p)
    + '<div class="row"><span>Kaynak</span><b>' + esc(p.src_dir)
    + (p.src_exists ? ' <span class="small">(' + p.src_dumps + " dosya)</span>" : ' <span class="pill err">yok</span>')
    + "</b></div>"
    + '<div class="row"><span>Hedef</span><b>' + esc(p.remote)
    + ((p.yedek_hedefler || []).length
        ? ' <span class="pill idle" title="' + esc(C("Birincil çalışmazsa sırayla denenir: "))
          + esc((p.yedek_hedefler || []).join(", ")) + '">+'
          + (p.yedek_hedefler || []).length + C(" yedek") + "</span>" : "")
    + "</b></div>"
    + (s.aktif_hedef && s.aktif_hedef !== p.remote
        ? '<div class="row"><span>' + C("Son yazılan") + '</span><b class="uyari-metin">'
          + esc(s.aktif_hedef) + "</b></div>" : "")
    + '<div class="row"><span>Program</span><b>' + progOf(p) + "</b></div>"
    + '<div class="row"><span>Sonraki</span><b>' + esc(p.next_run || "-") + "</b></div>"
    + '<div class="row"><span>Saklama</span><b>' + p.keep_days + C(" gün · min ") + p.keep_count
    + C(" set · çöp ") + p.drive_trash_days + C(" gün") + "</b></div>"
    + '<div class="row"><span>Son çalışma</span><b>' + esc(s.last_run || "-") + "</b></div>"
    + (p.weekly_report ? '<div class="row"><span>Haftalık rapor</span><b>' + esc(p.next_report || "-") + "</b></div>" : "")
    + (s.summary ? '<div class="small" style="margin-top:6px">' + esc(s.summary) + "</div>" : "")
    + '<div class="pbtns" onclick="event.stopPropagation()">'
    + "<button class=\"sm primary\" onclick=\"act('backup','" + p.id + "')\"" + (p.running ? " disabled" : "") + ">▶ Yedekle</button>"
    + "<button class=\"sm\" onclick=\"act('prune','" + p.id + "')\">🧹 Retention</button>"
    + "<button class=\"sm\" onclick=\"act('purgetrash','" + p.id + "')\">🗑 Çöpü Boşalt</button>"
    + "<button class=\"sm\" onclick=\"act('report','" + p.id + "')\">📊 Rapor</button>"
    + "<button class=\"sm\" onclick=\"act('testmail','" + p.id + "')\">✉ Test</button>"
    + "<button class=\"sm\" onclick=\"openEditor('" + p.id + "')\">✎ Düzenle</button>"
    + "<button class=\"sm warn\" onclick=\"delPlan('" + p.id + "')\">Sil</button></div></div>";
}
function pick(id: string): void { sel = id; remember(); render(); }

function detail(p: Plan | undefined): string {
  if (!p) return "";
  const s = p.state || ({} as PlanState);
  const b = s.backups || [], t = s.trash || [], q = s.quota || {};
  const T = s.totals || { count: b.length, size: b.reduce((a, x) => a + (Number(x.size) || 0), 0) };
  const TT = s.trash_totals || { count: t.length, size: 0 };
  let age = "—";
  if (b[0] && b[0].mod) {
    const d = (Date.now() - new Date(b[0].mod).getTime()) / 3600000;
    age = d < 24 ? d.toFixed(1) + " saat" : (d / 24).toFixed(1) + " gün";
  }
  const used = Number(q.used) || 0, total = Number(q.total) || 0;
  const pct = total ? Math.min(100, (used / total) * 100) : 0;
  const cards: [string, string | number][] = [
    ["Yedek dosyası", T.count], ["Toplam boyut", hb(T.size)], ["Son yedek yaşı", age],
    ["Çöpte bekleyen", TT.count], ["Saklama", p.keep_days + " gün"], ["Çöp süresi", p.drive_trash_days + " gün"]];
  const h = (s.history || []).slice(0, 12);
  return '<div class="panel"><h2>' + esc(p.name) + ' <span class="small">'
    + esc(p.src_dir) + " → " + esc(p.remote) + "</span></h2>"
    + '<div class="grid">' + cards.map((c) => '<div class="card"><div class="k">' + c[0]
      + '</div><div class="v">' + c[1] + "</div></div>").join("") + "</div>"
    + '<h2>Google Drive kullanımı</h2><div class="bar"><i style="width:' + pct.toFixed(1) + '%"></i></div>'
    + '<div class="small" style="margin-top:8px">' + hb(used) + " / " + hb(total) + " (" + pct.toFixed(1)
    + C("%) · çöp: ") + hb(q.trashed || 0) + C(" · boş: ") + hb(q.free || 0) + "</div></div>"
    + '<div class="cols"><div class="panel"><h2>Yedekler (Drive)</h2><table><thead><tr><th>Tarih</th>'
    + '<th>VM/CT</th><th class="r">Boyut</th><th>Dosya</th></tr></thead><tbody>'
    + (b.slice(0, 40).map((x) => {
        const cl = String(x.guest).indexOf("lxc") === 0 ? "tag lxc" : "tag";
        return "<tr><td>" + esc((x.mod || "").slice(0, 19).replace("T", " ")) + '</td><td><span class="'
          + cl + '">' + esc(x.guest) + '</span></td><td class="r">' + hb(x.size)
          + '</td><td class="small">' + esc(x.name) + "</td></tr>";
      }).join("") || '<tr><td colspan=4 class="small">yedek yok</td></tr>')
    + '</tbody></table></div><div class="panel"><h2>Google çöp kutusu (' + TT.count
    + ') <span class="small">süre dolunca kalıcı silinir</span></h2>'
    + '<table><thead><tr><th>Dosya</th><th class="r">Boyut</th><th class="r">Kalan</th></tr></thead><tbody>'
    + (t.map((x) => '<tr><td class="small">' + esc(x.name) + '</td><td class="r">' + hb(x.size)
        + '</td><td class="r">' + (x.tracked ? x.remain_days + " gün" : "—") + "</td></tr>").join("")
      || '<tr><td colspan=3 class="small">boş</td></tr>')
    + "</tbody></table></div></div>"
    + '<div class="panel"><h2>Çalışma geçmişi</h2><table><thead><tr><th>Zaman</th><th>Durum</th>'
    + "<th>Tetik</th><th>Özet</th></tr></thead><tbody>"
    + (h.map((x) => "<tr><td>" + esc(x.time) + "</td><td>" + esc(x.status) + "</td><td>"
        + esc(x.trigger) + '</td><td class="small">' + esc(x.summary) + "</td></tr>").join("")
      || '<tr><td colspan=4 class="small">kayıt yok</td></tr>') + "</tbody></table></div>";
}

function render(): void {
  if (!S) return;
  const ps = S.plans || [];
  if (!sel || !ps.some((p) => p.id === sel)) sel = ps.length ? ps[0].id : null;
  running = ps.filter((p) => p.running).length;
  const sunucuDil = (S.settings && (S.settings as unknown as Record<string, unknown>).dil) as string | undefined;
  const sec = document.getElementById("dilsec") as HTMLSelectElement | null;
  if (sec && sunucuDil && sec.value !== dilAl()) sec.value = dilAl();
  const tls = S.tls;
  setHtml("tlsrozet", tls && tls.aktif
    ? '<span class="pill ok" title="' + esc(C("Bağlantı şifreli")) + '">🔒 HTTPS</span>'
    : '<span class="pill err" title="' + esc(C("Trafik şifresiz — yalnızca VPN içinde kullan"))
      + '">⚠ HTTP</span>');
  const g = S.guncelleme;
  setHtml("uprozet", g && g.yeni_var
    ? '<span class="pill run" title="' + esc(C("Yeni sürüm var: ") + (g.uzak || ""))
      + '" style="cursor:pointer" onclick="openSettings()">⬆ ' + esc(g.uzak || "") + C(" hazır")
      + "</span>"
    : '<span class="small" title="' + esc(C("Kurulu sürüm")) + '">v' + esc(S.surum || "?")
      + "</span>");
  saglikCiz(); kullaniciCiz();
  setTxt("hinfo", ps.length + " plan" + (running ? " · " + running + " çalışıyor" : "")
    + (S.updated ? " · durum: " + S.updated : "") + (S.smtp_ready ? "" : " · mail profili yok"));
  setHtml("plans", ps.map(planCard).join("")
    || '<div class="card">' + C("Henüz plan yok. Sağ üstten + Yeni Plan ile başla.") + "</div>");
  hesapSerit();
  setHtml("detail", detail(ps.filter((p) => p.id === sel)[0]));
  ceviriUygula();
  const tabs: [string, string][] = ([["all", "Tümü"], ["system", "Sistem"]] as [string, string][])
    .concat(ps.map((p) => [p.id, p.name] as [string, string]));
  setHtml("logtabs", tabs.map((t) => '<button class="' + (LOGSRC === t[0] ? "on" : "")
    + "\" onclick=\"setLog('" + t[0] + "')\">" + esc(t[1]) + "</button>").join(""));
}
function setLog(src: string): void { LOGSRC = src; remember(); void loadLog(); }

/** Dinamik uretilen icerik de cevrilsin (kartlar, tablolar, modallar). */
function ceviriUygula(): void { if (dilAl() !== "tr") sayfayiCevir(); }

/** Ana ekranda hesap kotalari. Dolmak uzere olan bir hesap yedegi bozar,
 *  bu yuzden plana girmeden gorunur olmali. Tiklayinca yonetim ekrani acilir. */
function hesapSerit(): void {
  const h = (S && S.hesaplar) || [];
  if (!h.length) { setHtml("hesapserit", ""); return; }
  setHtml("hesapserit", h.map((x) => {
    const q = x.quota || {};
    if (!q.ok) {
      return '<div class="hesap hata" onclick="openAccounts()"><div class="ad"><b>'
        + esc(x.name) + '</b><span>hata</span></div><div class="alt">⚠ '
        + esc(q.error || C("kota okunamadı")) + "</div></div>";
    }
    const pct = x.pct === null || x.pct === undefined ? 0 : x.pct;
    const sinif = pct >= 90 ? " dolu" : (pct >= 75 ? " uyari" : "");
    return '<div class="hesap' + sinif + '" onclick="openAccounts()" title="Yönetmek için tıkla">'
      + '<div class="ad"><b>' + esc(x.name) + "</b><span>" + pct.toFixed(0) + "%</span></div>"
      + '<div class="mini"><i style="width:' + Math.min(100, pct) + '%"></i></div>'
      + '<div class="alt">' + hb(q.used) + " / " + hb(q.total)
      + (q.trashed ? C(" · çöp ") + hb(q.trashed) : "") + "</div></div>";
  }).join(""));
}

async function loadLog(): Promise<void> {
  try {
    const r = await fetch("/api/log?src=" + encodeURIComponent(LOGSRC));
    setTxt("log", await r.text());
  } catch { /* gecici ag hatasi */ }
  if (S) render();
}

async function refresh(): Promise<void> {
  try { S = await api<Status>("/api/status"); }
  catch { return; }
  if (S && S.login) { location.reload(); return; }
  render();
  void loadLog();
  akisBaslat();
  yoklamaAyarla();
}

/* ---------- canli olay akisi (SSE) ----------
 * Once birkac saniyede bir /api/status cekiliyordu: hicbir sey degismese de
 * istek gidiyor, degisiklik ise gec gorunuyordu. Artik sunucu itiyor.
 * Akis kurulamazsa (eski vekil, kapali ayar) eski yoklama moduna dusuyoruz —
 * arayuz her durumda calisir kalir. */
let akis: EventSource | null = null;
let akisCanli = false;
let akisHata = 0;

function akisDurumu(canli: boolean): void {
  if (akisCanli === canli) return;
  akisCanli = canli;
  const e = document.getElementById("canli");
  if (e) {
    e.className = "pill " + (canli ? "ok" : "idle");
    e.textContent = canli ? C("● CANLI") : C("○ yoklama");
    e.title = canli ? C("Sunucu değişiklikleri anında gönderiyor")
                    : C("Canlı akış yok — belirli aralıklarla yenileniyor");
  }
  yoklamaAyarla();
}

/** Akis calisirken yoklama tamamen durur; yalnizca yedek olarak seyrek doner. */
function yoklamaAyarla(): void {
  const base = ((S && S.settings && S.settings.ui_refresh_sec) || 5) * 1000;
  window.clearInterval(refTimer);
  const iv = akisCanli ? 60000 : (running ? Math.min(base, 2000) : base);
  refTimer = window.setInterval(() => void refresh(), iv);
}

function akisBaslat(): void {
  if (akis || typeof EventSource === "undefined") return;
  if (S && S.settings && S.settings.sse_enabled === false) return;
  try { akis = new EventSource("/api/events"); }
  catch { akis = null; return; }

  akis.addEventListener("open", () => { akisHata = 0; akisDurumu(true); });

  akis.addEventListener("durum", (e: MessageEvent) => {
    try {
      const y = JSON.parse(e.data) as Status;
      if (y.login) { location.reload(); return; }
      // csrf ve kullanici adi yalnizca /api/status ile gelir (oturuma bagli).
      // Akis paketi public_status() uretir; oradan gelmeyeni mevcudundan koru,
      // yoksa her canli guncellemede baslikta ad kaybolur.
      if (S && !y.csrf) y.csrf = S.csrf;
      if (S && !y.user) y.user = S.user;
      S = y; akisDurumu(true); render();
    } catch { /* bozuk paket: bir sonraki tazeleme duzeltir */ }
  });

  akis.addEventListener("ilerleme", (e: MessageEvent) => {
    try {
      const m = JSON.parse(e.data) as Record<string, Progress>;
      if (!S) return;
      S.plans.forEach((p) => {
        const g = m[p.id];
        p.progress = g; p.running = Boolean(g);
      });
      running = S.plans.filter((p) => p.running).length;
      render();
    } catch { /* yok say */ }
  });

  akis.addEventListener("log", (e: MessageEvent) => {
    try { logEkle((JSON.parse(e.data) as { satirlar: string[] }).satirlar || []); }
    catch { /* yok say */ }
  });

  akis.addEventListener("kalp", () => akisDurumu(true));

  akis.addEventListener("error", () => {
    akisDurumu(false);
    // EventSource kendi yeniden baglanir; ustuste basarisiz olursa vazgec
    if (++akisHata >= 6 && akis) { akis.close(); akis = null; }
  });
}

/** Akistan gelen satirlari log kutusuna ekler. Secili kaynaga gore suzulur,
 *  kutu sonundaysa asagi kaydirilir (okurken zipladigi olmasin). */
function logEkle(satirlar: string[]): void {
  if (!satirlar.length) return;
  const kutu = el("log");
  const dipte = kutu.scrollHeight - kutu.scrollTop - kutu.clientHeight < 40;
  const suz = satirlar.filter((x) => {
    if (LOGSRC === "all") return true;
    const m = /\|\s*\[([^\]]+)\]/.exec(x);
    return LOGSRC === "system" ? !m : Boolean(m && m[1] === LOGSRC);
  });
  if (!suz.length) return;
  kutu.textContent = ((kutu.textContent || "") + "\n" + suz.join("\n")).trim();
  // Bellek sinirli kalsin: en fazla son 2000 satir tutulur
  const t = (kutu.textContent || "").split("\n");
  if (t.length > 2000) kutu.textContent = t.slice(-2000).join("\n");
  if (dipte) kutu.scrollTop = kutu.scrollHeight;
}

async function act(d: string, pid: string): Promise<void> {
  flash(C("çalışıyor…"), true);
  try {
    const j = await api("/api/action?do=" + d + "&plan=" + encodeURIComponent(pid), { method: "POST" });
    flash(j.msg || "tamam", j.ok);
  } catch { flash("hata", false); }
  if (!akisCanli) window.setTimeout(() => void refresh(), 900);
}
async function delPlan(pid: string): Promise<void> {
  if (!await onay(C("Plan silinsin mi? Drive'daki yedek dosyalarına dokunulmaz."))) return;
  const j = await api("/api/plan/delete?plan=" + encodeURIComponent(pid), { method: "POST" });
  flash(j.msg || "", j.ok);
  void refresh();
}
async function logout(): Promise<void> {
  // Cikis her zaman sorulur. Yanlislikla (ya da beklenmedik bir yoldan)
  // tetiklenen bir cikis, "beni hatirla" oturumunu sessizce yok ediyordu.
  const metin = dirty
    ? C("Kaydedilmemiş değişiklikler var. Yine de çıkılsın mı?")
    : C("Oturumu kapatmak istediğine emin misin?\n"
      + "\"Beni hatırla\" işaretlemiş olsan bile hatırlanan oturum silinir.");
  if (!await onay(metin, C("Çıkış"), C("Çık"), C("Vazgeç"))) return;
  await api("/logout", { method: "POST" });
  location.reload();
}

/** Basliktaki kullanici adi ve cikis dugmesi. */
function kullaniciCiz(): void {
  const e = document.getElementById("kullanici");
  if (!e || !S) return;
  e.textContent = S.user || "";
  e.title = C("Oturum sahibi");
}

/* ---------- plan sihirbazi ---------- */
const ADIMLAR = [C("Plan"), "Kaynak", "Hedef", "Saklama", "Zamanlama", C("Aktarım"), "Bildirim", C("Özet")];
/** Her adimda dogrulanacak alanlar. Ozet adiminda hepsi bir kez daha kontrol edilir. */
const ADIM_ALANLARI: string[][] = [
  ["e-name"], ["e-src"], ["e-acct", "e-folder"], ["e-kd", "e-kc", "e-td"],
  ["e-runat", "e-wvm", "e-mage"],
  ["e-bw", "e-bwsch", "e-tr", "e-ck", "e-chunk", "e-bwlink", "e-bwres", "e-bwmin", "e-bwmax", "e-bwint", "e-bwsm", "e-bwstep"],
  ["e-mail", "e-rmail", "e-rat", "e-rdays", "e-rstale", "e-rquota"], [],
];
let wAktif = 1;
let wSihirbaz = false;

function wGoster(): void {
  Array.prototype.slice.call(document.querySelectorAll(".wstep")).forEach((d: HTMLElement) => {
    const n = Number(d.getAttribute("data-step"));
    d.style.display = !wSihirbaz || n === wAktif ? "" : "none";
  });
  el("w-adimlar").style.display = wSihirbaz ? "" : "none";
  if (wSihirbaz) {
    setHtml("w-adimlar", ADIMLAR.map((ad, i) => {
      const n = i + 1;
      const sinif = n === wAktif ? "on" : (n < wAktif ? "ok" : "");
      return '<span class="' + sinif + '">' + n + ". " + esc(ad) + "</span>";
    }).join(""));
  }
  const son = wAktif === ADIMLAR.length;
  el("w-geri").style.display = wSihirbaz && wAktif > 1 ? "" : "none";
  el("w-ileri").style.display = wSihirbaz && !son ? "" : "none";
  el("w-kaydet").style.display = !wSihirbaz || son ? "" : "none";
  if (wSihirbaz && son) wOzet();
}

/** Yalnizca verilen alanlari dogrular; digerlerini bozmadan birakir. */
function wAdimGecerli(adim: number): boolean {
  const alanlar = ADIM_ALANLARI[adim - 1] || [];
  if (!alanlar.length) return true;
  const oncekiHatalar = alanlar.filter((id) => document.getElementById(id))
    .map((id) => [id, fld(id).classList.contains("bad")] as [string, boolean]);
  const tumu = validatePlan();
  if (tumu) return true;
  // Bu adimin alanlarindan biri hatali mi?
  const buAdimHatali = alanlar.some((id) => document.getElementById(id) && fld(id).classList.contains("bad"));
  if (!buAdimHatali) {
    // Hata baska adimda: bu adimin gorunumunu temizle, gecise izin ver
    oncekiHatalar.forEach(([id, vardi]) => { if (!vardi) good(id); });
    return true;
  }
  return false;
}

function wAdim(yon: number): void {
  if (yon > 0 && !wAdimGecerli(wAktif)) {
    hataOdakla();
    flash(C("bu adımda eksik veya hatalı alan var"), false);
    return;
  }
  wAktif = Math.min(ADIMLAR.length, Math.max(1, wAktif + yon));
  wGoster();
  if (wAktif === 4) void kapasiteYukle();
  el("m-edit").scrollTop = 0;
}

function wSatir(baslik: string, deger: string, uyari?: boolean): string {
  return '<tr' + (uyari ? ' class="uyari"' : "") + "><td>" + esc(baslik) + "</td><td>"
    + esc(deger) + "</td></tr>";
}
function wOzet(): void {
  const wd = Array.prototype.slice.call(el("e-wd").querySelectorAll("input:checked"))
    .map((c: HTMLInputElement) => WD[Number(c.value) - 1]);
  const bildirim = [chk("e-nsuc") ? C("başarılı") : "", chk("e-nerr") ? "hata" : "",
    chk("e-nskip") ? C("atlandı") : ""].filter(Boolean).join(", ") || C("hiçbiri");
  const oto = chk("e-bwauto");
  const hiz = oto ? ("otomatik (" + val("e-bwmin") + " – " + (val("e-bwmax") || val("e-bw")) + ")")
    : (val("e-bwsch") ? C("çizelge: ") + val("e-bwsch") : val("e-bw"));
  let h = '<table class="ozet"><tbody>';
  h += wSatir(C("Plan adı"), val("e-name"));
  h += wSatir("Durum", chk("e-enabled") ? "etkin" : C("kapalı (zamanlayıcı atlar)"), !chk("e-enabled"));
  h += wSatir("Kaynak", val("e-src"));
  h += wSatir("Hedef", (val("e-acct") || "?") + ":" + val("e-folder"));
  h += wSatir("Saklama", val("e-kd") + C(" gün · VM/CT başına en az ") + val("e-kc") + C(" set"));
  const yh = yhTopla();
  h += wSatir(C("Yedek hedefler"), yh.length
    ? yh.map((x, i) => (i + 1) + ". " + esc(x)).join("<br>")
    : C("yok — sadece birincil hedef"));
  h += wSatir(C("Host yapılandırması"), chk("e-hc")
    ? C("yedekleniyor · son ") + val("e-hck") + C(" arşiv saklanır")
      + (chk("e-hcj") ? C(" · JSON görüntü dahil") : "")
    : C("yedeklenmiyor"), !chk("e-hc"));
  h += wSatir(C("Çöp süresi"), val("e-td") + C(" gün sonra kalıcı silinir"));
  h += wSatir("Program", (wd.length ? wd.join(",") : C("her gün")) + C(" saat ") + val("e-runat"));
  h += wSatir(C("vzdump koruması"), chk("e-wv")
    ? C("bekle, en fazla ") + val("e-wvm") + C(" dk") : C("beklemeden çalış"), !chk("e-wv"));
  h += wSatir("Hatada retention", chk("e-pof")
    ? C("ÇALIŞIR — yükleme başarısızsa da siler") : C("çalışmaz (güvenli)"), chk("e-pof"));
  h += wSatir(C("Hız"), hiz);
  h += wSatir("Mail", (val("e-mail") || "—") + "  ·  " + bildirim);
  h += wSatir(C("Haftalık rapor"), chk("e-wr")
    ? WD[Number(val("e-rday")) - 1] + " " + val("e-rat") + " (" + val("e-rdays") + C(" günlük dönem)")
    : C("kapalı"));
  h += "</tbody></table>";
  const uyarilar: string[] = [];
  if (!val("e-acct")) uyarilar.push(C("Google hesabı seçilmedi — 3. adıma dön."));
  if (Number(val("e-kc")) === 0) uyarilar.push(C("Güvenlik tabanı 0: uzun süre yedeklenmeyen bir VM/CT'nin tüm yedekleri silinebilir."));
  if (chk("e-pof")) uyarilar.push(C("Hatada retention açık: yeni yedek çıkmadan eskiler silinebilir."));
  if (!val("e-mail") && (chk("e-nsuc") || chk("e-nerr") || chk("e-wr")))
    uyarilar.push(C("Bildirim seçili ama alıcı adresi boş."));
  if (uyarilar.length) {
    h += '<div class="hint" style="margin-top:10px;color:#ffd479">'
      + uyarilar.map((u) => "⚠ " + esc(u)).join("<br>") + "</div>";
  }
  setHtml("w-ozet", h);
}

/** Hesap ekleme paneli tek bir DOM parcasidir; sihirbaz ile modal arasinda tasinir. */
/* ---------- yedek hedefler ---------- */
// Basarisizlikta sirayla denenecek hedefler. Satirlar dinamik: hesap secimi +
// klasor. Kaydederken "hesap:klasor" dizisine cevrilir.
let YH: { hesap: string; klasor: string }[] = [];

function yhCiz(): void {
  const kutu = document.getElementById("e-yh-liste");
  if (!kutu) return;
  if (!YH.length) {
    kutu.innerHTML = '<div class="small">' + C("Yedek hedef yok — sadece birincil kullanılır.")
      + "</div>";
  } else {
    kutu.innerHTML = YH.map((h, i) =>
      '<div class="inline yh-satir" style="margin-bottom:6px">'
      + '<span class="small" style="width:18px;text-align:right">' + (i + 1) + ".</span>"
      + '<select class="yh-hesap" data-i="' + i + '" style="flex:0 0 40%"></select>'
      + '<input class="yh-klasor" data-i="' + i + '" style="flex:1" placeholder="'
      + esc(C("klasör")) + '" value="' + esc(h.klasor) + '">'
      + '<button class="sm" type="button" data-yukari="' + i + '" title="'
      + esc(C("Yukarı taşı")) + '"' + (i ? "" : " disabled") + ">↑</button>"
      + '<button class="sm warn" type="button" data-sil="' + i + '" title="'
      + esc(C("Bu hedefi kaldır")) + '">✕</button></div>').join("");
    // Hesap listeleri birincil hedefle ayni kaynaktan doldurulur
    const secenekler = REM.map((r) => '<option value="' + esc(r.name) + '">'
      + esc(r.name) + "</option>").join("");
    Array.prototype.slice.call(kutu.querySelectorAll(".yh-hesap"))
      .forEach((sel: HTMLSelectElement) => {
        const i = Number(sel.getAttribute("data-i"));
        sel.innerHTML = secenekler;
        sel.value = YH[i].hesap || (REM[0] && REM[0].name) || "";
        sel.onchange = () => { YH[i].hesap = sel.value; yhOzet(); markDirty(); };
      });
    Array.prototype.slice.call(kutu.querySelectorAll(".yh-klasor"))
      .forEach((g: HTMLInputElement) => {
        const i = Number(g.getAttribute("data-i"));
        g.oninput = () => { YH[i].klasor = g.value; yhOzet(); markDirty(); };
      });
    Array.prototype.slice.call(kutu.querySelectorAll("[data-sil]"))
      .forEach((b: HTMLElement) => {
        b.onclick = () => { YH.splice(Number(b.getAttribute("data-sil")), 1);
                            yhCiz(); markDirty(); };
      });
    Array.prototype.slice.call(kutu.querySelectorAll("[data-yukari]"))
      .forEach((b: HTMLElement) => {
        b.onclick = () => {
          const i = Number(b.getAttribute("data-yukari"));
          if (i < 1) return;
          const t = YH[i - 1]; YH[i - 1] = YH[i]; YH[i] = t;
          yhCiz(); markDirty();
        };
      });
  }
  yhOzet();
}

/** Ayni hesabin baska klasoru gercek koruma saglamaz; bunu soyle. */
function yhOzet(): void {
  const e = document.getElementById("e-yh-ozet");
  if (!e) return;
  const ana = val("e-acct");
  const ayni = YH.filter((h) => h.hesap === ana).length;
  e.textContent = !YH.length ? ""
    : ayni ? C("⚠ ") + ayni + C(" hedef birincil ile aynı hesapta — hesap kilitlenirse işe yaramaz")
           : YH.length + C(" yedek hedef tanımlı");
  e.className = "small" + (ayni ? " uyari-metin" : "");
}

function yhEkle(): void {
  YH.push({ hesap: val("e-acct") || (REM[0] && REM[0].name) || "", klasor: "" });
  yhCiz(); markDirty();
}

function yhTopla(): string[] {
  return YH.map((h) => (h.hesap || "").trim() + ":" + (h.klasor || "").trim())
    .filter((x) => x.length > 1 && !x.startsWith(":") && !x.endsWith(":"));
}

function yhDoldur(liste: string[]): void {
  YH = (liste || []).map((x) => {
    const i = String(x).indexOf(":");
    return i < 0 ? { hesap: String(x), klasor: "" }
                 : { hesap: String(x).slice(0, i), klasor: String(x).slice(i + 1) };
  });
  yhCiz();
}

function hesapPaneliTasi(hedefId: string, gorunur: boolean): void {
  const panel = el("hesap-ekle-panel");
  const yuva = document.getElementById(hedefId);
  if (yuva && panel.parentElement !== yuva) yuva.appendChild(panel);
  panel.style.display = gorunur ? "" : "none";
}
function wHesapEkle(): void {
  hesapPaneliTasi("w-hesap-yuvasi", true);
  acctTab(1);
  fld("a-name").focus();
}

/* ---------- plan duzenleyici ---------- */
interface Preset {
  keep_days: number; keep_count: number; drive_trash_days: number; run_at: string;
  weekdays: number[]; bwlimit: string; transfers: number; checkers: number; drive_chunk: string;
  min_age_min: number; vzdump_wait_min: number; weekly_report: boolean; report_day: number;
  report_at: string; report_days: number; report_stale_days: number; report_quota_warn: number;
}
const PRESETS: Record<string, Preset> = {
  az: { keep_days: 3, keep_count: 3, drive_trash_days: 1, run_at: "03:00", weekdays: [],
    bwlimit: "30M", transfers: 2, checkers: 4, drive_chunk: "64M", min_age_min: 10,
    vzdump_wait_min: 120, weekly_report: true, report_day: 1, report_at: "09:00",
    report_days: 7, report_stale_days: 2, report_quota_warn: 90 },
  gunluk: { keep_days: 14, keep_count: 3, drive_trash_days: 1, run_at: "03:00", weekdays: [],
    bwlimit: "30M", transfers: 2, checkers: 4, drive_chunk: "64M", min_age_min: 10,
    vzdump_wait_min: 60, weekly_report: true, report_day: 1, report_at: "09:00",
    report_days: 7, report_stale_days: 2, report_quota_warn: 90 },
  haftalik: { keep_days: 180, keep_count: 4, drive_trash_days: 7, run_at: "05:00", weekdays: [7],
    bwlimit: "10M", transfers: 1, checkers: 4, drive_chunk: "64M", min_age_min: 15,
    vzdump_wait_min: 120, weekly_report: true, report_day: 1, report_at: "09:00",
    report_days: 30, report_stale_days: 8, report_quota_warn: 85 },
  kritik: { keep_days: 30, keep_count: 7, drive_trash_days: 3, run_at: "02:00", weekdays: [],
    bwlimit: "50M", transfers: 3, checkers: 8, drive_chunk: "64M", min_age_min: 10,
    vzdump_wait_min: 180, weekly_report: true, report_day: 1, report_at: "08:00",
    report_days: 7, report_stale_days: 1, report_quota_warn: 80 },
  test: { keep_days: 2, keep_count: 1, drive_trash_days: 0.5, run_at: "22:00", weekdays: [],
    bwlimit: "5M", transfers: 1, checkers: 2, drive_chunk: "32M", min_age_min: 1,
    vzdump_wait_min: 5, weekly_report: false, report_day: 1, report_at: "09:00",
    report_days: 7, report_stale_days: 2, report_quota_warn: 90 },
};
function preset(k: string): void {
  const v = PRESETS[k];
  if (!v) return;
  setVal("e-kd", v.keep_days); setVal("e-kc", v.keep_count); setVal("e-td", v.drive_trash_days);
  setVal("e-ck", v.checkers); setVal("e-chunk", v.drive_chunk); setVal("e-mage", v.min_age_min);
  setVal("e-wvm", v.vzdump_wait_min); setChk("e-wr", v.weekly_report); setVal("e-rday", v.report_day);
  setVal("e-rat", v.report_at); setVal("e-rdays", v.report_days);
  setVal("e-rstale", v.report_stale_days); setVal("e-rquota", v.report_quota_warn);
  Array.prototype.slice.call(el("e-wd").querySelectorAll("input")).forEach((c: HTMLInputElement) => {
    c.checked = v.weekdays.indexOf(Number(c.value)) >= 0;
  });
  setVal("e-bwsch", ""); good("e-bwsch");
  ramHint(); saklamaIpucu(); markDirty();
  flash(C("senaryo yüklendi — kaydetmeden uygulanmaz"), true);
}
/** Saklama alanlarinin yardim metni yazilan degere gore guncellenir.
 *  Sabit "14 gun" ornegi, alanda 3 yazarken yaniltiyordu. */
function saklamaIpucu(): void {
  const gun = Number(val("e-kd")) || 0;
  const taban = Number(val("e-kc")) || 0;
  const cop = Number(val("e-td")) || 0;
  const gunluk = KAP && KAP.analiz && KAP.analiz.ok ? (KAP.analiz.gunluk || 0) : 0;

  let a = gun === 0
    ? C("Gün kuralı kapalı — yalnızca aşağıdaki set tabanı korur.")
    : gun + " " + C("günden eski setler Google çöp kutusuna gönderilir.")
      + " " + C("Günlük yedek alıyorsan Drive'da yaklaşık") + " " + gun + " " + C("set durur.");
  if (gunluk) a += " " + C("Ölçülene göre") + " ≈ " + hb(gunluk * gun) + ".";
  setHtml("eg-kd", a);

  let b2 = taban === 0
    ? '<b style="color:#ffd479">' + C("0 riskli: gün kuralı bir VM/CT'nin tüm yedeklerini silebilir.") + "</b>"
    : C("Bir VM/CT uzun süre yedeklenmese bile en yeni") + " <b>" + taban + "</b> "
      + C("seti gün kuralından muaf tutulur.");
  if (taban && gun && taban >= gun)
    b2 += " " + C("Taban gün sayısından büyük ya da eşit: pratikte saklamayı taban belirler.");
  setHtml("eg-kc", b2);

  setHtml("eg-td", cop === 0
    ? C("0 = çöpe uğramadan hemen kalıcı silinir; yanlış silmede geri dönüş olmaz.")
    : C("Yanlış silme olursa") + " <b>" + cop + " " + C("gün") + "</b> "
      + C("içinde Drive'ın çöp kutusundan geri alabilirsin.")
      + (gunluk ? " " + C("Çöpte yaklaşık") + " " + hb(gunluk * cop) + " " + C("bekler.") : ""));
}

function ramHint(): void {
  const c = String(val("e-chunk") || "").match(/^(\d+(?:\.\d+)?)([KMG])$/i);
  const t = Number(val("e-tr")) || 1;
  if (!c) { setTxt("e-ram", C("RAM ≈ parça × transfer")); return; }
  const carp: Record<string, number> = { K: 1 / 1024, M: 1, G: 1024 };
  const mb = parseFloat(c[1]) * carp[c[2].toUpperCase()];
  setTxt("e-ram", C("Tahmini rclone RAM kullanımı: ") + Math.round(mb * t) + " MB ("
    + c[0] + " × " + t + " transfer)");
}

function openEditor(pid: string | null): void {
  const p = pid && S ? S.plans.filter((x) => x.id === pid)[0] : undefined;
  EDIT = pid || null;
  dirty = false;
  wSihirbaz = !pid;                 // yeni plan: sihirbaz, mevcut plan: tek sayfa form
  wAktif = 1;
  setTxt("ed-title", p ? C("Plan: ") + p.name : C("🧭 Yeni plan sihirbazı"));
  setTxt("ed-alt", p
    ? C("Tüm ayarlar tek sayfada. Alan adlarının üstüne gelince açıklama çıkar.")
    : C("Adım adım ilerle. Hiçbir şey kaydedilmez, son adımda onaylarsın."));
  const d = {
    name: "", enabled: true, src_dir: "/var/lib/vz/dump", remote: C("gdrive:proxmox-yedek"),
    keep_days: 14, keep_count: 3, drive_trash_days: 1, run_at: "03:00", weekdays: [] as number[],
    bwlimit: "30M", bwlimit_schedule: "", bwlimit_upload_only: true,
    transfers: 2, checkers: 4, drive_chunk: "64M", rclone_extra: [] as string[],
    mail_to: "", smtp_profile: "", notify_success: true, notify_failure: true, notify_skipped: false,
    wait_for_vzdump: true, vzdump_wait_min: 60, min_age_min: 10,
    skip_patterns: ["*.dat", "*.tmp", "*.part"], prune_on_failure: false, weekly_report: true,
    host_config_enabled: true, host_config_json: true, host_config_keep_count: 30,
    report_day: 1, report_at: "09:00", report_days: 7, report_stale_days: 2,
    report_quota_warn: 90, report_mail_to: "",
  };
  const v = (p || d) as unknown as Plan;
  // Ortak alanlar tablodan doldurulur (bkz. alanlar.ts); asagidakiler ozel durumlar.
  alanlariDoldur(v as unknown as Record<string, unknown>);
  const rp = String(v.remote || C("gdrive:proxmox-yedek")).split(":");
  setVal("e-folder", rp.slice(1).join(":"));
  void loadIfaces(v.bw_auto_iface || ""); bwAutoUygula();
  setHtml("e-rday", WD.map((n, i) => '<option value="' + (i + 1) + '">' + n + "</option>").join(""));
  setVal("e-rday", v.report_day || 1);
  setHtml("e-wd", WD.map((n, i) => '<label><input type="checkbox" value="' + (i + 1) + '"'
    + ((v.weekdays || []).indexOf(i + 1) >= 0 ? " checked" : "") + ">" + n + "</label>").join(""));
  setTxt("e-srchint", p ? (p.src_exists ? p.src_dumps + " dosya bulundu" : C("⚠ klasör bulunamadı")) : "");
  void loadRemotes(rp[0]).then(() => yhDoldur((v.yedek_hedefler as string[]) || []));
  loadSmtpSelect(v.smtp_profile); void loadStorages();
  ramHint(); saklamaIpucu(); bwLinkKipi();
  Array.prototype.slice.call(document.querySelectorAll("#m-edit input,#m-edit select"))
    .forEach((e: HTMLElement) => { e.oninput = markDirty; e.onchange = markDirty; });
  fld("e-bwlmode").onchange = () => { bwLinkKipi(); markDirty(); };
  fld("e-acct").onchange = () => { yhOzet(); markDirty(); };
  fld("e-kd").oninput = () => { kapasiteCiz(); saklamaIpucu(); markDirty(); };
  fld("e-kc").oninput = () => { saklamaIpucu(); markDirty(); };
  fld("e-td").oninput = () => { kapasiteCiz(); saklamaIpucu(); markDirty(); };
  fld("e-chunk").oninput = () => { ramHint(); markDirty(); };
  fld("e-tr").oninput = () => { ramHint(); markDirty(); };
  hesapPaneliTasi("w-hesap-yuvasi", false);
  ceviriUygula();
  KAP = null; kapAnahtar = "";
  wGoster();
  if (!wSihirbaz) void kapasiteYukle();
  openM("m-edit");
}

function validatePlan(): boolean {
  // Alanlarin tamami tablodan dogrulanir (bkz. alanlar.ts).
  // Burada yalnizca birden fazla alani birlikte ilgilendiren kurallar kalir.
  hataTuruBaslat();
  let ok = alanlariDogrula();
  if (!val("e-acct")) ok = bad("e-acct", C("önce bir Google hesabı ekle")) && ok; else good("e-acct");
  ok = vRx("e-folder", RX.folder, C('klasör adında : * ? " < > | olamaz')) && ok;
  if (!val("e-folder").trim()) ok = bad("e-folder", C("hedef klasör gerekli")) && ok;
  if (chk("e-bwauto")) {
    const alt = bwBytes(val("e-bwmin")), ust = bwBytes(val("e-bwmax"));
    if (ust && alt && alt > ust) ok = bad("e-bwmin", C("alt sınır üst sınırdan büyük olamaz")) && ok;
  }
  if (Number(val("e-kc")) === 0 && Number(val("e-kd")) === 0) {
    ok = bad("e-kc", C("ikisi birden 0 olamaz — hiç yedek kalmaz")) && ok;
  }
  return ok;
}

async function savePlan(): Promise<void> {
  if (!validatePlan()) { hataOdakla(); flash(C("form hatalı — kırmızı alanlara bak"), false); return; }
  const wd = Array.prototype.slice.call(el("e-wd").querySelectorAll("input:checked"))
    .map((c: HTMLInputElement) => Number(c.value));
  const body: Record<string, unknown> = {
    ...alanlariTopla(),                       // tum ortak alanlar (bkz. alanlar.ts)
    yedek_hedefler: yhTopla(),
    id: EDIT,
    remote: val("e-acct") + ":" + val("e-folder").trim().replace(/^\/+/, ""),
    weekdays: wd,
  };
  const j = await api("/api/plan/save", { method: "POST", body: JSON.stringify(body) });
  flash(j.msg || "", j.ok);
  if (j.ok) {
    dirty = false; wSihirbaz = false; closeM("m-edit");
    sel = j.id || sel; remember(); taslakSil(); void refresh();
  }
}

/* ---------- kapasite planlayici ---------- */
interface Analiz {
  ok: boolean; hata?: string; dosya?: number; set_sayisi?: number;
  toplam?: number; gunluk?: number;
  misafirler?: { ad: string; toplam: number; set_basina: number; pay: number }[];
}
let KAP: { analiz: Analiz; kota: Quota; oneri?: number; oneri_pay_pct?: number } | null = null;
let kapAnahtar = "";

/** Kaynak klasoru olcup secilen hesabin kotasina gore projeksiyon gosterir.
 *  Saklama suresini tahminle degil olcumle secmek icin. */
async function kapasiteYukle(zorla?: boolean): Promise<void> {
  const src = val("e-src").trim(), hesap = val("e-acct");
  if (!src || !hesap) { setTxt("kap-durum", C("Kaynak ve hesap seçilince kapasite hesabı burada çıkar.")); return; }
  const anahtar = src + "|" + hesap;
  if (!zorla && anahtar === kapAnahtar && KAP) { kapasiteCiz(); return; }
  setTxt("kap-durum", C("ölçülüyor…"));
  try {
    KAP = await api<{ analiz: Analiz; kota: Quota; oneri?: number; oneri_pay_pct?: number }>(
      "/api/analiz?src=" + encodeURIComponent(src) + "&hesap=" + encodeURIComponent(hesap));
    kapAnahtar = anahtar;
  } catch { setTxt("kap-durum", C("ölçüm başarısız")); return; }
  kapasiteCiz(); saklamaIpucu();
}

function kapasiteCiz(): void {
  if (!KAP) return;
  const a = KAP.analiz, q = KAP.kota || {};
  const goster = (id: string, g: boolean) => { el(id).style.display = g ? "" : "none"; };
  if (!a.ok) {
    setHtml("kap-durum", '<span class="kap-hata">⚠ ' + esc(a.hata || C("ölçülemedi")) + "</span>");
    ["kap-bar", "kap-alt", "kap-btn"].forEach((i) => goster(i, false));
    setHtml("kap-misafir", "");
    return;
  }
  const gunluk = a.gunluk || 0;
  const gun = Number(val("e-kd")) || 0, cop = Number(val("e-td")) || 0;
  const gereken = gunluk * (gun + cop);
  const toplam = Number(q.total) || 0, kullanilan = Number(q.used) || 0, bos = Number(q.free) || 0;
  const sonraPct = toplam ? ((kullanilan + gereken) / toplam) * 100 : 0;
  const mevcutPct = toplam ? (kullanilan / toplam) * 100 : 0;
  const sigar = gereken < bos;

  setHtml("kap-durum",
    C("Ölçüldü: günde <b>") + hb(gunluk) + "</b> üretiliyor ("
    + (a.set_sayisi || 0) + C(" günlük set, toplam ") + hb(a.toplam) + ").<br>"
    + "<b>" + gun + C(" gün</b> saklama + <b>") + cop + C(" gün</b> çöp → Drive'da <b>") + hb(gereken)
    + "</b> gerekir.");
  goster("kap-bar", true); goster("kap-alt", true); goster("kap-btn", true);
  const bar = el("kap-bar");
  bar.className = "kap-bar" + (!sigar ? " tasma" : (sonraPct >= 80 ? " uyari" : ""));
  el("kap-mevcut").style.width = Math.min(100, mevcutPct) + "%";
  el("kap-yeni").style.width = Math.min(100 - Math.min(100, mevcutPct),
    toplam ? (gereken / toplam) * 100 : 0) + "%";
  setHtml("kap-alt",
    "<span>şu an dolu: " + hb(kullanilan) + "</span>"
    + "<span>bu planla: <b>%" + sonraPct.toFixed(1) + "</b></span>"
    + "<span>hesap: " + hb(toplam) + "</span>");

  let uyari = "";
  if (KAP.kota && (KAP.kota as unknown as Record<string, unknown>).bekliyor) {
    uyari = C("Kota ölçülüyor, birkaç saniye sonra tekrar bak.");
  } else if (!toplam) {
    uyari = C("Kota okunamadı — doluluk hesaplanamıyor. Gereken alan yine de doğru.");
  } else if (!sigar) {
    uyari = C("⚠ Bu süre hesaba <b>sığmaz</b>: ") + hb(gereken) + " gerekiyor, " + hb(bos) + C(" boş var.");
  } else if (sonraPct >= 85) {
    uyari = "⚠ Hesap %" + sonraPct.toFixed(0) + C(" dolar. VM/CT'ler büyürse yer biter.");
  }
  if (KAP.oneri) {
    // "Onerilen" demek yaniltiyordu: bu en iyi secim degil, bos alana guvenle
    // sigabilecek en uzun sure. Kisa sure daha az yer kaplar.
    uyari += (uyari ? "<br>" : "")
      + C("Sığabilecek en uzun süre: <b>") + KAP.oneri + C(" gün</b> (boş alanın %")
      + (KAP.oneri_pay_pct || 60) + C("'ini kullanır). Bu bir tavsiye değil, üst sınır.")
      + "<br>" + C("Kısa süre daha az yer kaplar; uzun süre geç fark edilen bir soruna karşı daha geniş geri dönüş penceresi verir.");
  }
  const ilk = "<br>İlk yükleme <b>" + hb(a.toplam) + "</b> olur (kaynakta " + (a.set_sayisi || 0)
    + C(" set var); hedef doluluğa ancak ") + gun + C(" gün sonra ulaşılır.");
  setHtml("kap-misafir",
    (uyari ? '<div class="kap-uyari">' + uyari + ilk + "</div>" : '<div class="kap-uyari">' + ilk.slice(4) + "</div>")
    + "<table><tbody>" + (a.misafirler || []).map((m) =>
      "<tr><td>" + esc(m.ad) + "</td><td>set başına " + hb(m.set_basina)
      + " · %" + m.pay + "</td></tr>").join("") + "</tbody></table>");
}

function kapasiteOner(): void {
  if (!KAP || !KAP.oneri) return;
  setVal("e-kd", KAP.oneri); good("e-kd"); markDirty(); kapasiteCiz();
  flash(KAP.oneri + C(" gün uygulandı"), true);
}

/* ---------- klasor gezgini ---------- */
async function loadStorages(): Promise<void> {
  try {
    const j = await api<{ storages: { name: string; path: string; dumps: number }[] }>("/api/storages");
    const s = j.storages || [];
    setHtml("e-stor", s.length ? C("Proxmox depoları: ") + s.map((x) =>
      "<a href=\"#\" onclick=\"setSrc('" + x.path + "');return false\" style=\"color:#58a6ff\">"
      + esc(x.name) + " (" + x.dumps + ")</a>").join(" · ") : "");
  } catch { /* yok say */ }
}
function setSrc(path: string): void { setVal("e-src", path); markDirty(); }
async function openBrowser(): Promise<void> { await goDir(val("e-src") || ""); openM("m-browse"); }
async function goDir(p: string): Promise<void> {
  const j = await api<BrowseResult>("/api/browse?path=" + encodeURIComponent(p));
  cur = j.path;
  setTxt("b-path", j.path + (j.error ? "  ⚠ " + j.error : "  (" + j.dumps + " dosya)"));
  setHtml("b-stor", (j.roots || []).map((r) => "<button class=\"sm\" onclick=\"goDir('" + r + "')\">"
    + esc(r) + "</button>").join(""));
  let h = j.parent ? "<div onclick=\"goDir('" + j.parent + C("')\"><span>⬆ üst klasör</span><span></span></div>") : "";
  h += (j.dirs || []).map((d) => "<div onclick=\"goDir('" + d.path + "')\"><span>📁 " + esc(d.name)
    + '</span><span class="small">' + (d.dumps ? d.dumps + " dosya" : "") + "</span></div>").join("");
  setHtml("b-list", h || '<div><span class="small">alt klasör yok</span><span></span></div>');
}
function pickHere(): void {
  setVal("e-src", cur); markDirty(); closeM("m-browse");
  void api<BrowseResult>("/api/browse?path=" + encodeURIComponent(cur))
    .then((j) => setTxt("e-srchint", j.dumps + " dosya bulundu"));
}

/* ---------- Google hesaplari ---------- */
async function loadRemotes(selName?: string): Promise<void> {
  try { const j = await api<{ remotes: Remote[] }>("/api/remotes"); REM = j.remotes || []; }
  catch { REM = []; }
  setHtml("e-acct", REM.map((r) => '<option value="' + esc(r.name) + '">' + esc(r.name)
    + "</option>").join("") || '<option value="">(hesap yok)</option>');
  if (selName) setVal("e-acct", selName);
  setTxt("e-accthint", REM.length ? REM.length + C(" hesap tanımlı") : C("Henüz hesap yok — 'Yönet' ile ekle."));
}
/* ---------- saglayicilar ---------- */
interface Saglayici {
  tur: string; ad: string; simge: string; dogrulandi: boolean; not: string; kurulu: boolean;
}
let SAG: Saglayici[] = [];

async function saglayicilariYukle(): Promise<void> {
  try {
    const j = await api<{ saglayicilar: Saglayici[] }>("/api/saglayicilar");
    SAG = (j.saglayicilar || []).filter((x) => x.kurulu);
  } catch { SAG = []; }
  setHtml("a-tur", SAG.map((x) => '<option value="' + esc(x.tur) + '">'
    + esc(x.simge + " " + x.ad) + (x.dogrulandi ? "" : C("  (denenmedi)"))
    + "</option>").join(""));
  saglayiciDegisti();
}

function saglayiciDegisti(): void {
  const x = SAG.filter((y) => y.tur === val("a-tur"))[0];
  const e = document.getElementById("a-turhint");
  if (!e) return;
  if (!x) { e.textContent = ""; return; }
  // Neyin gercekten denendigini sakla: "calisiyor gibi duruyor" demek yaniltir.
  e.innerHTML = (x.not ? esc(C(x.not)) + " " : "")
    + (x.dogrulandi
        ? '<b style="color:#7ee2a8">' + esc(C("Gerçek hesapla uçtan uca doğrulandı.")) + "</b>"
        : '<b style="color:#ffd479">' + esc(C("OAuth akışı çalışıyor ama yükleme/saklama "
            + "davranışı gerçek hesapla denenmedi. Önce küçük bir planla dene.")) + "</b>");
}

function openAccounts(): void {
  openM("m-acct"); hesapPaneliTasi("hesap-ekle-yuvasi", true); acctTab(1);
  void saglayicilariYukle(); void renderAccounts();
  void api<AuthStatus>("/api/remote/auth/status").then((j) => {
    if (j.waiting && j.url) { el("a-authbox").style.display = ""; setTxt("a-url", j.url); pollAuth(); }
  });
}
function acctTab(n: number): void {
  el("a-tab1").className = n === 1 ? "on" : "";
  el("a-tab2").className = n === 2 ? "on" : "";
  el("a-m1").style.display = n === 1 ? "" : "none";
  el("a-m2").style.display = n === 2 ? "" : "none";
}
async function renderAccounts(): Promise<void> {
  const j = await api<{ remotes: Remote[] }>("/api/remotes?quota=1");
  REM = j.remotes || [];
  setHtml("a-list", REM.length ? REM.map((r) => {
    const q = r.quota || {};
    const line = q.ok ? hb(q.used) + " / " + hb(q.total) + C("  ·  çöp ") + hb(q.trashed || 0)
      + C("  ·  boş ") + hb(q.free || 0) : "⚠ " + esc(q.error || C("kota okunamadı"));
    return '<div class="card" data-hesap="' + esc(r.name) + '" style="margin-bottom:8px"><div style="display:flex;'
      + 'justify-content:space-between;gap:8px;align-items:center;flex-wrap:wrap">'
      + "<b>" + esc(r.name) + '</b> <span class="small">' + esc(r.type) + "</span>"
      + '<span style="flex:1"></span>'
      + "<button class=\"sm\" onclick=\"acctTest('" + r.name + "')\">Test</button>"
      + "<button class=\"sm warn\" onclick=\"acctDel('" + r.name + "')\">" + C("Sil")
      + "</button></div>"
      + '<div class="small" style="margin-top:6px">' + line + "</div></div>";
  }).join("") : '<div class="small">Henüz hesap yok.</div>');
}
async function acctTest(n: string): Promise<void> {
  flash("kontrol ediliyor…", true);
  const j = await api("/api/remote/test?name=" + encodeURIComponent(n), { method: "POST" });
  flash(j.msg || "", j.ok);
}
async function acctDel(n: string): Promise<void> {
  if (!await onay("'" + n + C("' kaldırılsın mı? Drive'daki dosyalara dokunulmaz."))) return;
  const j = await api("/api/remote/delete?name=" + encodeURIComponent(n), { method: "POST" });
  flash(j.msg || "", j.ok); void renderAccounts(); void loadRemotes();
}
async function acctPaste(): Promise<void> {
  if (!RX.acct.test(val("a-name").trim())) { bad("a-name", "sadece harf, rakam, - ve _"); return; }
  good("a-name");
  try { JSON.parse(val("a-token")); } catch { bad("a-token", C("geçerli JSON değil")); return; }
  good("a-token");
  const j = await api("/api/remote/add", { method: "POST",
    body: JSON.stringify({ name: val("a-name"), token: val("a-token"),
                           tur: val("a-tur") || "drive" }) });
  flash(j.msg || "", j.ok);
  if (j.ok) {
    setVal("a-token", ""); setVal("a-name", "");
    void renderAccounts(); void loadRemotes(j.name);
    if (wSihirbaz) { hesapPaneliTasi("w-hesap-yuvasi", false); good("e-acct"); markDirty(); }
  }
}
async function authStart(): Promise<void> {
  if (!RX.acct.test(val("a-name").trim())) { bad("a-name", C("önce geçerli bir hesap adı yaz")); return; }
  good("a-name");
  const j = await api<AuthStart>("/api/remote/auth/start", { method: "POST",
    body: JSON.stringify({ tur: val("a-tur") || "drive" }) });
  setVal("a-tunnel", j.tunnel || "");
  if (!j.ok) { flash(j.msg || C("başlatılamadı"), false); return; }
  el("a-authbox").style.display = "";
  setTxt("a-url", j.url || "");
  flash(C("adresi tarayıcında aç"), true);
  pollAuth();
}
function pollAuth(): void {
  window.clearInterval(authTimer);
  authTimer = window.setInterval(() => {
    void (async () => {
      const st = await api<AuthStatus>("/api/remote/auth/status");
      if (st.ready) {
        window.clearInterval(authTimer);
        setTxt("a-wait", C("jeton alındı, hesap oluşturuluyor…"));
        const j = await api("/api/remote/auth/finish", { method: "POST",
          body: JSON.stringify({ name: val("a-name") }) });
        flash(j.msg || "", j.ok);
        el("a-authbox").style.display = "none";
        if (j.ok) {
          setVal("a-name", ""); void renderAccounts(); void loadRemotes(j.name);
          if (wSihirbaz) { hesapPaneliTasi("w-hesap-yuvasi", false); good("e-acct"); markDirty(); }
        }
      } else if (!st.waiting) {
        window.clearInterval(authTimer);
        setTxt("a-wait", C("yetkilendirme sonlandı"));
      }
    })();
  }, 2000);
}
async function authCancel(): Promise<void> {
  window.clearInterval(authTimer);
  await api("/api/remote/auth/cancel", { method: "POST" });
  el("a-authbox").style.display = "none";
  flash("iptal edildi", true);
}

/* ---------- SMTP profilleri ---------- */
interface SmtpPreset { host: string; port: number; security: string; hint: string }
const SMTP_PRESETS: Record<string, SmtpPreset> = {
  gmail: { host: "smtp.gmail.com", port: 587, security: "starttls",
    hint: C("Gmail hesap şifresi çalışmaz. Google Hesabı → Güvenlik → 2 Adımlı Doğrulama → Uygulama şifreleri'nden 16 haneli şifre üret.") },
  outlook: { host: "smtp.office365.com", port: 587, security: "starttls",
    hint: C("Microsoft 365 / Outlook. Kurumsal hesaplarda SMTP AUTH kapalı olabilir, yöneticiden açtırman gerekebilir.") },
  yandex: { host: "smtp.yandex.com", port: 465, security: "ssl",
    hint: C("Yandex'te 'Uygulama şifreleri' bölümünden şifre üret. Kullanıcı adı tam adres olmalı.") },
  yahoo: { host: "smtp.mail.yahoo.com", port: 465, security: "ssl",
    hint: C("Yahoo'da uygulama şifresi zorunlu, normal şifre kabul edilmez.") },
  custom: { host: "", port: 587, security: "starttls",
    hint: C("Sunucu, port ve güvenlik ayarını sağlayıcından öğren.") },
};
function smtpPreset(): void {
  const v = SMTP_PRESETS[val("s-preset")];
  if (!v) return;
  if (v.host) setVal("s-host", v.host);
  setVal("s-port", v.port); setVal("s-sec", v.security);
  setTxt("s-presethint", v.hint);
}
function openSmtp(): void { openM("m-smtp"); smtpClear(); renderSmtp(); }
function loadSmtpSelect(selId?: string): void {
  SMTP = (S && S.smtp) || [];
  setHtml("e-smtp", SMTP.map((x) => '<option value="' + esc(x.id) + '">' + esc(x.name)
    + " (" + esc(x.user || x.host) + ")</option>").join("") || '<option value="">(profil yok)</option>');
  if (selId) setVal("e-smtp", selId);
  setTxt("e-smtphint", SMTP.length ? "" : C("Mail profili yok — '✉ Yönet' ile ekle, yoksa mail gitmez."));
}
function renderSmtp(): void {
  SMTP = (S && S.smtp) || [];
  setHtml("s-list", SMTP.length ? SMTP.map((x) =>
    '<div class="card" data-smtp="' + esc(x.id) + '" style="margin-bottom:8px"><div style="display:flex;justify-content:space-between;'
    + 'gap:8px;align-items:center;flex-wrap:wrap"><b>' + esc(x.name) + "</b>"
    + '<span style="flex:1"></span>'
    + "<button class=\"sm\" onclick=\"smtpEdit('" + x.id + "')\">" + C("Düzenle") + "</button>"
    + "<button class=\"sm\" onclick=\"smtpTest('" + x.id + "')\">Test maili</button>"
    + "<button class=\"sm warn\" onclick=\"smtpDel('" + x.id + "')\">" + C("Sil")
    + "</button></div>"
    + '<div class="small" style="margin-top:6px">' + esc(x.user || "-") + " · " + esc(x.host)
    + ":" + x.port + " · " + esc(x.security) + "</div></div>").join("")
    : '<div class="small">Henüz profil yok.</div>');
}
function smtpClear(): void {
  setVal("s-id", ""); setVal("s-name", ""); setVal("s-host", "smtp.gmail.com");
  setVal("s-port", 587); setVal("s-sec", "starttls"); setVal("s-user", "");
  setVal("s-pass", ""); setVal("s-from", ""); setVal("s-preset", "");
  setTxt("s-presethint", ""); setTxt("s-formtitle", "Yeni profil");
}
function smtpEdit(id: string): void {
  const x = SMTP.filter((y) => y.id === id)[0];
  if (!x) return;
  setVal("s-id", x.id); setVal("s-name", x.name); setVal("s-host", x.host);
  setVal("s-port", x.port); setVal("s-sec", x.security); setVal("s-user", x.user);
  setVal("s-pass", ""); setVal("s-from", x.from);
  setTxt("s-formtitle", C("Düzenle: ") + x.name);
}
async function smtpSave(): Promise<void> {
  let ok = true;
  ok = vTxt("s-name", C("profil adı gerekli")) && ok;
  ok = vRx("s-host", RX.host, C("geçerli bir sunucu adı yaz")) && ok;
  ok = vNum("s-port", 1, 65535, C("1-65535 arası port")) && ok;
  ok = vMails("s-user", true) && ok;
  ok = vMails("s-from", true) && ok;
  if (!ok) { flash(C("form hatalı"), false); return; }
  const b: Record<string, unknown> = {
    id: val("s-id"), name: val("s-name"), host: val("s-host"), port: Number(val("s-port")),
    security: val("s-sec"), user: val("s-user"), from: val("s-from"),
  };
  if (val("s-pass")) b.pass = val("s-pass");
  if (!val("s-id") && !val("s-pass")) { flash(C("yeni profil için şifre gerekli"), false); return; }
  const j = await api("/api/smtp/save", { method: "POST", body: JSON.stringify(b) });
  flash(j.msg || "", j.ok);
  if (j.ok) { smtpClear(); await refresh(); renderSmtp(); loadSmtpSelect(); }
}
async function smtpDel(id: string): Promise<void> {
  if (!await onay(C("Profil silinsin mi?"))) return;
  const j = await api("/api/smtp/delete?id=" + encodeURIComponent(id), { method: "POST" });
  flash(j.msg || "", j.ok);
  await refresh(); renderSmtp(); loadSmtpSelect();
}
async function smtpTest(id: string): Promise<void> {
  const to = await sorMetin(C("Test maili hangi adrese gitsin?\n(boş bırakırsan gönderen adresine gider)"), "");
  if (to === null) return;
  flash(C("gönderiliyor…"), true);
  const j = await api("/api/smtp/test?id=" + encodeURIComponent(id) + "&to=" + encodeURIComponent(to),
    { method: "POST" });
  flash(j.msg || "", j.ok);
}

/* ---------- genel ayarlar ---------- */
/** Telegram testi. Jeton kaydedilmemisse once kaydetmesi gerektigini soyle. */
async function tgTest(): Promise<void> {
  const e = document.getElementById("g-tgdurum");
  const yaz = (t: string, iyi: boolean): void => {
    if (e) { e.textContent = t; e.className = "small" + (iyi ? "" : " uyari-metin"); }
  };
  const jetonVar = Boolean(S && S.telegram_jeton_var) || Boolean(val("g-tgtoken").trim());
  if (!jetonVar) { yaz(C("önce bot jetonunu gir ve kaydet"), false); return; }
  if (val("g-tgtoken").trim()) {
    yaz(C("önce Kaydet'e bas, sonra test et"), false); return;
  }
  yaz(C("gönderiliyor…"), true);
  const j = await api("/api/telegram/test", { method: "POST",
    body: JSON.stringify({ chat: val("g-tgchat").trim() }) });
  yaz(j.msg || "", j.ok);
  flash(j.msg || "", j.ok);
}

/* ---------- bakim: ayar tasima, Proxmox linki, oturumlar ---------- */

/** Tarayici indirmesi: sunucu Content-Disposition ile dosya adini verir. */
function ayarIndir(sirlarla: boolean): void {
  if (sirlarla) {
    void onay(C("İndirilecek dosya SMTP şifrelerini düz metin içerecek.\n"
              + "Yalnızca güvendiğin bir yere kaydet."),
              C("Şifrelerle indir"), C("İndir"), C("Vazgeç")).then((e) => {
      if (e) window.location.href = "/api/disa-aktar?sirlar=1";
    });
    return;
  }
  window.location.href = "/api/disa-aktar";
}

function ayarYukleAc(): void { (el("s-dosya") as HTMLInputElement).click(); }

async function ayarYukle(dosya: File): Promise<void> {
  let veri: unknown;
  try { veri = JSON.parse(await dosya.text()); }
  catch { flash(C("dosya geçerli JSON değil"), false); return; }
  const d = veri as { plans?: unknown[]; smtp_profiles?: unknown[]; _surum?: string };
  const np = (d.plans || []).length, ns = (d.smtp_profiles || []).length;
  const kip = await onay(
    C("Dosyada ") + np + C(" plan, ") + ns + C(" mail profili var")
    + (d._surum ? C(" (sürüm ") + esc(d._surum) + ")" : "") + ".\n"
    + C("Mevcut planların korunsun mu, yoksa yerlerine bunlar mı geçsin?"),
    C("Ayar yükle"), C("Ekle (mevcutlar kalsın)"), C("Vazgeç"));
  if (!kip) return;
  const j = await api("/api/ice-aktar", { method: "POST",
    body: JSON.stringify({ veri, kip: "ekle" }) });
  flash(j.msg || "", j.ok);
  if (j.ok) { void refresh(); renderSmtp(); }
}

async function pveLinkDurum(): Promise<void> {
  const e = document.getElementById("s-pvelink");
  if (!e) return;
  try {
    const j = await api<{ ok: boolean; var: boolean; url: string; msg: string }>(
      "/api/proxmox-link");
    e.innerHTML = !j.ok ? esc(C("Durum okunamadı: ") + (j.msg || ""))
      : j.var ? "✅ " + esc(C("Link ekli: ")) + "<code>" + esc(j.url) + "</code>"
              : "○ " + esc(C("Link yok. Eklenecek adres: ")) + "<code>" + esc(j.url) + "</code>";
  } catch { e.textContent = C("durum okunamadı"); }
}

async function pveLink(ekle: boolean): Promise<void> {
  const j = await api("/api/proxmox-link", { method: "POST",
    body: JSON.stringify({ ekle }) });
  flash(j.msg || "", j.ok);
  void pveLinkDurum();
}

interface OturumSatir {
  onek: string; kullanici: string; adres: string;
  olusma: string; kalan_gun: number; bu_mu: boolean;
}

async function oturumlariYukle(): Promise<void> {
  const kutu = document.getElementById("s-oturumlar");
  if (!kutu) return;
  try {
    const j = await api<{ oturumlar: OturumSatir[];
                          ayarlar: Record<string, unknown> }>("/api/oturumlar");
    const o = j.oturumlar || [];
    kutu.innerHTML = !o.length
      ? '<div class="small">' + C("Hatırlanan açık oturum yok.") + "</div>"
      : '<table><thead><tr><th>' + C("Cihaz") + "</th><th>" + C("Adres")
        + "</th><th>" + C("Açılış") + '</th><th class="r">' + C("Kalan")
        + "</th><th></th></tr></thead><tbody>"
        + o.map((x) => "<tr><td>" + (x.bu_mu ? "<b>" + C("bu tarayıcı") + "</b>"
            : '<code class="small">' + esc(x.onek) + "…</code>")
          + "</td><td>" + esc(x.adres) + "</td><td>" + esc(x.olusma)
          + '</td><td class="r">' + x.kalan_gun + C(" gün") + "</td><td>"
          + (x.bu_mu ? "" : '<button class="sm warn" onclick="oturumKapat(\''
              + esc(x.onek) + "')\">" + C("Kapat") + "</button>")
          + "</td></tr>").join("") + "</tbody></table>"
        + '<div class="small" style="margin-top:6px">'
        + C("Hatırlama: ") + (j.ayarlar.remember_enabled ? C("açık") : C("kapalı"))
        + " · " + esc(String(j.ayarlar.remember_days)) + C(" gün")
        + " · " + C("adres bağlama: ") + esc(String(j.ayarlar.session_ip_bind || "ip"))
        + " · SameSite: " + esc(String(j.ayarlar.cookie_samesite || "Lax")) + "</div>";
    ceviriUygula();
  } catch { kutu.textContent = C("okunamadı"); }
}

async function oturumKapat(onek: string | null, hepsi?: boolean): Promise<void> {
  if (hepsi && !await onay(C("Bu tarayıcı dışındaki tüm hatırlanan oturumlar kapatılsın mı?"),
                           C("Oturumlar"), C("Kapat"), C("Vazgeç"))) return;
  const j = await api("/api/oturum/kapat", { method: "POST",
    body: JSON.stringify(hepsi ? { hepsi: true } : { onek }) });
  flash(j.msg || "", j.ok);
  void oturumlariYukle();
}

function openSettings(): void {
  const s = S ? S.settings : null;
  if (!s) return;
  setVal("g-bind", s.ui_bind); setVal("g-port", s.ui_port); setVal("g-user", s.ui_user);
  setVal("g-pass", ""); setVal("g-refresh", s.ui_refresh_sec);
  setVal("g-roots", (s.browse_roots || []).join(", ")); setVal("g-re", s.dump_regex);
  setVal("g-hist", s.history_max); setVal("g-logn", s.log_tail_lines);
  setVal("g-tail", s.rclone_tail_lines); setVal("g-rows", s.snapshot_max_rows);
  setVal("g-logmb", s.log_max_mb); setVal("g-logkeep", s.log_keep);
  setVal("g-tmo", s.rclone_timeout_min); setChk("g-cleanup", s.allow_account_cleanup);
  setVal("g-cert", s.ssl_cert || ""); setVal("g-key", s.ssl_key || "");
  setVal("g-nets", (s.allow_networks || []).join(", "));
  setChk("g-upcheck", s.update_check !== false); setChk("g-upauto", !!s.update_auto);
  setVal("g-upurl", s.update_url || "");
  upDurum();
  setChk("g-cookiesec", !!s.cookie_secure);
  const t = S && S.tls;
  const c = t && t.sertifika;
  setHtml("g-tlsdurum", t && t.aktif
    ? '<span style="color:#7ee2a8">🔒 TLS açık.</span> Sertifika: <b>' + esc(c ? c.konu : "-")
      + "</b> · veren: " + esc(c ? c.veren : "-") + C(" · bitiş: ") + esc(c ? c.bitis : "-")
    : '<span style="color:#ff9b9b">⚠ TLS kapalı</span> — arayüz düz HTTP çalışıyor.');
  setChk("g-tg", Boolean((s as unknown as Record<string, unknown>).telegram_enabled));
  setVal("g-tgchat", String((s as unknown as Record<string, unknown>).telegram_chat_id || ""));
  setVal("g-tget", String((s as unknown as Record<string, unknown>).telegram_etiket || ""));
  setVal("g-tgtoken", "");
  setTxt("g-tgdurum", S && S.telegram_jeton_var ? C("jeton kayıtlı") : C("jeton girilmemiş"));
  void pveLinkDurum(); void oturumlariYukle();
  const df = el("s-dosya") as HTMLInputElement;
  df.onchange = () => { if (df.files && df.files[0]) void ayarYukle(df.files[0]); df.value = ""; };
  openM("m-set");
}
async function saveSettings(): Promise<void> {
  let ok = true;
  ok = vRx("g-bind", RX.ip, C("IP adresi yaz (0.0.0.0 veya 127.0.0.1)")) && ok;
  ok = vNum("g-port", 1, 65535, "1-65535") && ok;
  ok = vTxt("g-user", C("kullanıcı adı gerekli")) && ok;
  ok = vNum("g-refresh", 1, 3600, C("1-3600 sn")) && ok;
  ok = vNum("g-hist", 1, 1000, "1-1000") && ok;
  ok = vNum("g-logn", 10, 5000, "10-5000") && ok;
  ok = vNum("g-tail", 1, 1000, "1-1000") && ok;
  ok = vNum("g-rows", 1, 10000, "1-10000") && ok;
  ok = vNum("g-logmb", 0, 1000, "0-1000 MB") && ok;
  ok = vNum("g-logkeep", 1, 20, "1-20") && ok;
  ok = vNum("g-tmo", 0, 1440, C("0-1440 dk")) && ok;
  ok = vTxt("g-re", C("kalıp boş olamaz")) && ok;
  const netler = val("g-nets").split(",").map((x) => x.trim()).filter(Boolean);
  const kotuNet = netler.filter((x) => !/^\d{1,3}(\.\d{1,3}){3}(\/\d{1,2})?$/.test(x)
    && !/^[0-9a-fA-F:]+(\/\d{1,3})?$/.test(x));
  if (kotuNet.length) ok = bad("g-nets", C("geçersiz ağ: ") + kotuNet[0]) && ok; else good("g-nets");
  if (!ok) { flash(C("form hatalı"), false); return; }
  const b: Record<string, unknown> = {
    ui_bind: val("g-bind"), ui_port: Number(val("g-port")), ui_user: val("g-user"),
    ui_refresh_sec: Number(val("g-refresh")), dump_regex: val("g-re"),
    history_max: Number(val("g-hist")), log_tail_lines: Number(val("g-logn")),
    rclone_tail_lines: Number(val("g-tail")), snapshot_max_rows: Number(val("g-rows")),
    log_max_mb: Number(val("g-logmb")), log_keep: Number(val("g-logkeep")),
    rclone_timeout_min: Number(val("g-tmo")), allow_account_cleanup: chk("g-cleanup"),
    ssl_cert: val("g-cert"), ssl_key: val("g-key"), cookie_secure: chk("g-cookiesec"),
    allow_networks: val("g-nets").split(",").map((x) => x.trim()).filter(Boolean),
    update_check: chk("g-upcheck"), update_auto: chk("g-upauto"), update_url: val("g-upurl"),
    browse_roots: val("g-roots").split(",").map((x) => x.trim()).filter(Boolean),
  };
  if (val("g-pass")) b.ui_pass = val("g-pass");
  b.telegram_enabled = chk("g-tg");
  b.telegram_chat_id = val("g-tgchat").trim();
  b.telegram_etiket = val("g-tget").trim();
  // Jeton yalnizca YENI girildiyse gonderilir; bos ise sunucudaki korunur
  if (val("g-tgtoken").trim()) b.telegram_token = val("g-tgtoken").trim();
  const j = await api("/api/settings/save", { method: "POST", body: JSON.stringify(b) });
  flash(j.msg || "", j.ok);
  if (j.ok) { closeM("m-set"); void refresh(); }
}

/* ---------- guncelleme ---------- */
function upDurum(): void {
  const g = S && S.guncelleme;
  const v = (S && S.surum) || "?";
  let h = C("Kurulu sürüm: <b>v") + esc(v) + "</b>";
  if (g && g.hata) h += ' · <span style="color:#ff9b9b">kontrol hatası: ' + esc(g.hata) + "</span>";
  else if (g && g.yeni_var) h += ' · <span style="color:#ffd479">yeni sürüm hazır: <b>v'
    + esc(g.uzak || "") + "</b></span>";
  else if (g && g.uzak) h += C(" · güncel");
  setHtml("g-guncel", h);
  el("g-upbtn").style.display = g && g.yeni_var ? "" : "none";
}
async function upKontrol(): Promise<void> {
  flash("kontrol ediliyor…", true);
  const j = await api<{ ok: boolean; surum?: string; uzak?: string; yeni_var?: boolean; hata?: string }>(
    "/api/update/check?force=1");
  await refresh(); upDurum();
  flash(j.hata ? C("hata: ") + j.hata
    : (j.yeni_var ? C("yeni sürüm var: v") + j.uzak : C("güncel: v") + j.surum), !j.hata);
}
async function upKur(): Promise<void> {
  if (!await onay(C("Güncelleme kurulacak.\n\nPlanların ve ayarların korunur, ikisinin de yedeği alınır.\n")
    + C("Arayüz birkaç saniye yeniden başlar. Devam edilsin mi?"))) return;
  flash(C("indiriliyor ve doğrulanıyor…"), true);
  const j = await api("/api/update/apply", { method: "POST" });
  flash(j.msg || "", j.ok);
  if (j.ok) window.setTimeout(() => location.reload(), 6000);
}
async function upGeri(): Promise<void> {
  if (!await onay(C("Önceki sürüme dönülecek. Devam edilsin mi?"))) return;
  const j = await api("/api/update/rollback", { method: "POST" });
  flash(j.msg || "", j.ok);
  if (j.ok) window.setTimeout(() => location.reload(), 6000);
}

/* ---------- baslangic ---------- */
/** Kaydedilmemis degisiklik varsa uygulama ici onay sorar. */
async function kapatmayiDene(m: HTMLElement): Promise<void> {
  if (m.id === "m-edit" && dirty
    && !await onay(C("Kaydedilmemiş değişiklikler var, kapatılsın mı?"))) return;
  m.classList.remove("show");
  if (m.id === "m-edit") dirty = false;
}

Array.prototype.slice.call(document.querySelectorAll(".mask")).forEach((m: HTMLElement) => {
  m.addEventListener("click", (e: Event) => {
    if (e.target !== m || m.id === "m-onay") return;   // onay penceresi disi tiklama kapatmaz
    void kapatmayiDene(m);
  });
});
document.addEventListener("keydown", (e: KeyboardEvent) => {
  const acik = Array.prototype.slice.call(
    document.querySelectorAll(".mask.show")) as HTMLElement[];
  if (acik.some((m) => m.id === "m-onay")) {
    if (e.key === "Enter") onayKapat(true);
    else if (e.key === "Escape") onayKapat(false);
    return;
  }
  if (e.key !== "Escape") return;
  acik.forEach((m) => void kapatmayiDene(m));
});
/* ---------- sag tik menuleri ----------
 * Kayitlar bir kez yapilir; listeler yenilendiginde tekrar baglamak gerekmez
 * cunku dinleyici document uzerinde ve secici ile eslesiyor (bkz. menu.ts). */

function planBul(id: string): Plan | undefined {
  return S ? S.plans.filter((p) => p.id === id)[0] : undefined;
}

/** Planin yalnizca etkin bayragini degistirir. save_plan ad ve hedefi zorunlu
 *  gordugu icin onlari da yolluyoruz; gerisi sunucuda korunur. */
async function planDurumDegistir(p: Plan): Promise<void> {
  const j = await api("/api/plan/save", { method: "POST", body: JSON.stringify(
    { id: p.id, name: p.name, remote: p.remote, enabled: !p.enabled }) });
  flash(j.ok ? (p.enabled ? C("plan duraklatıldı") : C("plan etkinleştirildi")) : (j.msg || ""), j.ok);
  void refresh();
}

/** Mevcut plandan kopya: sihirbaz yerine dolu formu acar, id bos kalir. */
function planKopyala(p: Plan): void {
  openEditor(null);
  wSihirbaz = false; wGoster();
  alanlariDoldur(p as unknown as Record<string, unknown>);
  const i = p.remote.indexOf(":");
  setVal("e-acct", p.remote.slice(0, i)); setVal("e-folder", p.remote.slice(i + 1));
  setVal("e-name", p.name + C(" (kopya)"));
  setChk("e-enabled", false);          // kopya kapali baslar, once gozden gecirilsin
  Array.prototype.slice.call(el("e-wd").querySelectorAll("input")).forEach((c: HTMLInputElement) => {
    c.checked = (p.weekdays || []).indexOf(Number(c.value)) >= 0;
  });
  ramHint(); saklamaIpucu(); markDirty();
  flash(C("kopya hazır — gözden geçirip kaydet"), true);
}

function planMenusu(kap: HTMLElement): MenuOge[] {
  const p = planBul(kap.getAttribute("data-plan") || "");
  if (!p) return [];
  const aliciVar = Boolean(p.report_mail_to || p.mail_to);
  return [
    { baslik: true, etiket: p.name },
    { simge: "▶", etiket: "Yedeklemeyi başlat", pasif: p.running,
      ipucu: p.running ? "bu plan zaten çalışıyor" : "", is: () => act("backup", p.id) },
    { simge: p.enabled ? "⏸" : "✅", etiket: p.enabled ? "Planı duraklat" : "Planı etkinleştir",
      ipucu: p.enabled ? "zamanlama durur, dosyalara dokunulmaz" : "",
      is: () => planDurumDegistir(p) },
    { ayrac: true },
    { simge: "🧹", etiket: "Retention'ı şimdi çalıştır", pasif: p.running,
      ipucu: "süresi dolan setleri Drive çöpüne taşır", is: () => act("prune", p.id) },
    { simge: "🗑", etiket: "Çöpü boşalt", pasif: p.running,
      ipucu: "çöpte süresi dolmuş dosyaları kalıcı siler", is: () => act("purgetrash", p.id) },
    { simge: "↻", etiket: "Drive durumunu tazele", pasif: p.running,
      is: () => act("refresh", p.id) },
    { ayrac: true },
    { simge: "📊", etiket: "Haftalık raporu şimdi gönder", pasif: !aliciVar || !p.weekly_report,
      ipucu: !p.weekly_report ? "bu planda rapor kapalı"
           : !aliciVar ? "önce plana mail adresi gir" : "", is: () => act("report", p.id) },
    { simge: "✉", etiket: "Test maili gönder", pasif: !p.mail_to,
      ipucu: p.mail_to ? "" : "önce plana mail adresi gir", is: () => act("testmail", p.id) },
    { ayrac: true },
    { simge: "✎", etiket: "Düzenle", is: () => openEditor(p.id) },
    { simge: "⧉", etiket: "Kopyasını oluştur", is: () => planKopyala(p) },
    { simge: "⬇", etiket: "Planı JSON olarak indir",
      ipucu: "başka sunucuya taşımak için", is: () => dosyaIndir(
        "plan-" + p.id + ".json", JSON.stringify(p, null, 2), "application/json") },
    { simge: "📋", etiket: "Kaynak klasörü kopyala", is: () => panoyaYaz(p.src_dir, C("kaynak")) },
    { simge: "📋", etiket: "Hedefi kopyala", is: () => panoyaYaz(p.remote, C("hedef")) },
    { simge: "📄", etiket: "Bu planın loglarını göster", is: () => setLog(p.id) },
    { simge: "🧩", etiket: "Yapılandırmayı şimdi yedekle",
      pasif: p.running || !p.host_config_enabled,
      ipucu: p.host_config_enabled ? "/etc/pve, ağ ve depo tanımları — ~25 KB"
                                   : "bu planda yapılandırma yedeği kapalı",
      is: () => act("hostconf", p.id) },
    { ayrac: true },
    { simge: "🗑", etiket: "Planı sil", tehlike: true, is: () => delPlan(p.id) },
  ];
}

function hesapMenusu(kap: HTMLElement): MenuOge[] {
  const ad = kap.getAttribute("data-hesap") || "";
  const r = REM.filter((x) => x.name === ad)[0];
  const q = (r && r.quota) || {};
  return [
    { baslik: true, etiket: ad },
    { simge: "🔌", etiket: "Bağlantıyı test et", is: () => acctTest(ad) },
    { simge: "↻", etiket: "Kotayı yenile", is: () => renderAccounts() },
    { simge: "📋", etiket: "Hesap adını kopyala", is: () => panoyaYaz(ad, C("hesap adı")) },
    { simge: "📋", etiket: "Kota bilgisini kopyala", pasif: !q.ok,
      is: () => panoyaYaz(ad + ": " + hb(q.used || 0) + " / " + hb(q.total || 0)) },
    { ayrac: true },
    { simge: "🗑", etiket: "Hesabı kaldır", tehlike: true,
      ipucu: "Drive'daki dosyalara dokunulmaz", is: () => acctDel(ad) },
  ];
}

function smtpMenusu(kap: HTMLElement): MenuOge[] {
  const id = kap.getAttribute("data-smtp") || "";
  const x = SMTP.filter((y) => y.id === id)[0];
  if (!x) return [];
  return [
    { baslik: true, etiket: x.name },
    { simge: "✎", etiket: "Düzenle", is: () => smtpEdit(id) },
    { simge: "✉", etiket: "Test maili gönder", is: () => smtpTest(id) },
    { simge: "📋", etiket: "Sunucu adresini kopyala",
      is: () => panoyaYaz(x.host + ":" + x.port, C("sunucu")) },
    { ayrac: true },
    { simge: "🗑", etiket: "Profili sil", tehlike: true, is: () => smtpDel(id) },
  ];
}

/** Imlecin durdugu log satirini dondurur. Tarayici caret API'si varsa metin
 *  ofsetinden kesin bulunur; yoksa satir yuksekligiyle tahmin edilir. */
function logSatiriBul(tum: string, olay: MouseEvent): string {
  const d = document as unknown as {
    caretPositionFromPoint?: (x: number, y: number) => { offsetNode: Node; offset: number } | null;
    caretRangeFromPoint?: (x: number, y: number) => Range | null;
  };
  let ofset = -1;
  try {
    if (d.caretPositionFromPoint) {
      const k = d.caretPositionFromPoint(olay.clientX, olay.clientY);
      if (k) ofset = k.offset;
    } else if (d.caretRangeFromPoint) {
      const r = d.caretRangeFromPoint(olay.clientX, olay.clientY);
      if (r) ofset = r.startOffset;
    }
  } catch { /* desteklenmiyorsa tahmine duseriz */ }
  if (ofset >= 0 && ofset <= tum.length) {
    const bas = tum.lastIndexOf("\n", Math.max(0, ofset - 1)) + 1;
    const son = tum.indexOf("\n", ofset);
    return tum.slice(bas, son < 0 ? tum.length : son);
  }
  const kutu = el("log");
  const g = kutu.getBoundingClientRect();
  const sy = parseFloat(getComputedStyle(kutu).lineHeight) || 16;
  const satirlar = tum.split("\n");
  const i = Math.floor((olay.clientY - g.top + kutu.scrollTop) / sy);
  return satirlar[Math.max(0, Math.min(satirlar.length - 1, i))] || "";
}

function logMenusu(_kap: HTMLElement, olay: MouseEvent): MenuOge[] {
  const tum = el("log").textContent || "";
  const satir = logSatiriBul(tum, olay);
  const kaynak = LOGSRC === "all" ? C("tümü") : LOGSRC === "system" ? C("sistem") : LOGSRC;
  return [
    { baslik: true, etiket: C("Log — ") + kaynak },
    { simge: "📋", etiket: "Bu satırı kopyala", pasif: !satir.trim(),
      ipucu: satir.slice(0, 90), is: () => panoyaYaz(satir, C("satır")) },
    { simge: "📋", etiket: "Görünen logu kopyala", is: () => panoyaYaz(tum) },
    { simge: "⬇", etiket: "Log dosyası olarak indir",
      is: () => dosyaIndir("pve-gdrive-" + LOGSRC + ".log", tum) },
    { ayrac: true },
    { simge: "↻", etiket: "Yenile", is: () => loadLog() },
    { simge: "📄", etiket: "Sistem loglarına geç", pasif: LOGSRC === "system",
      is: () => setLog("system") },
    { simge: "📚", etiket: "Tüm logları göster", pasif: LOGSRC === "all", is: () => setLog("all") },
  ];
}

/** Yedek ve cop tablolarindaki satirlar: dosya adiyla ugrasmak icin. */
function satirMenusu(tr: HTMLElement): MenuOge[] | null {
  const hucreler = Array.prototype.slice.call(tr.querySelectorAll("td")) as HTMLElement[];
  if (!hucreler.length) return null;
  const metinler = hucreler.map((td) => (td.textContent || "").trim());
  // En uzun hucre dosya adidir (vzdump-qemu-100-...zst)
  const dosya = metinler.filter((m) => m.indexOf("vzdump") === 0)[0] || "";
  return [
    { simge: "📋", etiket: "Dosya adını kopyala", pasif: !dosya,
      ipucu: dosya, is: () => panoyaYaz(dosya, C("dosya adı")) },
    { simge: "📋", etiket: "Satırı kopyala", is: () => panoyaYaz(metinler.join("  ")) },
    { simge: "📋", etiket: "Tabloyu kopyala", is: () => {
        const govde = tr.closest("table");
        const s2 = Array.prototype.slice.call(govde ? govde.querySelectorAll("tr") : [])
          .map((r: HTMLElement) => Array.prototype.slice.call(r.querySelectorAll("th,td"))
            .map((c: HTMLElement) => (c.textContent || "").trim()).join("\t")).join("\n");
        void panoyaYaz(s2, C("tablo"));
      } },
  ];
}

function genelMenu(): MenuOge[] {
  return [
    { simge: "➕", etiket: "Yeni plan", is: () => openEditor(null) },
    { simge: "↻", etiket: "Şimdi yenile", is: () => refresh() },
    { ayrac: true },
    { simge: "👤", etiket: "Google hesapları", is: () => openAccounts() },
    { simge: "✉", etiket: "SMTP profilleri", is: () => openSmtp() },
    { simge: "⚙", etiket: "Ayarlar", is: () => openSettings() },
  ];
}

function menuleriTanimla(): void {
  sagTik("[data-plan]", planMenusu);
  sagTik("[data-hesap]", hesapMenusu);
  sagTik("[data-smtp]", smtpMenusu);
  sagTik("#log", logMenusu);
  sagTik(".panel table tbody tr", satirMenusu);
  sagTik("#plans, .wrap > header, #detail", genelMenu);
}

dilBaslat();
surukleKur();
menuKur();
menuleriTanimla();
void refresh().then(taslakSor);
