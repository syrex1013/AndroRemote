import { useCallback, useEffect, useState } from "react";
import { toast } from "sonner";
import { RefreshCw, Trash2 } from "lucide-react";
import { api } from "@/lib/api";
import { fmtBytes } from "@/lib/format";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { DataTable } from "@/components/data-table";
import {
  AlertDialog, AlertDialogAction, AlertDialogCancel, AlertDialogContent, AlertDialogDescription,
  AlertDialogFooter, AlertDialogHeader, AlertDialogTitle,
} from "@/components/ui/alert-dialog";

interface CacheItem { tag: string; cmd: string; age: number; size: number }

export default function CacheView() {
  const [items, setItems] = useState<CacheItem[] | null>(null);
  const [purgeOpen, setPurgeOpen] = useState(false);
  const [filter, setFilter] = useState("");

  const load = useCallback(async () => {
    try {
      const r = await api<{ items: CacheItem[] }>("/api/cache");
      setItems(r.items);
    } catch (e) { toast.error(String(e instanceof Error ? e.message : e)); }
  }, []);

  useEffect(() => { load(); }, [load]);

  const purge = async () => {
    setPurgeOpen(false);
    try {
      const r = await api<{ cleared: number }>("/api/cache/clear", { method: "POST", body: "{}" });
      toast.success(`purged ${r.cleared} entries`);
      load();
    } catch (e) { toast.error(String(e instanceof Error ? e.message : e)); }
  };

  return (
    <div className="max-w-[1000px] space-y-3">
      <div className="flex items-center gap-2">
        <Button size="sm" variant="destructive" onClick={() => setPurgeOpen(true)}><Trash2 className="size-3.5 mr-1" /> Purge all</Button>
        <Button size="sm" variant="ghost" onClick={load}><RefreshCw className="size-3.5 mr-1" /> Refresh</Button>
        <Input value={filter} onChange={(e) => setFilter(e.target.value)} placeholder="filter cache…" className="h-8 w-48 font-mono text-xs" />
        <span className="flex-1" />
        <span className="text-[11px] font-mono text-muted-foreground">TTL cache — agent query results are reused for 60s</span>
      </div>
      <div className="rounded-lg border bg-card overflow-x-auto">
        {items === null ? (
          <p className="text-xs text-muted-foreground font-mono py-8 text-center">loading…</p>
        ) : items.length === 0 ? (
          <p className="text-xs text-muted-foreground font-mono py-8 text-center">cache is empty — cached agent responses show up here</p>
        ) : (
          <DataTable
            columns={[
              { key: "tag", head: "Session", class: "font-mono text-xs text-primary" },
              { key: "cmd", head: "Command", class: "font-mono text-xs" },
              { key: "age", head: "Age", class: "text-right font-mono text-xs", value: (r) => r.age, render: (r) => `${Math.floor(r.age)}s` },
              { key: "size", head: "Size", class: "text-right font-mono text-xs", value: (r) => r.size, render: (r) => fmtBytes(r.size) },
            ]}
            rows={items.filter((it) => !filter || `${it.tag} ${it.cmd}`.toLowerCase().includes(filter.toLowerCase()))}
            empty="no matching cache entries"
          />
        )}
      </div>
      <AlertDialog open={purgeOpen} onOpenChange={setPurgeOpen}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>Purge all cached results?</AlertDialogTitle>
            <AlertDialogDescription>This clears the local result cache. The next agent queries will run normally.</AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter><AlertDialogCancel>Cancel</AlertDialogCancel><AlertDialogAction onClick={purge}>Purge cache</AlertDialogAction></AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </div>
  );
}
