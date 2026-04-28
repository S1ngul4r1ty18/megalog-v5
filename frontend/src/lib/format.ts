export function fmtNumber(n: number | null | undefined): string {
  if (n === null || n === undefined) return "—";
  return new Intl.NumberFormat("pt-BR").format(n);
}

export function fmtBytes(b: number | null | undefined): string {
  if (b === null || b === undefined) return "—";
  const units = ["B", "KB", "MB", "GB", "TB"];
  let v = Math.abs(b);
  let i = 0;
  while (v >= 1024 && i < units.length - 1) { v /= 1024; i++; }
  return v.toFixed(v < 10 && i > 0 ? 1 : 0) + " " + units[i];
}

export function fmtDateTime(ts: number | null | undefined): string {
  if (!ts) return "—";
  return new Date(ts * 1000).toLocaleString("pt-BR");
}

export function fmtDate(ts: number | null | undefined): string {
  if (!ts) return "—";
  return new Date(ts * 1000).toLocaleDateString("pt-BR");
}

export function fmtPct(p: number | null | undefined, digits = 1): string {
  if (p === null || p === undefined) return "—";
  return p.toFixed(digits) + "%";
}
