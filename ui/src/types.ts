/** Arka ucun /api/status ile dondurdugu veri yapilari. */

interface Progress {
  phase?: string;
  phase_label?: string;
  started?: number;
  updated?: number;
  pct?: number;
  done?: number;
  total?: number;
  done_h?: string;
  total_h?: string;
  speed?: string;
  speed_bps?: number;
  eta?: string;
  trigger?: string;
  plan?: string;
}

interface Quota { total?: number; used?: number; trashed?: number; free?: number; ok?: boolean; error?: string }
interface Totals { count: number; size: number }
interface BackupRow { name: string; guest: string; size: number; mod: string }
interface TrashRow { name: string; size: number; remain_days: number; tracked?: boolean }
interface HistoryRow { time: string; status: string; summary: string; trigger: string }

interface PlanState {
  last_run: string | null;
  status: string;
  summary: string;
  history?: HistoryRow[];
  backups?: BackupRow[];
  trash?: TrashRow[];
  quota?: Quota;
  totals?: Totals;
  trash_totals?: Totals;
  updated?: string | null;
  aktif_hedef?: string | null;
  hedef_denemeleri?: { hedef: string; ok: boolean; yuklenen: number }[];
}

interface Plan {
  id: string;
  name: string;
  enabled: boolean;
  src_dir: string;
  remote: string;
  yedek_hedefler?: string[];
  keep_days: number;
  keep_count: number;
  drive_trash_days: number;
  run_at: string;
  weekdays: number[];
  bwlimit: string;
  bwlimit_schedule: string;
  bwlimit_upload_only: boolean;
  bwlimit_auto: boolean;
  bw_auto_link: string;
  bw_auto_reserve_pct: number;
  bw_auto_min: string;
  bw_auto_max: string;
  bw_auto_iface: string; bw_auto_link_mode?: "ogren" | "manuel";
  bw_auto_interval_sec: number;
  bw_auto_smooth: number;
  bw_auto_step_pct: number;
  transfers: number;
  checkers: number;
  drive_chunk: string;
  rclone_extra: string[];
  smtp_profile: string;
  mail_to: string;
  notify_success: boolean;
  notify_failure: boolean;
  notify_skipped: boolean;
  wait_for_vzdump: boolean;
  vzdump_wait_min: number;
  min_age_min: number;
  skip_patterns: string[];
  prune_on_failure: boolean;
  weekly_report: boolean;
  host_config_enabled: boolean; host_config_json: boolean; host_config_keep_count: number;
  report_day: number;
  report_at: string;
  report_days: number;
  report_stale_days: number;
  report_quota_warn: number;
  report_mail_to: string;
  /* sunucu tarafindan eklenenler */
  state: PlanState;
  running: boolean;
  progress: Progress | null;
  next_run: string | null;
  next_report: string | null;
  src_exists: boolean;
  src_dumps: number;
}

interface Saglik {
  tick: "iyi" | "gecikmis" | "bilinmiyor";
  tick_mesaj: string; tick_son: string | null; tick_yas_dk: number;
}

interface Settings {
  ui_bind: string; ui_port: number; ui_user: string; ui_refresh_sec: number;
  browse_roots: string[]; allow_account_cleanup: boolean; history_max: number;
  log_tail_lines: number; rclone_timeout_min: number; dump_regex: string;
  rclone_tail_lines: number; snapshot_max_rows: number; log_max_mb: number;
  log_keep: number; stats_interval_sec: number; log_file: string; state_file: string;
  ssl_cert: string; ssl_key: string; cookie_secure: boolean;
  update_check: boolean; update_auto: boolean; update_url: string; update_backup_keep: number;
  allow_networks: string[];
  failure_mail?: boolean; failure_mail_to?: string; tick_uyari_dk?: number;
  sse_enabled?: boolean; sse_watch_ms?: number;
  sse_heartbeat_sec?: number; sse_max_clients?: number;
  remember_enabled?: boolean; remember_days?: number;
  session_ip_bind?: "ip" | "ag" | "yok";
}

interface SmtpProfile {
  id: string; name: string; host: string; port: number;
  user: string; from: string; security: "starttls" | "ssl" | "none";
}

interface Remote { name: string; type: string; quota?: Quota }

interface Status {
  plans: Plan[];
  updated: string | null;
  settings: Settings;
  smtp: SmtpProfile[];
  smtp_ready: boolean;
  tls?: { aktif: boolean; sertifika: { konu: string; veren: string; bitis: string } | null };
  hesaplar?: { name: string; type: string; quota: Quota; pct: number | null }[];
  surum?: string;
  saglik?: Saglik;
  guncelleme?: { uzak: string | null; yeni_var: boolean; otomatik: boolean; hata: string };
  csrf: string;
  user?: string;
  login?: boolean;
}

interface ApiResult { ok: boolean; msg?: string; id?: string; name?: string; [k: string]: unknown }
interface BrowseResult { path: string; parent?: string; roots?: string[]; dirs?: {name: string; path: string; dumps: number}[]; dumps: number; error?: string }
interface AuthStart extends ApiResult { url?: string; tunnel?: string }
interface AuthStatus extends ApiResult { ready?: boolean; waiting?: boolean; url?: string | null }
