import axios, { AxiosError } from "axios";

export const api = axios.create({
  baseURL: "/",
  withCredentials: true,
  timeout: 30000,
});

// Em 401, joga o usuário pro /login (exceto se já estiver lá ou se for /api/auth/me).
api.interceptors.response.use(
  (r) => r,
  (err: AxiosError) => {
    if (err.response?.status === 401) {
      const path = window.location.pathname;
      const url = err.config?.url ?? "";
      if (path !== "/login" && !url.endsWith("/api/auth/me")) {
        window.location.href = "/login";
      }
    }
    return Promise.reject(err);
  },
);

// ── Tipagens dos contratos da API ────────────────────────────────────────────

export interface Me {
  user_id: number;
  username: string;
  role: "admin" | "user";
}

export interface SearchQuery {
  date: string;
  src_ip?: string;
  dst_ip?: string;
  nat_ip?: string;
  src_port?: number;
  dst_port?: number;
  nat_port?: number;
  proto?: "TCP" | "UDP";
  ts_start?: number;
  ts_end?: number;
  page?: number;
  per_page?: number;
}

export interface SearchRow {
  ts: number;
  ts_iso: string;
  src_ip: string;
  src_port: number | null;
  dst_ip: string;
  dst_port: number | null;
  nat_ip: string | null;
  nat_port: number | null;
  proto: string | null;
  in_iface: string | null;
  out_iface: string | null;
  conn_state: string | null;
  has_snat: boolean;
  tcp_flags: string | null;
  pkt_len: number | null;
}

export interface SearchResult {
  total: number;
  page: number;
  per_page: number;
  rows: SearchRow[];
}

export interface DailyRow {
  date: string;
  log_count: number;
  db_size_bytes: number;
  kind: "hot" | "cold" | "absent";
  format: "duckdb" | "parquet" | null;
  alert: boolean;
}

export interface TopIp {
  ip_id: number;
  ip: string;
  total_hits: number;
  first_seen: string | null;
  last_seen: string | null;
  is_hot: boolean;
}

export interface AlertRow {
  id: number;
  date: string;
  log_count: number;
  db_size_bytes: number;
  expected_count: number;
  ratio: number;
  analysis: string;
  details_json: string;
  classification: string | null;
  created_at: number;
  acknowledged: number;
  acknowledged_by: string | null;
  acknowledged_at: number | null;
}

export interface AuditRow {
  id: number;
  user_id: number | null;
  username: string | null;
  action: string;
  details: string | null;
  ip_address: string | null;
  ts: number;
}

export interface UserRow {
  id: number;
  username: string;
  role: "admin" | "user";
  created_at: number;
  last_login: number | null;
  active: number;
}

export interface DiskInfo {
  path: string;
  total_bytes: number;
  used_bytes: number;
  free_bytes: number;
  used_pct: number;
}

export interface SystemStatus {
  cpu: {
    per_core_pct: number[];
    avg_pct: number;
    load_avg: number[];
  };
  ram: {
    total_bytes: number;
    used_bytes: number;
    available_bytes: number;
    used_pct: number;
  };
  swap: {
    total_bytes: number;
    used_bytes: number;
    used_pct: number;
  };
  disks: Record<string, DiskInfo | null>;
  services: Record<string, string>;
  ingest: {
    raw_buffer_bytes: number;
    raw_last_seen: string | null;
    today_date: string;
    today_db_path: string;
    today_db_size: number;
    today_log_count: number | null;
    today_count_age_seconds: number | null;
  };
  now: string;
}

/**
 * Estrutura de `details_json` da anomalia. Nem todos os campos vêm
 * sempre populados — anomalias geradas por watchdog (stream/disco/etc)
 * trazem só `issues` + `checked_at`, sem análise de tráfego. Por isso
 * todos os campos analíticos são opcionais.
 */
export interface AnomalyDetails {
  // Watchdog-style (stream cheio, disco cheio, etc.)
  issues?: string[];
  checked_at?: number;

  // Analytics-style (gerado pelo anomaly_analyzer)
  total_conns?: number;
  unique_src_ips?: number;
  unique_dst_ips?: number;
  unique_dst_ports?: number;
  p2p_suspect_ips?: number;
  avg_ports_per_ip?: number;
  top_ip_pct?: number;
  port_ranges?: { well_known_pct: number; registered_pct: number; ephemeral_pct: number };
  protocols?: Record<string, number>;
  ntp_detail?: { ntp_clients: number; ntp_servers: number };
  dns_detail?: { dns_clients: number; dns_servers: number };
  top_ips?: Array<{
    ip: string; connections: number; dst_ips: number; dst_ports: number;
    pct: number; well_known_pct: number; ephemeral_pct: number;
  }>;
  top_dst_ips?: Array<{ ip: string; count: number; pct: number }>;
  top_dst_ports?: Array<{ port: number; count: number; pct: number; category: string; service: string }>;
  top_talker?: { ip: string; profile: string; pct: number; dst_ports: number; dst_ips: number };
  flow_repetition?: { max_flow_count: number; top_flows: Array<any> };
  protocol_anomaly?: { udp_on_tcp_ports: number; udp_on_tcp_pct: number; affected_ports: any[] };
  classification?: string;
  classification_score?: number;
  classification_reasons?: string[];
  possible_causes?: string[];
}
