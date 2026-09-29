import { useEffect, useState } from "react"

import { useSession } from "@/app/SessionContext"
import { ApiError } from "@/api/client"
import { listChannels, type ChannelOut } from "@/api/notifications"
import { Alert, AlertDescription } from "@/components/ui/alert"
import { ChannelCard } from "./ChannelCard"
import { DeliveryProblems } from "./DeliveryProblems"

export function NotificationsPanel({ tenantId }: { tenantId: string }) {
  const { accessToken } = useSession()
  const [channels, setChannels] = useState<ChannelOut[] | null>(null)
  const [error, setError] = useState<string | null>(null)
  // Bumped on every channel save so DeliveryProblems re-fetches - a corrected setting
  // is the realistic moment a stuck delivery is worth checking again.
  const [refreshToken, setRefreshToken] = useState(0)

  function reload() {
    listChannels(tenantId, accessToken)
      .then((rows) => {
        setChannels(rows)
        setRefreshToken((n) => n + 1)
      })
      .catch((err) => setError(err instanceof ApiError ? err.message : "Could not load delivery settings."))
  }

  // eslint-disable-next-line react-hooks/exhaustive-deps
  useEffect(reload, [tenantId, accessToken])

  const email = channels?.find((c) => c.channel === "email")
  const webhook = channels?.find((c) => c.channel === "webhook")
  const live = [email?.enabled && "email", webhook?.enabled && "webhook"].filter(Boolean)

  return (
    <div className="space-y-6">
      <p className="text-sm text-muted-foreground">
        {channels === null
          ? "Loading…"
          : live.length
            ? `Sending via ${live.join(" and ")}`
            : "Nothing leaves the platform — inbox only"}
      </p>

      {error && (
        <Alert variant="destructive">
          <AlertDescription>{error}</AlertDescription>
        </Alert>
      )}

      {/* Mounted only once the real settings have loaded, not with `saved` undefined
          then silently updated - ChannelCard seeds its form fields from `saved` once,
          the same lesson learned from TenantDetail/RoleForm re-syncing on stale props. */}
      {channels !== null && (
        <div className="grid gap-4 lg:grid-cols-2">
          <ChannelCard tenantId={tenantId} channel="email" title="Email (SMTP)" saved={email} onSaved={reload} />
          <ChannelCard tenantId={tenantId} channel="webhook" title="Webhook" saved={webhook} onSaved={reload} />
        </div>
      )}

      <DeliveryProblems tenantId={tenantId} refreshToken={refreshToken} />
    </div>
  )
}
