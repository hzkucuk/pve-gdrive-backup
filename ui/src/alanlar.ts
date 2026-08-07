/**
 * Plan formu alan tablosu.
 *
 * Onceden openEditor / savePlan / validatePlan ayni alan listesini uc kez, uc farkli
 * bicimde tekrarliyordu; yeni bir alan eklerken uc yeri birden duzenlemek gerekiyordu.
 * Artik tek kaynak burasi: doldur(), topla() ve dogrula() bu tablodan turer.
 */

type AlanTipi = "metin" | "sayi" | "onay" | "saat" | "liste";

interface Alan {
  id: string;                 // DOM elemani id'si
  anahtar: string;            // Plan nesnesindeki alan adi
  tip: AlanTipi;
  adim: number;               // sihirbazda hangi adimda dogrulanacak (0 = dogrulama yok)
  min?: number;
  max?: number;
  rx?: RegExp;
  mesaj?: string;
  ops?: boolean;              // bos birakilabilir mi
  ayirac?: RegExp;            // liste tipi icin
  kosul?: () => boolean;      // yalnizca bu kosul saglanirsa dogrulanir
  ozelDogrula?: (id: string) => boolean;   // tabloya sigmayan kural
  vars?: unknown;             // form doldururken kullanilacak varsayilan
}

const PLAN_ALANLARI: Alan[] = [
  // 1. Plan
  { id: "e-name",    anahtar: "name",    tip: "metin", adim: 1, mesaj: "plan adı gerekli" },
  { id: "e-enabled", anahtar: "enabled", tip: "onay",  adim: 0 },
  // 2. Kaynak
  { id: "e-src",     anahtar: "src_dir", tip: "metin", adim: 2, mesaj: "kaynak klasör gerekli" },
  // 3. Hedef — remote iki alandan birlesir, ozel islenir (e-acct + e-folder)
  // 4. Saklama
  { id: "e-kd", anahtar: "keep_days",        tip: "sayi", adim: 4, min: 0, max: 3650, mesaj: "0-3650 arası gün" },
  { id: "e-kc", anahtar: "keep_count",       tip: "sayi", adim: 4, min: 0, max: 999,  mesaj: "0-999 arası adet" },
  { id: "e-td", anahtar: "drive_trash_days", tip: "sayi", adim: 4, min: 0, max: 365,  mesaj: "0-365 arası gün" },
  // 5. Zamanlama ve cakisma
  { id: "e-runat", anahtar: "run_at",           tip: "saat", adim: 5, mesaj: "SS:DD biçiminde saat (ör. 03:00)" },
  { id: "e-wv",    anahtar: "wait_for_vzdump",  tip: "onay", adim: 0 },
  { id: "e-wvm",   anahtar: "vzdump_wait_min",  tip: "sayi", adim: 5, min: 0, max: 1440, mesaj: "0-1440 dakika" },
  { id: "e-mage",  anahtar: "min_age_min",      tip: "sayi", adim: 5, min: 0, max: 1440, mesaj: "0-1440 dakika" },
  { id: "e-skip",  anahtar: "skip_patterns",    tip: "liste", adim: 0 },
  { id: "e-pof",   anahtar: "prune_on_failure", tip: "onay", adim: 0 },
  // 6. Aktarim
  { id: "e-bw",    anahtar: "bwlimit",      tip: "metin", adim: 6, rx: RX.bw, ops: true, mesaj: "ör. 30M, 2M veya off" },
  { id: "e-tr",    anahtar: "transfers",    tip: "sayi",  adim: 6, min: 1, max: 64, mesaj: "1-64 arası" },
  { id: "e-ck",    anahtar: "checkers",     tip: "sayi",  adim: 6, min: 1, max: 64, mesaj: "1-64 arası" },
  { id: "e-chunk", anahtar: "drive_chunk",  tip: "metin", adim: 6, rx: RX.chunk, mesaj: "ör. 64M, 128M, 8M" },
  { id: "e-extra", anahtar: "rclone_extra", tip: "liste", adim: 0 },
  { id: "e-bwup",  anahtar: "bwlimit_upload_only", tip: "onay", adim: 0 },
  { id: "e-bwauto", anahtar: "bwlimit_auto",       tip: "onay", adim: 0 },
  { id: "e-bwif",   anahtar: "bw_auto_iface",      tip: "metin", adim: 0, ops: true },
  // 7. Bildirim
  { id: "e-smtp",  anahtar: "smtp_profile",   tip: "metin", adim: 0, ops: true },
  { id: "e-nsuc",  anahtar: "notify_success", tip: "onay",  adim: 0 },
  { id: "e-nerr",  anahtar: "notify_failure", tip: "onay",  adim: 0 },
  { id: "e-nskip", anahtar: "notify_skipped", tip: "onay",  adim: 0 },
  { id: "e-wr",    anahtar: "weekly_report",  tip: "onay",  adim: 0 },
  { id: "e-rday",  anahtar: "report_day",     tip: "sayi",  adim: 0, min: 1, max: 7 },
  { id: "e-mail",  anahtar: "mail_to",        tip: "metin", adim: 7, ops: true,
    ozelDogrula: (id) => vMails(id, !(chk("e-nsuc") || chk("e-nerr") || chk("e-nskip") || chk("e-wr"))) },
  { id: "e-rmail", anahtar: "report_mail_to", tip: "metin", adim: 7, ops: true,
    ozelDogrula: (id) => vMails(id, true) },
  // Haftalik rapor alanlari: yalnizca rapor acikken dogrulanir
  { id: "e-rat",    anahtar: "report_at",         tip: "saat", adim: 7, vars: "09:00",
    kosul: () => chk("e-wr"), mesaj: "SS:DD biçiminde saat" },
  { id: "e-rdays",  anahtar: "report_days",       tip: "sayi", adim: 7, min: 1, max: 365, vars: 7,
    kosul: () => chk("e-wr"), mesaj: "1-365 gün" },
  { id: "e-rstale", anahtar: "report_stale_days", tip: "sayi", adim: 7, min: 0, max: 365, vars: 2,
    kosul: () => chk("e-wr"), mesaj: "0-365 gün" },
  { id: "e-rquota", anahtar: "report_quota_warn", tip: "sayi", adim: 7, min: 0, max: 100, vars: 90,
    kosul: () => chk("e-wr"), mesaj: "0-100 arası yüzde" },
  // Bant genisligi cizelgesi ve otomatik mod: yalnizca ilgiliyken dogrulanir
  { id: "e-bwsch",  anahtar: "bwlimit_schedule", tip: "metin", adim: 6, ops: true, vars: "",
    ozelDogrula: (id) => vBwSched(id) },
  { id: "e-bwlink", anahtar: "bw_auto_link",        tip: "metin", adim: 6, rx: RX.bw, vars: "100M",
    kosul: () => chk("e-bwauto"), mesaj: "ör. 12M, 100M" },
  { id: "e-bwres",  anahtar: "bw_auto_reserve_pct", tip: "sayi", adim: 6, min: 0, max: 95, vars: 30,
    kosul: () => chk("e-bwauto"), mesaj: "0-95 arası yüzde" },
  { id: "e-bwmin",  anahtar: "bw_auto_min",         tip: "metin", adim: 6, rx: RX.bw, vars: "1M",
    kosul: () => chk("e-bwauto"), mesaj: "ör. 512K, 1M" },
  { id: "e-bwmax",  anahtar: "bw_auto_max",         tip: "metin", adim: 6, rx: RX.bw, ops: true, vars: "",
    kosul: () => chk("e-bwauto"), mesaj: "ör. 30M veya boş" },
  { id: "e-bwint",  anahtar: "bw_auto_interval_sec", tip: "sayi", adim: 6, min: 2, max: 3600, vars: 10,
    kosul: () => chk("e-bwauto"), mesaj: "2-3600 sn" },
  { id: "e-bwsm",   anahtar: "bw_auto_smooth",      tip: "sayi", adim: 6, min: 0.05, max: 1, vars: 0.4,
    kosul: () => chk("e-bwauto"), mesaj: "0.05 - 1 arası" },
  { id: "e-bwstep", anahtar: "bw_auto_step_pct",    tip: "sayi", adim: 6, min: 1, max: 90, vars: 25,
    kosul: () => chk("e-bwauto"), mesaj: "1-90 arası yüzde" },
];

/** Formu bir plandan (veya varsayilanlardan) doldurur. */
function alanlariDoldur(v: Record<string, unknown>): void {
  for (const a of PLAN_ALANLARI) {
    if (!document.getElementById(a.id)) continue;
    const ham = v[a.anahtar];
    const d = ham === undefined || ham === null || ham === "" ? (a.vars ?? ham) : ham;
    if (a.tip === "onay") setChk(a.id, ham !== false && ham !== undefined ? Boolean(ham) : false);
    else if (a.tip === "liste") setVal(a.id, Array.isArray(d) ? (d as string[]).join(" ") : "");
    else setVal(a.id, d === undefined || d === null ? "" : d);
  }
}

/** Form degerlerini plan nesnesine toplar. */
function alanlariTopla(): Record<string, unknown> {
  const o: Record<string, unknown> = {};
  for (const a of PLAN_ALANLARI) {
    if (!document.getElementById(a.id)) continue;
    if (a.tip === "onay") o[a.anahtar] = chk(a.id);
    else if (a.tip === "sayi") o[a.anahtar] = Number(val(a.id));
    else if (a.tip === "liste") o[a.anahtar] = val(a.id).split(a.ayirac || /\s+/).filter(Boolean);
    else o[a.anahtar] = val(a.id);
  }
  return o;
}

/** Tabloya gore dogrular. adim verilirse yalnizca o adimin alanlari kontrol edilir. */
function alanlariDogrula(adim?: number): boolean {
  let ok = true;
  for (const a of PLAN_ALANLARI) {
    if (!document.getElementById(a.id)) continue;
    if (adim !== undefined && a.adim !== adim) continue;
    if (a.adim === 0) continue;
    if (a.kosul && !a.kosul()) { good(a.id); continue; }
    if (a.ozelDogrula) { ok = a.ozelDogrula(a.id) && ok; continue; }
    if (a.tip === "sayi") ok = vNum(a.id, a.min ?? 0, a.max ?? null, a.mesaj || "geçersiz sayı") && ok;
    else if (a.tip === "saat") ok = vRx(a.id, RX.time, a.mesaj || "SS:DD", a.ops) && ok;
    else if (a.rx) ok = vRx(a.id, a.rx, a.mesaj || "geçersiz değer", a.ops) && ok;
    else if (!a.ops) ok = vTxt(a.id, a.mesaj || "bu alan gerekli") && ok;
  }
  return ok;
}
