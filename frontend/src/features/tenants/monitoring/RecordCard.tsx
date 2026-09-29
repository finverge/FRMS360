import { Fragment } from "react"

import { formatInr } from "./format"
import { DispositionBadge, SeverityBadge } from "./SeverityBadge"

// Ported from the vanilla console's evKv() - renders one raw record (alert/
// transaction/case row) as a definition list, with the same *_paise -> rupees and
// *_ts/ts -> localised-datetime conventions, so a record reads the same way in both
// consoles during the migration.
export function RecordCard({
  title,
  record,
  skip = ["tenant_id"],
}: {
  title: string
  record: Record<string, unknown>
  skip?: string[]
}) {
  const entries = Object.entries(record).filter(([k]) => !skip.includes(k))
  return (
    <div className="rounded-md border border-border p-4">
      <h6 className="mb-2 text-sm font-medium">{title}</h6>
      <dl className="grid grid-cols-[max-content_1fr] gap-x-3 gap-y-1 text-sm">
        {entries.map(([k, v]) => {
          // Severity and disposition get the same colored badge as everywhere else in
          // the console (FilterBar chips, row tables, breakdown bars) instead of a
          // plain mono value - this is the one place they were still left as text.
          if (k === "severity" && v != null) {
            return (
              <Fragment key={k}>
                <dt className="capitalize text-muted-foreground">{k.replace(/_/g, " ")}</dt>
                <dd><SeverityBadge value={v} /></dd>
              </Fragment>
            )
          }
          if (k === "disposition" && v != null) {
            return (
              <Fragment key={k}>
                <dt className="capitalize text-muted-foreground">{k.replace(/_/g, " ")}</dt>
                <dd><DispositionBadge value={v} /></dd>
              </Fragment>
            )
          }
          let out: string
          if (v === null || v === undefined) out = "—"
          else if (k.endsWith("_paise")) out = formatInr(Number(v))
          else if (k.endsWith("_ts") || k === "ts") {
            const d = new Date(String(v))
            out = Number.isNaN(d.getTime()) ? String(v) : d.toLocaleString("en-IN")
          } else out = String(v)
          return (
            <Fragment key={k}>
              <dt className="capitalize text-muted-foreground">{k.replace(/_/g, " ")}</dt>
              <dd className="font-mono text-xs">{out}</dd>
            </Fragment>
          )
        })}
      </dl>
    </div>
  )
}
