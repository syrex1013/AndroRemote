export const fmtBytes = (n: number) =>
  n >= 1048576 ? (n / 1048576).toFixed(1) + " MB" : n >= 1024 ? (n / 1024).toFixed(1) + " KB" : (n || 0) + " B";

export const fmtUptime = (s: number) => {
  s = Math.max(0, Math.floor(s));
  const h = Math.floor(s / 3600), m = Math.floor((s % 3600) / 60);
  return (h ? h + "h " : "") + m + "m " + (s % 60) + "s";
};

export const fmtAge = (s: number) =>
  s < 60 ? `${s}s` : s < 3600 ? `${Math.floor(s / 60)}m${String(s % 60).padStart(2, "0")}s` : `${Math.floor(s / 3600)}h${String(Math.floor((s % 3600) / 60)).padStart(2, "0")}m`;

export const hhmmss = (ts: number) => new Date(ts * 1000).toTimeString().slice(0, 8);

export const cls = (...xs: (string | false | null | undefined)[]) => xs.filter(Boolean).join(" ");
