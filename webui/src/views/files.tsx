import { useCallback, useEffect, useState } from "react";
import { toast } from "sonner";
import { ArrowUp, Download, ListTree, Upload } from "lucide-react";
import { api, postOp } from "@/lib/api";
import { getToken } from "@/lib/api";
import { useConsole } from "@/state";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import {
  Table, TableBody, TableCell, TableHead, TableHeader, TableRow,
} from "@/components/ui/table";

interface FileRow { name: string; dir: boolean }

export default function FilesView() {
  const { snapshot, activeSession } = useConsole();
  const s = activeSession();
  const cid = snapshot?.active ?? null;
  const [path, setPath] = useState("/sdcard");
  const [rows, setRows] = useState<FileRow[] | null>(null);
  const [drives, setDrives] = useState<string[]>([]);

  const loadDrives = async () => {
    try {
      const r = await postOp<{ rows?: { path: string }[] }>("drives");
      setDrives((r.rows ?? []).map((x) => x.path));
    } catch (e) { toast.error(String(e instanceof Error ? e.message : e)); }
  };

  const list = useCallback(async (p: string) => {
    if (!cid) return;
    try {
      const r = await postOp<{ rows?: FileRow[]; error?: string }>("ls", { path: p });
      if (r.error) { toast.error(r.error); return; }
      setRows(r.rows ?? []);
    } catch (e) { toast.error(String(e instanceof Error ? e.message : e)); }
  }, [cid]);

  useEffect(() => { if (cid) list(path); }, [cid, path, list]);

  if (!cid) {
    return (
      <div className="text-center py-14 text-muted-foreground">
        <p className="text-sm">No session selected.</p>
        <p className="text-xs font-mono mt-1 opacity-70">pick an agent from the strip above</p>
      </div>
    );
  }

  const clean = path.replace(/\/$/, "");

  const enter = (name: string) => setPath(clean + "/" + name);

  const download = async (rpath: string) => {
    toast("downloading " + rpath.split("/").pop() + "…");
    try {
      const q = new URLSearchParams({ cid, path: rpath, name: rpath.split("/").pop() || "file.bin" });
      const tok = getToken();
      if (tok) q.set("token", tok);
      const res = await fetch("/api/download?" + q);
      if (!res.ok) { const j = await res.json().catch(() => ({})); throw new Error(j.error || "download failed"); }
      const blob = await res.blob();
      const a = document.createElement("a");
      a.href = URL.createObjectURL(blob);
      a.download = rpath.split("/").pop() || "file.bin";
      a.click();
      URL.revokeObjectURL(a.href);
      toast.success("saved " + a.download);
    } catch (e) { toast.error(String(e instanceof Error ? e.message : e)); }
  };

  const upload = async (f: File) => {
    toast("uploading " + f.name + "…");
    try {
      const buf = new Uint8Array(await f.arrayBuffer());
      let bin = "";
      for (let i = 0; i < buf.length; i += 8192) bin += String.fromCharCode(...buf.subarray(i, i + 8192));
      const r = await api<{ ok?: boolean; bytes?: number }>("/api/upload", {
        method: "POST",
        body: JSON.stringify({ cid, path: `${clean}/${f.name}`, data_b64: btoa(bin) }),
      });
      toast.success(`uploaded ${r.bytes?.toLocaleString()} B → ${clean}/${f.name}`);
      list(path);
    } catch (e) { toast.error(String(e instanceof Error ? e.message : e)); }
  };

  const crumbs = path.split("/").filter(Boolean);

  return (
    <div className="max-w-[1100px] space-y-3">
      <div className="flex items-center gap-2 flex-wrap">
        <Input value={path} onChange={(e) => setPath(e.target.value)} className="font-mono text-xs w-72 h-8" />
        <Button size="sm" variant="secondary" onClick={() => list(path)}><ListTree className="size-3.5 mr-1" /> List</Button>
        <Button size="sm" variant="outline" onClick={loadDrives}>Available drives</Button>
        <span className="flex-1" />
        <label className="cursor-pointer">
          <input type="file" className="hidden" onChange={(e) => { const f = e.target.files?.[0]; if (f) upload(f); e.target.value = ""; }} />
          <span className="inline-flex items-center gap-1.5 rounded-md border px-3 h-8 text-xs font-mono hover:bg-accent cursor-pointer">
            <Upload className="size-3.5" /> Upload here
          </span>
        </label>
      </div>
      {drives.length > 0 && <div className="flex gap-2 flex-wrap">{drives.map((d) => <Button key={d} size="sm" variant="ghost" className="font-mono text-xs" onClick={() => setPath(d)}>{d}</Button>)}</div>}

      <div className="text-xs font-mono text-muted-foreground break-all">
        <button className="hover:text-foreground cursor-pointer" onClick={() => setPath("/")}>/</button>
        {crumbs.map((c, i) => (
          <span key={i}>
            <button className="text-primary hover:underline cursor-pointer" onClick={() => setPath("/" + crumbs.slice(0, i + 1).join("/"))}>{c}</button>
            {i < crumbs.length - 1 && " / "}
          </span>
        ))}
      </div>

      <div className="rounded-lg border bg-card overflow-x-auto">
        {rows === null ? (
          <p className="text-xs text-muted-foreground font-mono py-8 text-center">listing…</p>
        ) : rows.length === 0 ? (
          <p className="text-xs text-muted-foreground font-mono py-8 text-center">empty directory</p>
        ) : (
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead className="w-16">Type</TableHead>
                <TableHead>Name</TableHead>
                <TableHead className="w-20 text-right"></TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {rows.filter((r) => r.dir).map((r) => (
                <TableRow key={r.name} className="cursor-pointer" onClick={() => enter(r.name)}>
                  <TableCell><Badge variant="secondary" className="font-mono text-[10px]">dir</Badge></TableCell>
                  <TableCell className="font-mono text-xs text-primary">{r.name}</TableCell>
                  <TableCell />
                </TableRow>
              ))}
              {rows.filter((r) => !r.dir).map((r) => (
                <TableRow key={r.name}>
                  <TableCell><Badge variant="outline" className="font-mono text-[10px]">file</Badge></TableCell>
                  <TableCell className="font-mono text-xs">{r.name}</TableCell>
                  <TableCell className="text-right">
                    <Button variant="ghost" size="sm" className="h-7 px-2" onClick={() => download(`${clean}/${r.name}`)}>
                      <Download className="size-3.5" />
                    </Button>
                  </TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        )}
      </div>
      {s && <p className="text-[11px] font-mono text-muted-foreground">{s.tag} · {s.model}</p>}
      <ArrowUp className="hidden" />
    </div>
  );
}
