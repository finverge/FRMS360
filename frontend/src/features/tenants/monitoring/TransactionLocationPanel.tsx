import L from "leaflet"
import "leaflet/dist/leaflet.css"
import markerIcon2x from "leaflet/dist/images/marker-icon-2x.png"
import markerIcon from "leaflet/dist/images/marker-icon.png"
import markerShadow from "leaflet/dist/images/marker-shadow.png"
import { useEffect, useRef, useState } from "react"

import { useSession } from "@/app/SessionContext"
import { ApiError } from "@/api/client"
import { geolocateTransaction, type GeolocateOut } from "@/api/evidence"
import { Alert, AlertDescription } from "@/components/ui/alert"
import { Button } from "@/components/ui/button"
import { StatCell } from "@/components/ui/sidebar"

// Vite/webpack both break Leaflet's default marker icon URLs (they're relative paths
// baked into the library's own CSS) - the standard fix is pointing the default icon at
// the bundled asset URLs explicitly, once, at module load.
delete (L.Icon.Default.prototype as unknown as { _getIconUrl?: unknown })._getIconUrl
L.Icon.Default.mergeOptions({ iconRetinaUrl: markerIcon2x, iconUrl: markerIcon, shadowUrl: markerShadow })

// There is no lat/long in Fraud360's data model (see the BRD gap this partially closes)
// - only a transaction's real IP address. This resolves that IP to an approximate
// location via a real, free IP-geolocation lookup (ip-api.com) and plots it on a real
// OpenStreetMap tile map - never a fabricated pin. An on-demand read, not something
// computed at ingestion time; see geolocation.py's module docstring for that trade-off.
export function TransactionLocationPanel({
  tenantId,
  txnId,
  revealed,
  justification,
}: {
  tenantId: string
  txnId: string
  revealed: boolean
  justification?: string
}) {
  const { accessToken } = useSession()
  const [busy, setBusy] = useState(false)
  const [data, setData] = useState<GeolocateOut | null>(null)
  const [error, setError] = useState<string | null>(null)
  const mapRef = useRef<HTMLDivElement>(null)
  const mapInstance = useRef<L.Map | null>(null)

  function handleLocate() {
    setBusy(true)
    setError(null)
    geolocateTransaction(tenantId, txnId, { reveal: true, justification }, accessToken)
      .then(setData)
      .catch((err) => {
        if (err instanceof ApiError && err.code === "geolocation_unavailable") {
          setError("The geolocation service isn't reachable right now. Try again shortly.")
        } else if (err instanceof ApiError) {
          setError(err.message)
        } else {
          setError("Could not locate this transaction.")
        }
      })
      .finally(() => setBusy(false))
  }

  useEffect(() => {
    if (!data?.locatable || !mapRef.current || data.lat == null || data.lon == null) return
    if (!mapInstance.current) {
      mapInstance.current = L.map(mapRef.current)
      L.tileLayer("https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png", {
        attribution: "© OpenStreetMap contributors",
        maxZoom: 18,
      }).addTo(mapInstance.current)
    }
    mapInstance.current.setView([data.lat, data.lon], 10)
    const marker = L.marker([data.lat, data.lon]).addTo(mapInstance.current)
    return () => {
      marker.remove()
    }
  }, [data])

  useEffect(() => () => { mapInstance.current?.remove(); mapInstance.current = null }, [])

  if (!revealed) {
    return (
      <p className="text-sm text-muted-foreground">
        Reveal PII (in the panel on the right) to locate this transaction - resolving an IP address to a
        place needs the real address, not the masked one.
      </p>
    )
  }

  if (!data) {
    return (
      <div className="flex flex-col items-center gap-3 rounded-lg border border-dashed border-border py-10 text-center">
        <p className="max-w-sm text-sm text-muted-foreground">
          Resolve this transaction's real IP address to an approximate location via a real (free)
          IP-geolocation lookup - not a stored coordinate, since this product's data model doesn't capture
          one.
        </p>
        <Button onClick={handleLocate} disabled={busy}>
          {busy ? "Locating…" : "Locate this transaction"}
        </Button>
        {error && (
          <Alert variant="destructive" className="mt-2 max-w-sm text-left">
            <AlertDescription>{error}</AlertDescription>
          </Alert>
        )}
      </div>
    )
  }

  if (!data.locatable) {
    return (
      <div className="space-y-2">
        <p className="text-sm text-muted-foreground">
          IP <span className="font-mono">{data.ip}</span> could not be located ({data.reason ?? "not locatable"}) -
          this is common for private/reserved IP ranges, which no geolocation service can resolve to a real place.
        </p>
        <Button size="sm" variant="ghost" onClick={handleLocate} disabled={busy}>Try again</Button>
      </div>
    )
  }

  return (
    <div className="space-y-3">
      <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
        <StatCell label="City" value={data.city || "—"} />
        <StatCell label="Region" value={data.region || "—"} />
        <StatCell label="Country" value={data.country || "—"} />
        <StatCell label="ISP" value={data.isp || "—"} />
      </div>
      <div ref={mapRef} className="h-72 w-full overflow-hidden rounded-lg border border-border" />
      <p className="text-xs text-muted-foreground">
        Approximate location of IP <span className="font-mono">{data.ip}</span> ({data.lat?.toFixed(4)}, {data.lon?.toFixed(4)}),
        via a free IP-geolocation lookup - an ISP's registered location, not necessarily the device's actual
        position.
      </p>
    </div>
  )
}
