import { useEffect, useState } from "react"
import { AlertCircle } from "lucide-react"

import { ApiError } from "@/api/client"
import { changePassword, passwordPolicy, type TokenOut } from "@/api/auth"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import { Alert, AlertDescription } from "@/components/ui/alert"

export function ChangePasswordForm({
  token,
  initialCurrentPassword,
  onChanged,
}: {
  token: string
  initialCurrentPassword: string
  onChanged: (result: TokenOut) => void
}) {
  const [currentPassword, setCurrentPassword] = useState(initialCurrentPassword)
  const [newPassword, setNewPassword] = useState("")
  const [policy, setPolicy] = useState("")
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)

  useEffect(() => {
    passwordPolicy()
      .then((r) => setPolicy(r.policy))
      .catch(() => {})
  }, [])

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault()
    setError(null)
    setBusy(true)
    try {
      const result = await changePassword(currentPassword, newPassword, token)
      onChanged(result)
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Could not change your password.")
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="space-y-4">
      <p className="text-sm text-muted-foreground">
        This is a temporary password. Choose your own before continuing.
      </p>
      <form onSubmit={handleSubmit} className="space-y-4" noValidate>
        <div className="space-y-2">
          <Label htmlFor="cp-current">Current password</Label>
          <Input
            id="cp-current"
            type="password"
            autoComplete="current-password"
            required
            value={currentPassword}
            onChange={(e) => setCurrentPassword(e.target.value)}
          />
        </div>
        <div className="space-y-2">
          <Label htmlFor="cp-new">New password</Label>
          <Input
            id="cp-new"
            type="password"
            autoComplete="new-password"
            required
            value={newPassword}
            onChange={(e) => setNewPassword(e.target.value)}
            aria-describedby="cp-policy"
          />
          {policy && (
            <p id="cp-policy" className="text-xs text-muted-foreground">
              {policy}
            </p>
          )}
        </div>

        {error && (
          <Alert variant="destructive">
            <AlertCircle />
            <AlertDescription>{error}</AlertDescription>
          </Alert>
        )}

        <Button type="submit" className="w-full" disabled={busy}>
          {busy ? "Changing…" : "Change password"}
        </Button>
      </form>
    </div>
  )
}
