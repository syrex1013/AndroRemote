import { useMemo, useState } from "react";
import { ArrowDown, ArrowUp, ChevronLeft, ChevronRight, ChevronsLeft, ChevronsRight } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import {
  Table, TableBody, TableCell, TableHead, TableHeader, TableRow,
} from "@/components/ui/table";
import { cn } from "@/lib/utils";

export interface Column {
  key: string;
  head: string;
  class?: string;
  /** value used for sorting/filtering; defaults to row[key] stringified */
  value?: (row: Record<string, any>) => string | number;
  render?: (row: Record<string, any>, index: number) => React.ReactNode;
}

interface Props {
  columns: Column[];
  rows: Record<string, any>[];
  /** render the trailing actions cell for a row */
  actions?: (row: Record<string, any>) => React.ReactNode;
  pageSize?: number;
  empty?: string;
  defaultSort?: { key: string; dir: "asc" | "desc" };
}

export function DataTable({ columns, rows, actions, pageSize = 15, empty = "no rows", defaultSort }: Props) {
  const [page, setPage] = useState(0);
  const [size, setSize] = useState(pageSize);
  const [sort, setSort] = useState<{ key: string; dir: "asc" | "desc" } | null>(defaultSort ?? null);

  const sorted = useMemo(() => {
    if (!sort) return rows;
    const col = columns.find((c) => c.key === sort.key);
    if (!col) return rows;
    const val = (r: Record<string, any>) => {
      const v = col.value ? col.value(r) : r[sort.key];
      if (typeof v === "number") return v;
      return String(v ?? "").toLowerCase();
    };
    return [...rows].sort((a, b) => {
      const va = val(a), vb = val(b);
      const cmp = va < vb ? -1 : va > vb ? 1 : 0;
      return sort.dir === "asc" ? cmp : -cmp;
    });
  }, [rows, sort, columns]);

  const pages = Math.max(1, Math.ceil(sorted.length / size));
  const cur = Math.min(page, pages - 1);
  const slice = sorted.slice(cur * size, cur * size + size);

  const toggleSort = (key: string) => {
    setSort((s) => (s?.key === key ? (s.dir === "asc" ? { key, dir: "desc" } : null) : { key, dir: "asc" }));
    setPage(0);
  };

  return (
    <div className="space-y-2">
      <div className="rounded-lg border bg-card overflow-x-auto">
        <Table>
          <TableHeader>
            <TableRow>
              {columns.map((c) => (
                <TableHead
                  key={c.key}
                  className={cn(
                    c.class?.includes("text-right") && "text-right",
                    "select-none",
                    // any column is sortable; headers with explicit class keep alignment
                  )}
                >
                  <button
                    className={cn(
                      "inline-flex items-center gap-1 hover:text-foreground transition-colors cursor-pointer",
                      sort?.key === c.key && "text-foreground",
                    )}
                    onClick={() => toggleSort(c.key)}
                    title="Sort"
                  >
                    {c.head}
                    {sort?.key === c.key &&
                      (sort.dir === "asc" ? <ArrowUp className="size-3" /> : <ArrowDown className="size-3" />)}
                  </button>
                </TableHead>
              ))}
              {actions && <TableHead className="w-10" />}
            </TableRow>
          </TableHeader>
          <TableBody>
            {slice.length === 0 ? (
              <TableRow>
                <TableCell colSpan={columns.length + (actions ? 1 : 0)} className="text-center text-muted-foreground font-mono text-xs py-8">
                  {rows.length === 0 ? empty : "no rows on this page"}
                </TableCell>
              </TableRow>
            ) : (
              slice.map((r, i) => (
                <TableRow key={i}>
                  {columns.map((c) => (
                    <TableCell key={c.key} className={c.class}>
                      {c.render ? c.render(r, cur * size + i) : String(r[c.key] ?? "—")}
                    </TableCell>
                  ))}
                  {actions && <TableCell className="text-right pr-2">{actions(r)}</TableCell>}
                </TableRow>
              ))
            )}
          </TableBody>
        </Table>
      </div>

      {/* pagination controls */}
      <div className="flex items-center gap-3 text-xs font-mono text-muted-foreground">
        <span>
          {sorted.length === 0 ? "0" : cur * size + 1}–{Math.min(sorted.length, (cur + 1) * size)} of {sorted.length}
        </span>
        <Select
          value={String(size)}
          onValueChange={(v) => { setSize(Number(v)); setPage(0); }}
        >
          <SelectTrigger size="sm" className="w-[74px] h-7 font-mono text-[11px]">
            <SelectValue />
          </SelectTrigger>
          <SelectContent>
            {[10, 15, 25, 50, 100].map((n) => (
              <SelectItem key={n} value={String(n)}>{n} / page</SelectItem>
            ))}
          </SelectContent>
        </Select>
        <span className="flex-1" />
        <div className="flex items-center gap-1">
          <Button variant="ghost" size="icon" className="size-7" disabled={cur === 0} onClick={() => setPage(0)}>
            <ChevronsLeft className="size-3.5" />
          </Button>
          <Button variant="ghost" size="icon" className="size-7" disabled={cur === 0} onClick={() => setPage(cur - 1)}>
            <ChevronLeft className="size-3.5" />
          </Button>
          <span className="px-1.5 tabular-nums">{cur + 1} / {pages}</span>
          <Button variant="ghost" size="icon" className="size-7" disabled={cur >= pages - 1} onClick={() => setPage(cur + 1)}>
            <ChevronRight className="size-3.5" />
          </Button>
          <Button variant="ghost" size="icon" className="size-7" disabled={cur >= pages - 1} onClick={() => setPage(pages - 1)}>
            <ChevronsRight className="size-3.5" />
          </Button>
        </div>
      </div>
    </div>
  );
}
