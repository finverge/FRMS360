import { Search } from "lucide-react"
import { useState } from "react"

import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { CompareDialog } from "./CompareDialog"
import { ExportCsvButton } from "./ExportCsvButton"
import { PaginationBar } from "./PaginationBar"
import { SeverityBadge } from "./SeverityBadge"
import { useRowsPage } from "./useRowsPage"

const MAX_COMPARE = 4

function fmtDate(v: unknown): string {
  if (!v || typeof v !== "string") return "—"
  const d = new Date(v)
  return Number.isNaN(d.getTime()) ? "—" : d.toLocaleDateString("en-IN")
}

// A response-due date in the past with the case still open is exactly the natural-
// justice breach BR's nj_breach_count metric counts - flagged here the same way, at
// the row level, since that is where an investigator actually acts on it.
function isOverdue(v: unknown): boolean {
  if (!v || typeof v !== "string") return false
  const d = new Date(v)
  return !Number.isNaN(d.getTime()) && d.getTime() < Date.now()
}

// Shared by both the table body and the Compare dialog, so the two views can never show
// different fields for the same row.
const COLUMNS: { key: string; label: string; align?: "right"; render: (r: Record<string, unknown>) => React.ReactNode }[] = [
  { key: "case_id", label: "Case", render: (r) => <span className="font-mono text-xs">{String(r.case_id)}</span> },
  { key: "opened_ts", label: "Opened", render: (r) => fmtDate(r.opened_ts) },
  { key: "state", label: "State", render: (r) => String(r.state ?? "").replace(/_/g, " ") },
  { key: "severity", label: "Severity", render: (r) => <SeverityBadge value={r.severity} /> },
  {
    key: "amount_paise", label: "Amount", align: "right",
    render: (r) => (typeof r.amount_paise === "number" ? (r.amount_paise / 100).toLocaleString("en-IN") : "—"),
  },
  { key: "rfa_flag", label: "RFA", render: (r) => (r.rfa_flag ? <Badge variant="outline">RFA</Badge> : "—") },
  { key: "assignee", label: "Assignee", render: (r) => String(r.assignee ?? "—") },
  {
    key: "response_due_ts", label: "Response due",
    render: (r) => {
      const overdue = !r.decision_ts && isOverdue(r.response_due_ts)
      return (
        <>
          <span className={overdue ? "text-destructive font-medium" : ""}>{fmtDate(r.response_due_ts)}</span>
          {overdue && <span className="ml-1 text-xs text-destructive">overdue</span>}
        </>
      )
    },
  },
]

export function CaseRowsTable({
  onRowClick,
  tenantId,
  exportQuery,
}: {
  onRowClick?: (caseId: string) => void
  tenantId: string
  // The dashboard's own date/severity/account filters (see useDateSeverityFilters'
  // queryString) - pagination, search and CSV export all stay scoped to the same window.
  exportQuery?: string
}) {
  const {
    query, setQuery, page, setPage, pageSize, setPageSize,
    rows, total, totalPages, loading, errored, searchActive,
  } = useRowsPage(tenantId, "case", exportQuery ?? "")
  const [selected, setSelected] = useState<Set<string>>(new Set())
  const [compareOpen, setCompareOpen] = useState(false)

  function toggle(id: string) {
    setSelected((s) => {
      const next = new Set(s)
      if (next.has(id)) next.delete(id)
      else if (next.size < MAX_COMPARE) next.add(id)
      return next
    })
  }

  const selectedRows = rows.filter((r) => selected.has(String(r.case_id)))

  return (
    <div className="space-y-2">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <h5 className="text-sm font-medium">
          Cases <span className="text-muted-foreground font-normal">({total.toLocaleString("en-IN")})</span>
        </h5>
        <div className="flex flex-wrap items-center gap-2">
          {selected.size > 0 && (
            <>
              <span className="text-xs text-muted-foreground">{selected.size} selected</span>
              <Button size="sm" variant="outline" disabled={selected.size < 2} onClick={() => setCompareOpen(true)}>
                Compare
              </Button>
              <Button size="sm" variant="ghost" onClick={() => setSelected(new Set())}>Clear</Button>
            </>
          )}
          <ExportCsvButton tenantId={tenantId} entity="case" query={exportQuery ?? ""} />
          <div className="relative w-[200px] shrink-0">
            <Search className="pointer-events-none absolute top-1/2 left-2.5 size-3.5 -translate-y-1/2 text-muted-foreground" />
            <Input
              value={query}
              onChange={(e) => setQuery(e.target.value)}
              placeholder="Search case ID"
              className="h-8 pl-8 text-sm"
            />
          </div>
        </div>
      </div>

      <div className="overflow-x-auto rounded-lg border border-border">
        <table className="w-full text-sm">
          <thead className="bg-muted text-left text-muted-foreground">
            <tr>
              <th className="w-8 px-3 py-1.5" />
              {COLUMNS.map((c) => (
                <th key={c.key} className={"px-3 py-1.5 font-medium" + (c.align === "right" ? " text-right" : "")}>
                  {c.label}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {loading && rows.length === 0 && (
              <tr><td colSpan={COLUMNS.length + 1} className="px-3 py-2.5 text-muted-foreground">Loading…</td></tr>
            )}
            {!loading && errored && (
              <tr><td colSpan={COLUMNS.length + 1} className="px-3 py-2.5 text-destructive">Could not load cases.</td></tr>
            )}
            {!loading && !errored && rows.length === 0 && (
              <tr>
                <td colSpan={COLUMNS.length + 1} className="px-3 py-3 text-muted-foreground">
                  <div className="flex flex-wrap items-center gap-2">
                    <span>
                      {searchActive
                        ? `No matches for "${query.trim()}" anywhere in the filtered cases.`
                        : "No cases match the current filters."}
                    </span>
                    {onRowClick && searchActive && (
                      <button
                        type="button"
                        className="text-primary hover:underline"
                        onClick={() => onRowClick(query.trim())}
                      >
                        Look up this case ID directly →
                      </button>
                    )}
                  </div>
                </td>
              </tr>
            )}
            {rows.map((r) => {
              const id = String(r.case_id)
              return (
                <tr
                  key={id}
                  className={"border-t border-border" + (onRowClick ? " cursor-pointer hover:bg-accent/50" : "")}
                >
                  <td className="px-3 py-1.5" onClick={(e) => e.stopPropagation()}>
                    <input
                      type="checkbox"
                      checked={selected.has(id)}
                      disabled={!selected.has(id) && selected.size >= MAX_COMPARE}
                      onChange={() => toggle(id)}
                      title={selected.size >= MAX_COMPARE && !selected.has(id) ? `Compare supports up to ${MAX_COMPARE} at a time` : undefined}
                    />
                  </td>
                  {COLUMNS.map((c) => (
                    <td
                      key={c.key}
                      className={"px-3 py-1.5" + (c.align === "right" ? " text-right" : "")}
                      onClick={() => onRowClick?.(id)}
                    >
                      {c.render(r)}
                    </td>
                  ))}
                </tr>
              )
            })}
          </tbody>
        </table>
      </div>

      <PaginationBar
        page={page} totalPages={totalPages} total={total} pageSize={pageSize}
        onPageChange={setPage} onPageSizeChange={setPageSize} loading={loading}
      />

      <CompareDialog
        open={compareOpen}
        onOpenChange={setCompareOpen}
        title="cases"
        idKey="case_id"
        rows={selectedRows}
        columns={COLUMNS}
      />
    </div>
  )
}
