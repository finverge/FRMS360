import { useEffect, useState } from "react"
import { AlertCircle } from "lucide-react"

import { ApiError } from "@/api/client"
import { activateMfa, startMfaEnrolment, type MfaEnrolOut } from "@/api/auth"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import { Alert, AlertDescription } from "@/components/ui/alert"

export function MfaEnrolForm({
  pendingToken,
  onActivated,
}: {
  pendingToken: string
  onActivated: (codes: string[]) => void
}) {
  const [enrol, setEnrol] = useState<MfaEnrolOut | null>(null)
  const [code, setCode] = useState("")
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)

  useEffect(() => {
    let cancelled = false
    startMfaEnrolment(pendingToken)
      .then((result) => !cancelled && setEnrol(result))
      .catch((err) => !cancelled && setError(err instanceof ApiError ? err.message : "Could not start enrolment."))
    return () => {
      cancelled = true
    }
  }, [pendingToken])

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault()
    setError(null)
    setBusy(true)
    try {
      const result = await activateMfa(code, pendingToken)
      onActivated(result.backup_codes)
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Could not activate two-factor auth.")
    } finally {
      setBusy(false)
    }
  }

  if (!enrol && !error) {
    return <p className="text-sm text-muted-foreground">Setting up two-factor authentication…</p>
  }

  return (
    <div className="space-y-4">
      <p className="text-sm text-muted-foreground">
        Your bank requires a second factor for sign-in. Scan this QR code with an
        authenticator app (Google Authenticator, Microsoft Authenticator, 1Password, …).
      </p>

      {enrol && (
        <>
          <div
            className="flex justify-center rounded-lg border border-border bg-white p-4"
            // Server-rendered SVG from a TOTP URI this app constructed (cp_common.mfa's
            // qrcode-package output) - not user input, safe to inline.
            dangerouslySetInnerHTML={{ __html: enrol.qr_svg }}
          />
          <div className="rounded-md bg-muted px-3 py-2">
            <p className="text-xs text-muted-foreground">Can't scan? Enter this key manually:</p>
            <p className="break-all font-mono text-sm">{enrol.secret}</p>
          </div>
        </>
      )}

      <form onSubmit={handleSubmit} className="space-y-4" noValidate>
        <div className="space-y-2">
          <Label htmlFor="enrol-code">Enter the 6-digit code to confirm</Label>
          <Input
            id="enrol-code"
            inputMode="numeric"
            autoComplete="one-time-code"
            placeholder="123456"
            required
            value={code}
            onChange={(e) => setCode(e.target.value)}
          />
        </div>

        {error && (
          <Alert variant="destructive">
            <AlertCircle />
            <AlertDescription>{error}</AlertDescription>
          </Alert>
        )}

        <Button type="submit" className="w-full" disabled={busy || !enrol}>
          {busy ? "Activating…" : "Activate"}
        </Button>
      </form>
    </div>
  )
}
