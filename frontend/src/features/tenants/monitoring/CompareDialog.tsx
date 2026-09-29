import { Dialog, DialogContent, DialogHeader, DialogTitle } from "@/components/ui/dialog"

// Generic side-by-side comparison, shared by every row table (case/alert/transaction) -
// Verafye's own "Compare" button does exactly this: pick a few rows, see them as columns
// against the same field list the table already shows. Reusing each table's own column
// definitions here means the compare view can never drift from what the table displays.
export function CompareDialog<T extends Record<string, unknown>>({
  open,
  onOpenChange,
  title,
  idKey,
  rows,
  columns,
}: {
  open: boolean
  onOpenChange: (open: boolean) => void
  title: string
  idKey: string
  rows: T[]
  columns: { key: string; label: string; render: (row: T) => React.ReactNode }[]
}) {
  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-w-4xl">
        <DialogHeader>
          <DialogTitle>Compare {title} ({rows.length})</DialogTitle>
        </DialogHeader>
        <div className="max-h-[70vh] overflow-auto rounded-lg border border-border">
          <table className="w-full border-collapse text-sm">
            <thead>
              <tr className="bg-muted">
                <th className="sticky left-0 z-10 bg-muted px-3 py-1.5 text-left font-medium text-muted-foreground">
                  &nbsp;
                </th>
                {rows.map((r) => (
                  <th
                    key={String(r[idKey])}
                    className="border-l border-border px-3 py-1.5 text-left font-mono text-xs font-semibold"
                  >
                    {String(r[idKey])}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {columns.map((c) => (
                <tr key={c.key} className="border-t border-border">
                  <td className="sticky left-0 z-10 bg-card px-3 py-1.5 font-medium text-muted-foreground">
                    {c.label}
                  </td>
                  {rows.map((r) => (
                    <td key={String(r[idKey])} className="border-l border-border px-3 py-1.5 align-top">
                      {c.render(r)}
                    </td>
                  ))}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </DialogContent>
    </Dialog>
  )
}
