import { useState } from "react"

import { useSession } from "@/app/SessionContext"
import { ApiError } from "@/api/client"
import { sendTest, setChannel, type ChannelOut } from "@/api/notifications"
import { Alert, AlertDescription } from "@/components/ui/alert"
import { Button } from "@/components/ui/button"
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select"

/** Parses "1, 5, 15, 60" into [1,5,15,60]; empty/invalid means "use the platform
 * default", matching the old console's retryPolicy() - never "wait zero minutes". */
function parseBackoff(text: string): number[] | undefined {
  const steps = text.split(/[,\s]+/).map((x) => parseInt(x, 10)).filter((n) => Number.isFinite(n) && n > 0)
  return steps.length ? steps : undefined
}

export function ChannelCard({
  tenantId,
  channel,
  title,
  saved,
  onSaved,
}: {
  tenantId: string
  channel: "email" | "webhook"
  title: string
  saved: ChannelOut | undefined
  onSaved: () => void
}) {
  const { accessToken } = useSession()
  const cfg = saved?.config ?? {}
  const [enabled, setEnabled] = useState(saved?.enabled ?? false)
  const [severity, setSeverity] = useState<string>(saved?.min_severity ?? "warn")
  const [host, setHost] = useState(cfg.host ?? "")
  const [port, setPort] = useState(String(cfg.port ?? 25))
  const [from, setFrom] = useState(cfg.from ?? "")
  const [username, setUsername] = useState(cfg.username ?? "")
  const [password, setPassword] = useState("")
  const [starttls, setStarttls] = useState(cfg.starttls ?? true)
  const [url, setUrl] = useState(cfg.url ?? "")
  const [maxAttempts, setMaxAttempts] = useState(String(cfg.max_attempts ?? 5))
  const [backoff, setBackoff] = useState((cfg.retry_backoff_minutes ?? [1, 5, 15, 60]).join(", "))
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)
  const [testBusy, setTestBusy] = useState(false)
  const [testResult, setTestResult] = useState<{ ok: boolean; text: string } | null>(null)

  async function handleSave(e: React.FormEvent) {
    e.preventDefault()
    setError(null)
    setBusy(true)
    try {
      const config: Record<string, unknown> = {
        max_attempts: Number(maxAttempts) || 5,
        retry_backoff_minutes: parseBackoff(backoff),
      }
      if (channel === "email") {
        config.host = host.trim()
        config.port = Number(port) || 25
        config.from = from.trim()
        config.username = username.trim()
        config.starttls = starttls
        // Only sent when typed - the field is never pre-filled, so submitting it
        // empty must not wipe a working credential (see notify.py's set_channel merge).
        if (password) config.password = password
      } else {
        config.url = url.trim()
      }
      await setChannel(tenantId, { channel, enabled, min_severity: severity, config }, accessToken)
      setPassword("")
      onSaved()
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Could not save this channel.")
    } finally {
      setBusy(false)
    }
  }

  async function handleTest() {
    setTestResult(null)
    setTestBusy(true)
    try {
      const res = await sendTest(tenantId, channel, "", accessToken)
      setTestResult({ ok: res.ok, text: res.ok ? `Delivered to ${res.to} — ${res.detail}` : `Not delivered: ${res.detail}` })
    } catch (err) {
      setTestResult({ ok: false, text: err instanceof ApiError ? err.message : "Could not send a test." })
    } finally {
      setTestBusy(false)
    }
  }

  return (
    <Card>
      <CardHeader>
        <CardTitle className="flex items-center justify-between text-base">
          {title}
          <label className="flex items-center gap-2 text-sm font-normal">
            <input type="checkbox" checked={enabled} onChange={(e) => setEnabled(e.target.checked)} />
            Enabled
          </label>
        </CardTitle>
      </CardHeader>
      <CardContent>
        <form onSubmit={handleSave} className="space-y-4">
          {channel === "email" ? (
            <div className="grid gap-4 sm:grid-cols-2">
              <div className="space-y-2">
                <Label htmlFor={`${channel}-host`}>SMTP host</Label>
                <Input id={`${channel}-host`} value={host} onChange={(e) => setHost(e.target.value)} />
              </div>
              <div className="space-y-2">
                <Label htmlFor={`${channel}-port`}>Port</Label>
                <Input id={`${channel}-port`} type="number" value={port} onChange={(e) => setPort(e.target.value)} />
              </div>
              <div className="space-y-2">
                <Label htmlFor={`${channel}-from`}>From address</Label>
                <Input id={`${channel}-from`} value={from} onChange={(e) => setFrom(e.target.value)} placeholder="frms-noreply@bank.example" />
              </div>
              <label className="flex items-center gap-2 self-end pb-2 text-sm">
                <input type="checkbox" checked={starttls} onChange={(e) => setStarttls(e.target.checked)} />
                STARTTLS
              </label>
              <div className="space-y-2">
                <Label htmlFor={`${channel}-user`}>Username</Label>
                <Input id={`${channel}-user`} value={username} onChange={(e) => setUsername(e.target.value)} />
              </div>
              <div className="space-y-2">
                <Label htmlFor={`${channel}-pass`}>Password</Label>
                <Input
                  id={`${channel}-pass`}
                  type="password"
                  value={password}
                  onChange={(e) => setPassword(e.target.value)}
                  placeholder={saved?.has_credentials ? "unchanged" : ""}
                />
              </div>
            </div>
          ) : (
            <div className="space-y-2">
              <Label htmlFor={`${channel}-url`}>Webhook URL</Label>
              <Input id={`${channel}-url`} value={url} onChange={(e) => setUrl(e.target.value)} placeholder="https://.../incidents" />
            </div>
          )}

          <div className="grid gap-4 sm:grid-cols-3">
            <div className="space-y-2">
              <Label htmlFor={`${channel}-sev`}>Minimum severity</Label>
              <Select value={severity} onValueChange={setSeverity}>
                <SelectTrigger id={`${channel}-sev`}><SelectValue /></SelectTrigger>
                <SelectContent>
                  <SelectItem value="info">Info</SelectItem>
                  <SelectItem value="warn">Warn</SelectItem>
                  <SelectItem value="urgent">Urgent</SelectItem>
                </SelectContent>
              </Select>
            </div>
            <div className="space-y-2">
              <Label htmlFor={`${channel}-tries`}>Max attempts</Label>
              <Input id={`${channel}-tries`} type="number" value={maxAttempts} onChange={(e) => setMaxAttempts(e.target.value)} />
            </div>
            <div className="space-y-2">
              <Label htmlFor={`${channel}-backoff`}>Backoff (minutes)</Label>
              <Input id={`${channel}-backoff`} value={backoff} onChange={(e) => setBackoff(e.target.value)} placeholder="1, 5, 15, 60" />
            </div>
          </div>

          {error && (
            <Alert variant="destructive">
              <AlertDescription>{error}</AlertDescription>
            </Alert>
          )}

          <div className="flex flex-wrap items-center gap-3">
            <Button type="submit" disabled={busy}>{busy ? "Saving…" : "Save"}</Button>
            <Button type="button" variant="outline" onClick={handleTest} disabled={testBusy}>
              {testBusy ? "Sending…" : "Send test"}
            </Button>
          </div>

          {testResult && (
            <p className={"text-sm " + (testResult.ok ? "text-success" : "text-destructive")}>{testResult.text}</p>
          )}
        </form>
      </CardContent>
    </Card>
  )
}
