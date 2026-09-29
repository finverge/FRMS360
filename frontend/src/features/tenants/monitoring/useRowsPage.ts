import { useEffect, useState } from "react"

import { useSession } from "@/app/SessionContext"
import { fetchRowsPage, type EvidenceEntity } from "@/api/evidence"

export const PAGE_SIZES = [25, 50, 75, 100]

/** One row table's data: server-paginated (never "most recent N"), and searched
 * server-side across the whole filtered dataset (not just the current page) once the
 * query is 2+ chars - shared by Case/Alert/TransactionRowsTable so pagination, search
 * debounce, and page-reset behave identically everywhere. */
export function useRowsPage(tenantId: string, entity: EvidenceEntity, baseQuery: string) {
  const { accessToken } = useSession()
  const [query, setQueryRaw] = useState("")
  const [page, setPage] = useState(1)
  const [pageSize, setPageSizeRaw] = useState(50)
  const [rows, setRows] = useState<Record<string, unknown>[]>([])
  const [total, setTotal] = useState(0)
  const [loading, setLoading] = useState(true)
  const [errored, setErrored] = useState(false)

  // Changing the search term or the page size while sitting on page 7 would otherwise
  // just show an empty page - both go back to page 1 in the same action as the change.
  function setQuery(v: string) { setQueryRaw(v); setPage(1) }
  function setPageSize(n: number) { setPageSizeRaw(n); setPage(1) }

  const trimmedQuery = query.trim()

  // The dashboard's own date/severity/account filters changed upstream - stay on page 1
  // of the new result set rather than an offset that belonged to the old one.
  // eslint-disable-next-line react-hooks/exhaustive-deps
  useEffect(() => { setPage(1) }, [baseQuery])

  useEffect(() => {
    if (!tenantId) return
    let cancelled = false
    setLoading(true)
    const handle = setTimeout(() => {
      fetchRowsPage(
        tenantId, entity,
        { baseQuery, idSearch: trimmedQuery.length >= 2 ? trimmedQuery : undefined,
          limit: pageSize, offset: (page - 1) * pageSize },
        accessToken
      )
        .then((res) => { if (!cancelled) { setRows(res.rows); setTotal(res.total); setErrored(false) } })
        .catch(() => { if (!cancelled) { setRows([]); setTotal(0); setErrored(true) } })
        .finally(() => { if (!cancelled) setLoading(false) })
    }, trimmedQuery ? 300 : 0)
    return () => { cancelled = true; clearTimeout(handle) }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [tenantId, entity, baseQuery, trimmedQuery, page, pageSize, accessToken])

  const totalPages = Math.max(1, Math.ceil(total / pageSize))

  return {
    query, setQuery, page, setPage, pageSize, setPageSize,
    rows, total, totalPages, loading, errored,
    searchActive: trimmedQuery.length >= 2,
  }
}
