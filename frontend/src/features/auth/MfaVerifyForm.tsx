import { useState } from "react"
import { AlertCircle } from "lucide-react"

import { ApiError } from "@/api/client"
import { verifyMfa, type TokenOut } from "@/api/auth"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import { Alert, AlertDescription } from "@/components/ui/alert"

export function MfaVerifyForm({
  email,
  pendingToken,
  onVerified,
  onBack,
}: {
  email: string
  pendingToken: string
  onVerified: (result: TokenOut) => void
  onBack: () => void
}) {
  const [code, setCode] = useState("")
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault()
    setError(null)
    setBusy(true)
    try {
      const result = await verifyMfa(code, pendingToken)
      onVerified(result)
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Could not verify that code.")
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="space-y-4">
      <p className="text-sm text-muted-foreground">
        Open your authenticator app and enter the current 6-digit code for{" "}
        <span className="font-medium text-foreground">{email}</span>.
      </p>
      <form onSubmit={handleSubmit} className="space-y-4" noValidate>
        <div className="space-y-2">
          <Label htmlFor="mfa-code">Code</Label>
          <Input
            id="mfa-code"
            inputMode="numeric"
            autoComplete="one-time-code"
            placeholder="123456"
            autoFocus
            required
            value={code}
            onChange={(e) => setCode(e.target.value)}
          />
          <p className="text-xs text-muted-foreground">
            Lost your device? Enter one of your recovery codes instead — each works once.
          </p>
        </div>

        {error && (
          <Alert variant="destructive">
            <AlertCircle />
            <AlertDescription>{error}</AlertDescription>
          </Alert>
        )}

        <Button type="submit" className="w-full" disabled={busy}>
          {busy ? "Verifying…" : "Verify"}
        </Button>
      </form>
      <Button type="button" variant="ghost" className="w-full" onClick={onBack}>
        Back to sign in
      </Button>
    </div>
  )
}
