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
function markDirty(): void { dirty = true; }

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
function bad(id: string, msg: string): boolean {
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
function bwAutoToggle(): void {
  const acik = chk("e-bwauto");
  el("bwauto-box").style.display = acik ? "" : "none";
  fld("e-bw").disabled = acik;
  fld("e-bwsch").disabled = acik;
  markDirty();
}
async function loadIfaces(secili: string): Promise<void> {
  try {
    const j = await api<{ default: string; ifaces: { name: string; tx: number; default: boolean }[] }>("/api/ifaces");
    const list = j.ifaces || [];
    setHtml("e-bwif", '<option value="">(otomatik: ' + esc(j.default || "-") + ")</option>"
      + list.map((i) => '<option value="' + esc(i.name) + '">C(' + esc(i.name)
        + " — " + hb(i.tx) + " gönderilmiş" + (i.default ? " (varsayılan rota)" : "") + "</option>").join(""));
    setVal("e-bwif", secili);
    setTxt("e-bwifhint", "Proxmox')ta köprü (vmbr0) yalnızca host trafiğini görebilir; "
      + "VM ve CT trafiğini de saymak için fiziksel veya bond arayüzünü seç.");
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
  if (p.running) return '<span class="pill run">● ÇALIŞIYOR</span>';
  if (!p.enabled) return '<span class="pill off">KAPALI</span>';
  if (s.status === "basarili") return '<span class="pill ok">✔ BAŞARILI</span>';
  if (s.status === "HATA") return '<span class="pill err">✖ HATA</span>';
  if (s.status === "atlandi") return '<span class="pill run">⏸ ATLANDI</span>';
  return '<span class="pill idle">' + esc(s.status || "—").toUpperCase() + "</span>";
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
  return '<div class="plan' + (p.id === sel ? " sel" : "") + "\" onclick=\"pick('" + p.id + "')\">"
    + "<h3>" + esc(p.name) + pillOf(p) + "</h3>"
    + progBox(p)
    + '<div class="row"><span>Kaynak</span><b>' + esc(p.src_dir)
    + (p.src_exists ? ' <span class="small">(' + p.src_dumps + " dosya)</span>" : ' <span class="pill err">yok</span>')
    + "</b></div>"
    + '<div class="row"><span>Hedef</span><b>' + esc(p.remote) + "</b></div>"
    + '<div class="row"><span>Program</span><b>' + progOf(p) + "</b></div>"
    + '<div class="row"><span>Sonraki</span><b>' + esc(p.next_run || "-") + "</b></div>"
    + '<div class="row"><span>Saklama</span><b>C(' + p.keep_days + " gün · min " + p.keep_count
    + " set · çöp " + p.drive_trash_days + " gün</b></div>"
    + ')<div class="row"><span>Son çalışma</span><b>' + esc(s.last_run || "-") + "</b></div>"
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
    + '<div class="small" style="margin-top:8px">C(' + hb(used) + " / " + hb(total) + " (" + pct.toFixed(1)
    + "%) · çöp: " + hb(q.trashed || 0) + " · boş: " + hb(q.free || 0) + "</div></div>"
    + ')<div class="cols"><div class="panel"><h2>Yedekler (Drive)</h2><table><thead><tr><th>Tarih</th>'
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
    ? '<span class="pill ok" title=C("Bağlantı şifreli")>🔒 HTTPS</span>'
    : '<span class="pill err" title=C("Trafik şifresiz — yalnızca VPN içinde kullan")>⚠ HTTP</span>');
  const g = S.guncelleme;
  setHtml("uprozet", g && g.yeni_var
    ? '<span class="pill run" title=C("Yeni sürüm var: ' + esc(g.uzak || ")")
      + '" style="cursor:pointer" onclick="openSettings()">⬆ C(' + esc(g.uzak || "") + " hazır</span>"
    : ')<span class="small" title=C("Kurulu sürüm")>vC(' + esc(S.surum || "?") + "</span>");
  setTxt("hinfo", ps.length + " plan" + (running ? " · " + running + " çalışıyor" : "")
    + (S.updated ? " · durum: " + S.updated : "") + (S.smtp_ready ? "" : " · mail profili yok"));
  setHtml("plans", ps.map(planCard).join("")
    || ')<div class="card">Henüz plan yok. Sağ üstten C("+ Yeni Plan") ile başla.</div>');
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
  const base = ((S && S.settings && S.settings.ui_refresh_sec) || 5) * 1000;
  const iv = running ? Math.min(base, 2000) : base;
  window.clearInterval(refTimer);
  refTimer = window.setInterval(() => void refresh(), iv);
}

async function act(d: string, pid: string): Promise<void> {
  flash(C("çalışıyor…"), true);
  try {
    const j = await api("/api/action?do=" + d + "&plan=" + encodeURIComponent(pid), { method: "POST" });
    flash(j.msg || "tamam", j.ok);
  } catch { flash("hata", false); }
  window.setTimeout(() => void refresh(), 900);
}
async function delPlan(pid: string): Promise<void> {
  if (!confirm(C("Plan silinsin mi? Drive'daki yedek dosyalarına dokunulmaz."))) return;
  const j = await api("/api/plan/delete?plan=" + encodeURIComponent(pid), { method: "POST" });
  flash(j.msg || "", j.ok);
  void refresh();
}
async function logout(): Promise<void> { await api("/logout", { method: "POST" }); location.reload(); }

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
  ramHint(); markDirty();
  flash(C("senaryo yüklendi — kaydetmeden uygulanmaz"), true);
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
    report_day: 1, report_at: "09:00", report_days: 7, report_stale_days: 2,
    report_quota_warn: 90, report_mail_to: "",
  };
  const v = (p || d) as unknown as Plan;
  // Ortak alanlar tablodan doldurulur (bkz. alanlar.ts); asagidakiler ozel durumlar.
  alanlariDoldur(v as unknown as Record<string, unknown>);
  const rp = String(v.remote || C("gdrive:proxmox-yedek")).split(":");
  setVal("e-folder", rp.slice(1).join(":"));
  void loadIfaces(v.bw_auto_iface || ""); bwAutoToggle();
  setHtml("e-rday", WD.map((n, i) => '<option value="' + (i + 1) + '">' + n + "</option>").join(""));
  setVal("e-rday", v.report_day || 1);
  setHtml("e-wd", WD.map((n, i) => '<label><input type="checkbox" value="' + (i + 1) + '"'
    + ((v.weekdays || []).indexOf(i + 1) >= 0 ? " checked" : "") + ">" + n + "</label>").join(""));
  setTxt("e-srchint", p ? (p.src_exists ? p.src_dumps + " dosya bulundu" : C("⚠ klasör bulunamadı")) : "");
  void loadRemotes(rp[0]); loadSmtpSelect(v.smtp_profile); void loadStorages(); ramHint();
  Array.prototype.slice.call(document.querySelectorAll("#m-edit input,#m-edit select"))
    .forEach((e: HTMLElement) => { e.oninput = markDirty; e.onchange = markDirty; });
  fld("e-kd").oninput = () => { kapasiteCiz(); markDirty(); };
  fld("e-td").oninput = () => { kapasiteCiz(); markDirty(); };
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
  if (!validatePlan()) { flash(C("form hatalı — kırmızı alanlara bak"), false); return; }
  const wd = Array.prototype.slice.call(el("e-wd").querySelectorAll("input:checked"))
    .map((c: HTMLInputElement) => Number(c.value));
  const body: Record<string, unknown> = {
    ...alanlariTopla(),                       // tum ortak alanlar (bkz. alanlar.ts)
    id: EDIT,
    remote: val("e-acct") + ":" + val("e-folder").trim().replace(/^\/+/, ""),
    weekdays: wd,
  };
  const j = await api("/api/plan/save", { method: "POST", body: JSON.stringify(body) });
  flash(j.msg || "", j.ok);
  if (j.ok) {
    dirty = false; wSihirbaz = false; closeM("m-edit");
    sel = j.id || sel; remember(); void refresh();
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
  kapasiteCiz();
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
    uyari += (uyari ? "<br>" : "") + C("Önerilen: <b>") + KAP.oneri + C(" gün</b> (boş alanın %")
      + (KAP.oneri_pay_pct || 60) + C("'ini kullanır, büyümeye pay bırakır).");
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
function openAccounts(): void {
  openM("m-acct"); hesapPaneliTasi("hesap-ekle-yuvasi", true); acctTab(1); void renderAccounts();
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
    return '<div class="card" style="margin-bottom:8px"><div style="display:flex;'
      + 'justify-content:space-between;gap:8px;align-items:center;flex-wrap:wrap">'
      + "<b>" + esc(r.name) + '</b> <span class="small">' + esc(r.type) + "</span>"
      + '<span style="flex:1"></span>'
      + "<button class=\"sm\" onclick=\"acctTest('" + r.name + "')\">Test</button>"
      + "<button class=\"sm warn\" onclick=\"acctDel('" + r.name + C("')\">Sil</button></div>")
      + '<div class="small" style="margin-top:6px">' + line + "</div></div>";
  }).join("") : '<div class="small">Henüz hesap yok.</div>');
}
async function acctTest(n: string): Promise<void> {
  flash("kontrol ediliyor…", true);
  const j = await api("/api/remote/test?name=" + encodeURIComponent(n), { method: "POST" });
  flash(j.msg || "", j.ok);
}
async function acctDel(n: string): Promise<void> {
  if (!confirm("'" + n + C("' kaldırılsın mı? Drive'daki dosyalara dokunulmaz."))) return;
  const j = await api("/api/remote/delete?name=" + encodeURIComponent(n), { method: "POST" });
  flash(j.msg || "", j.ok); void renderAccounts(); void loadRemotes();
}
async function acctPaste(): Promise<void> {
  if (!RX.acct.test(val("a-name").trim())) { bad("a-name", "sadece harf, rakam, - ve _"); return; }
  good("a-name");
  try { JSON.parse(val("a-token")); } catch { bad("a-token", C("geçerli JSON değil")); return; }
  good("a-token");
  const j = await api("/api/remote/add", { method: "POST",
    body: JSON.stringify({ name: val("a-name"), token: val("a-token") }) });
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
  const j = await api<AuthStart>("/api/remote/auth/start", { method: "POST" });
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
    '<div class="card" style="margin-bottom:8px"><div style="display:flex;justify-content:space-between;'
    + 'gap:8px;align-items:center;flex-wrap:wrap"><b>' + esc(x.name) + "</b>"
    + '<span style="flex:1"></span>'
    + "<button class=\"sm\" onclick=\"smtpEdit('" + x.id + C("')\">Düzenle</button>")
    + "<button class=\"sm\" onclick=\"smtpTest('" + x.id + "')\">Test maili</button>"
    + "<button class=\"sm warn\" onclick=\"smtpDel('" + x.id + C("')\">Sil</button></div>")
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
  if (!confirm("Profil silinsin mi?")) return;
  const j = await api("/api/smtp/delete?id=" + encodeURIComponent(id), { method: "POST" });
  flash(j.msg || "", j.ok);
  await refresh(); renderSmtp(); loadSmtpSelect();
}
async function smtpTest(id: string): Promise<void> {
  const to = prompt(C("Test maili hangi adrese gitsin?\n(boş bırakırsan gönderen adresine gider)"), "");
  if (to === null) return;
  flash(C("gönderiliyor…"), true);
  const j = await api("/api/smtp/test?id=" + encodeURIComponent(id) + "&to=" + encodeURIComponent(to),
    { method: "POST" });
  flash(j.msg || "", j.ok);
}

/* ---------- genel ayarlar ---------- */
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
  if (!confirm(C("Güncelleme kurulacak.\n\nPlanların ve ayarların korunur, ikisinin de yedeği alınır.\n")
    + C("Arayüz birkaç saniye yeniden başlar. Devam edilsin mi?"))) return;
  flash(C("indiriliyor ve doğrulanıyor…"), true);
  const j = await api("/api/update/apply", { method: "POST" });
  flash(j.msg || "", j.ok);
  if (j.ok) window.setTimeout(() => location.reload(), 6000);
}
async function upGeri(): Promise<void> {
  if (!confirm(C("Önceki sürüme dönülecek. Devam edilsin mi?"))) return;
  const j = await api("/api/update/rollback", { method: "POST" });
  flash(j.msg || "", j.ok);
  if (j.ok) window.setTimeout(() => location.reload(), 6000);
}

/* ---------- baslangic ---------- */
Array.prototype.slice.call(document.querySelectorAll(".mask")).forEach((m: HTMLElement) => {
  m.addEventListener("click", (e: Event) => {
    if (e.target !== m) return;
    if (m.id === "m-edit" && dirty
      && !confirm(C("Kaydedilmemiş değişiklikler var, kapatılsın mı?"))) return;
    m.classList.remove("show");
    if (m.id === "m-edit") dirty = false;
  });
});
document.addEventListener("keydown", (e: KeyboardEvent) => {
  if (e.key !== "Escape") return;
  Array.prototype.slice.call(document.querySelectorAll(".mask.show")).forEach((m: HTMLElement) => {
    if (m.id === "m-edit" && dirty
      && !confirm(C("Kaydedilmemiş değişiklikler var, kapatılsın mı?"))) return;
    m.classList.remove("show");
  });
});
dilBaslat();
void refresh();
