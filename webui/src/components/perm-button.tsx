import { useState } from "react";
import { toast } from "sonner";
import { ShieldPlus } from "lucide-react";
import { postOp } from "@/lib/api";
import { Button } from "@/components/ui/button";

/** Request a feature's runtime permissions on the active agent (permreq), or
 * open a system settings screen (uireq). The dialog appears on the device. */
export function PermButton({ perms, what, label }: { perms?: string[]; what?: string; label: string }) {
  const [busy, setBusy] = useState(false);
  return (
    <Button size="sm" variant="outline" disabled={busy}
      onClick={async () => {
        setBusy(true);
        try {
          const r = await postOp<{ text?: string; ok?: boolean; error?: string }>(
            perms ? "permreq" : "uireq", perms ? { perms } : { what: what! });
          if (r.error) toast.error(r.error);
          else toast.success(r.text || "check the device screen");
        } catch (e) { toast.error(String(e instanceof Error ? e.message : e)); }
        setBusy(false);
      }}>
      <ShieldPlus className="size-3.5 mr-1" /> {label}
    </Button>
  );
}
