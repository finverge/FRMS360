import { useEffect, useMemo, useState } from "react"

import { useSession } from "@/app/SessionContext"
import { ApiError } from "@/api/client"
import { getGraph, type GraphOut } from "@/api/evidence"
import { Alert, AlertDescription } from "@/components/ui/alert"
import { Badge } from "@/components/ui/badge"
import { Card, CardContent } from "@/components/ui/card"
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog"
import { StatCell } from "@/components/ui/sidebar"
import { formatInr } from "./format"
import { layoutGraph } from "./networkLayout"
import { PiiRevealControl } from "./PiiRevealControl"

// Brightened against the fixed-dark investigation canvas below (the canvas doesn't
// follow the app's own light/dark toggle - a network map is read as its own "room",
// the same way an ops floor's link-analysis wall stays dark regardless of what else
// is on screen, so these need to read clearly on near-black rather than on a card).
const ROLE_COLOR: Record<string, string> = {
  hub: "#FF6B5B",
  collector: "#FFB454",
  disburser: "#2DD4BF",
}
const DEFAULT_COLOR = "#93A5C4"

const WIDTH = 640
const HEIGHT = 420

// The account graph behind a case or an account - how a ring is actually shaped (see
// routes/dashboards.py's graph()). Node ids are real account numbers, so the same
// masking applies here as everywhere else: a masked graph still shows the true
// structure (degree, role, edge weight), just with pseudonymised node ids instead of
// real account numbers.
export function NetworkGraphView({
  tenantId,
  scope,
  open,
  onOpenChange,
}: {
  tenantId: string
  scope: { case_id?: string; account?: string }
  open: boolean
  onOpenChange: (open: boolean) => void
}) {
  const { accessToken } = useSession()
  const [data, setData] = useState<GraphOut | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [hovered, setHovered] = useState<string | null>(null)

  function load(reveal: boolean, justification?: string) {
    setError(null)
    getGraph(tenantId, scope, { reveal, justification }, accessToken)
      .then(setData)
      .catch((err) => setError(err instanceof ApiError ? err.message : "Could not build this network."))
  }

  // eslint-disable-next-line react-hooks/exhaustive-deps
  useEffect(() => load(false), [tenantId, scope.case_id, scope.account, accessToken])

  const laidOut = useMemo(() => {
    if (!data || data.nodes.length === 0) return []
    return layoutGraph(data.nodes, data.edges, WIDTH, HEIGHT)
  }, [data])

  const byId = useMemo(() => new Map(laidOut.map((n) => [n.id, n])), [laidOut])
  const maxDeg = Math.max(1, ...laidOut.map((n) => n.degree))
  const maxVal = Math.max(1, ...(data?.edges.map((e) => e.value_paise) ?? [1]))
  const hoveredNode = hovered ? byId.get(hovered) : null
  const showLabels = laidOut.length <= 40

  const totalValue = data?.edges.reduce((sum, e) => sum + e.value_paise, 0) ?? 0
  const topHub = laidOut.length
    ? laidOut.reduce((best, n) => (n.degree > best.degree ? n : best), laidOut[0])
    : null

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-w-4xl">
        <DialogHeader>
          <DialogTitle className="flex items-center gap-2">
            Account network
            {data && (
              <span className="text-sm font-normal text-muted-foreground">
                {data.scope.case_id && `· case ${data.scope.case_id.slice(0, 10)}`}
                {data.scope.account && `· account ${data.scope.account}`}
              </span>
            )}
            {data && <Badge variant={data.pii_masked ? "outline" : "destructive"}>{data.pii_masked ? "masked" : "revealed — audited"}</Badge>}
          </DialogTitle>
        </DialogHeader>

        <div className="space-y-3">
          <div className="flex flex-wrap items-center justify-between gap-2">
            <PiiRevealControl onApply={(justification) => load(true, justification)} disabled={data ? !data.pii_masked : false} />
          </div>

          {error && (
            <Alert variant="destructive">
              <AlertDescription>{error}</AlertDescription>
            </Alert>
          )}

          {!data && !error && <p className="text-sm text-muted-foreground">Building network…</p>}

          {data && data.nodes.length === 0 && (
            <p className="text-sm text-muted-foreground">No linked accounts for this scope.</p>
          )}

          {data && laidOut.length > 0 && (
            <>
              <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
                <Card className="py-3"><CardContent className="px-4"><StatCell label="Accounts" value={data.nodes.length} /></CardContent></Card>
                <Card className="py-3"><CardContent className="px-4"><StatCell label="Links" value={data.edges.length} /></CardContent></Card>
                <Card className="py-3"><CardContent className="px-4"><StatCell label="Value moved" value={formatInr(totalValue)} /></CardContent></Card>
                <Card className="py-3"><CardContent className="px-4"><StatCell label="Busiest account" value={topHub ? `${topHub.id} · ${topHub.degree}` : "—"} /></CardContent></Card>
              </div>

              {/* A fixed dark "investigation" surface, independent of the app's own
                  light/dark theme - link-analysis views read as their own room on a
                  real ops floor, and the bright role colors above are tuned for this
                  near-black background specifically, not for a themed card. */}
              <div className="relative overflow-hidden rounded-lg border border-white/10 bg-[#050912]">
                <div
                  className="absolute inset-0 opacity-40"
                  style={{
                    backgroundImage: "radial-gradient(rgba(255,255,255,0.06) 1px, transparent 1px)",
                    backgroundSize: "22px 22px",
                  }}
                />
                <svg viewBox={`0 0 ${WIDTH} ${HEIGHT}`} className="relative w-full" style={{ height: HEIGHT }}>
                  <defs>
                    <filter id="ngGlow" x="-100%" y="-100%" width="300%" height="300%">
                      <feGaussianBlur stdDeviation="4.5" result="blur" />
                      <feMerge>
                        <feMergeNode in="blur" />
                        <feMergeNode in="SourceGraphic" />
                      </feMerge>
                    </filter>
                    <style>{`
                      @keyframes ngDashFlow { to { stroke-dashoffset: -24; } }
                      .ng-edge { animation: ngDashFlow 1.4s linear infinite; }
                    `}</style>
                  </defs>

                  {data.edges.map((e, i) => {
                    const s = byId.get(e.source)
                    const t = byId.get(e.target)
                    if (!s || !t) return null
                    const dx = t.x - s.x
                    const dy = t.y - s.y
                    const dist = Math.max(1, Math.sqrt(dx * dx + dy * dy))
                    const curvature = Math.min(30, dist * 0.18) * (i % 2 === 0 ? 1 : -1)
                    const mx = (s.x + t.x) / 2 + (-dy / dist) * curvature
                    const my = (s.y + t.y) / 2 + (dx / dist) * curvature
                    const color = ROLE_COLOR[s.role] ?? DEFAULT_COLOR
                    const faded = hovered && hovered !== e.source && hovered !== e.target
                    return (
                      <path
                        key={i}
                        d={`M ${s.x} ${s.y} Q ${mx} ${my} ${t.x} ${t.y}`}
                        fill="none"
                        stroke={color}
                        strokeWidth={0.75 + (e.value_paise / maxVal) * 2.5}
                        strokeDasharray="5 5"
                        className="ng-edge"
                        opacity={faded ? 0.08 : 0.5}
                      />
                    )
                  })}

                  {laidOut.map((n) => {
                    const size = 4 + (n.degree / maxDeg) * 11
                    const color = ROLE_COLOR[n.role] ?? DEFAULT_COLOR
                    const isHovered = hovered === n.id
                    const faded = hovered && !isHovered
                    return (
                      <g
                        key={n.id}
                        onMouseEnter={() => setHovered(n.id)}
                        onMouseLeave={() => setHovered((h) => (h === n.id ? null : h))}
                        opacity={faded ? 0.3 : 1}
                        style={{ cursor: "pointer" }}
                      >
                        <circle
                          cx={n.x} cy={n.y} r={size}
                          fill={color}
                          filter={isHovered || n.role === "hub" ? "url(#ngGlow)" : undefined}
                          stroke={isHovered ? "#fff" : "rgba(5,9,18,0.6)"}
                          strokeWidth={isHovered ? 2 : 1}
                        />
                        {showLabels && (
                          <text
                            x={n.x}
                            y={n.y + size + 11}
                            textAnchor="middle"
                            fontSize={9}
                            fontFamily="ui-monospace, monospace"
                            fill="rgba(226,232,255,0.75)"
                          >
                            {n.id.length > 12 ? `${n.id.slice(0, 11)}…` : n.id}
                          </text>
                        )}
                      </g>
                    )
                  })}
                </svg>

                {hoveredNode && (
                  <div className="absolute top-3 right-3 w-52 rounded-md border border-white/10 bg-[#0b1220]/95 p-3 text-xs shadow-lg backdrop-blur-sm">
                    <div className="flex items-center justify-between gap-2">
                      <span className="truncate font-mono font-semibold text-white">{hoveredNode.id}</span>
                      <span
                        className="rounded px-1.5 py-0.5 text-[10px] font-medium uppercase tracking-wide"
                        style={{ color: ROLE_COLOR[hoveredNode.role] ?? DEFAULT_COLOR, backgroundColor: "rgba(255,255,255,0.08)" }}
                      >
                        {hoveredNode.role}
                      </span>
                    </div>
                    <div className="mt-2 space-y-1 text-white/70">
                      <div className="flex justify-between"><span>Counterparties</span><span className="font-mono text-white">{hoveredNode.degree}</span></div>
                      <div className="flex justify-between"><span>In</span><span className="font-mono text-white">{formatInr(hoveredNode.in_paise)}</span></div>
                      <div className="flex justify-between"><span>Out</span><span className="font-mono text-white">{formatInr(hoveredNode.out_paise)}</span></div>
                    </div>
                  </div>
                )}

                <div className="relative flex flex-wrap items-center gap-4 border-t border-white/10 px-3 py-2 text-xs text-white/60">
                  {(["hub", "collector", "disburser"] as const).map((role) => (
                    <span key={role} className="flex items-center gap-1.5">
                      <span className="inline-block size-2.5 rounded-full" style={{ backgroundColor: ROLE_COLOR[role], boxShadow: `0 0 6px ${ROLE_COLOR[role]}` }} />
                      {role}
                    </span>
                  ))}
                  <span className="flex items-center gap-1.5">
                    <span className="inline-block size-2.5 rounded-full" style={{ backgroundColor: DEFAULT_COLOR }} />
                    other
                  </span>
                </div>
              </div>

              <p className="text-xs text-muted-foreground">
                Node size is degree (counterparties); edge width is transaction value. Hover a node for its detail.
              </p>
            </>
          )}
        </div>
      </DialogContent>
    </Dialog>
  )
}
