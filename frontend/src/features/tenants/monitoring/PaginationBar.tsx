import { ChevronLeft, ChevronRight, ChevronsLeft, ChevronsRight } from "lucide-react"

import { Button } from "@/components/ui/button"
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select"
import { PAGE_SIZES } from "./useRowsPage"

// Shared by every row table (case/alert/transaction) - real pagination over the whole
// filtered dataset, not a fixed "most recent N" window. Mirrors Verafye's own
// page-size-select + first/prev/next/last control.
export function PaginationBar({
  page,
  totalPages,
  total,
  pageSize,
  onPageChange,
  onPageSizeChange,
  loading,
}: {
  page: number
  totalPages: number
  total: number
  pageSize: number
  onPageChange: (page: number) => void
  onPageSizeChange: (size: number) => void
  loading: boolean
}) {
  return (
    <div className="flex flex-wrap items-center justify-between gap-2 text-xs text-muted-foreground">
      <div className="flex items-center gap-1.5">
        <span>Rows per page</span>
        <Select value={String(pageSize)} onValueChange={(v) => onPageSizeChange(Number(v))}>
          <SelectTrigger className="h-7 w-[66px] text-xs">
            <SelectValue />
          </SelectTrigger>
          <SelectContent>
            {PAGE_SIZES.map((n) => (
              <SelectItem key={n} value={String(n)}>{n}</SelectItem>
            ))}
          </SelectContent>
        </Select>
      </div>

      <div className="flex items-center gap-1">
        <span className="mr-1 tabular-nums">
          Page {page} of {totalPages} · {total.toLocaleString("en-IN")} record{total === 1 ? "" : "s"}
        </span>
        <Button
          size="icon" variant="outline" className="size-7"
          disabled={page <= 1 || loading} onClick={() => onPageChange(1)}
          title="First page"
        >
          <ChevronsLeft className="size-3.5" />
        </Button>
        <Button
          size="icon" variant="outline" className="size-7"
          disabled={page <= 1 || loading} onClick={() => onPageChange(page - 1)}
          title="Previous page"
        >
          <ChevronLeft className="size-3.5" />
        </Button>
        <Button
          size="icon" variant="outline" className="size-7"
          disabled={page >= totalPages || loading} onClick={() => onPageChange(page + 1)}
          title="Next page"
        >
          <ChevronRight className="size-3.5" />
        </Button>
        <Button
          size="icon" variant="outline" className="size-7"
          disabled={page >= totalPages || loading} onClick={() => onPageChange(totalPages)}
          title="Last page"
        >
          <ChevronsRight className="size-3.5" />
        </Button>
      </div>
    </div>
  )
}
