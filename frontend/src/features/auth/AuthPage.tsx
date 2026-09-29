import { useState } from "react"

import type { TokenOut } from "@/api/auth"
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card"
import type { AuthView } from "./types"
import { LoginForm } from "./LoginForm"
import { MfaVerifyForm } from "./MfaVerifyForm"
import { MfaEnrolForm } from "./MfaEnrolForm"
import { BackupCodesDisplay } from "./BackupCodesDisplay"
import { ChangePasswordForm } from "./ChangePasswordForm"
import { SandboxSignupForm } from "./SandboxSignupForm"

export interface Session {
  accessToken: string
  role: string
  tenantId: string | null
}

/** Mirrors services/gateway/app/static/app.js's login state machine exactly - same four
 * branches off a login response (must_change_password / mfa_enrolment_required /
 * mfa_required / else authenticated), same "activate MFA -> show backup codes once ->
 * rehearse with mfa/verify" sequence, because that sequencing is a deliberate security
 * property (routes/auth.py's activate_mfa: the token used to enrol is still
 * mfa_pending-scoped until a real verify happens), not an incidental UI choice this
 * rewrite is free to simplify away. */
export function AuthPage({ onAuthenticated }: { onAuthenticated: (session: Session) => void }) {
  const [view, setView] = useState<AuthView>({ name: "login" })
  // The password just typed at login, so a forced change-password step can pre-fill
  // "current password" the same way the original console does - the user should not
  // have to retype what they entered ten seconds ago.
  const [lastPassword, setLastPassword] = useState("")

  function resolveTokenOut(result: TokenOut, email: string) {
    if (result.must_change_password) {
      setView({ name: "change-password", token: result.access_token })
    } else if (result.mfa_enrolment_required) {
      setView({ name: "mfa-enrol", pendingToken: result.access_token, email })
    } else if (result.mfa_required) {
      setView({ name: "mfa-verify", pendingToken: result.access_token, email })
    } else {
      onAuthenticated({ accessToken: result.access_token, role: result.role, tenantId: result.tenant_id })
    }
  }

  let title = "Control Plane"
  let description = "Multi-tenant fraud & risk administration"
  let body: React.ReactNode

  switch (view.name) {
    case "login":
      body = (
        <LoginForm
          onResult={(result, email, password) => {
            setLastPassword(password)
            resolveTokenOut(result, email)
          }}
          onRequestSandbox={() => setView({ name: "sandbox-signup" })}
        />
      )
      break

    case "sandbox-signup":
      title = "Request a sandbox"
      description = "No login needed — this creates a fully isolated tenant you administer yourself."
      body = <SandboxSignupForm onCancel={() => setView({ name: "login" })} />
      break

    case "change-password":
      title = "Choose a new password"
      body = (
        <ChangePasswordForm
          token={view.token}
          initialCurrentPassword={lastPassword}
          onChanged={(result) => resolveTokenOut(result, "")}
        />
      )
      break

    case "mfa-enrol": {
      const { pendingToken, email } = view
      title = "Set up two-factor authentication"
      body = (
        <MfaEnrolForm
          pendingToken={pendingToken}
          onActivated={(codes) => setView({ name: "mfa-backup-codes", codes, pendingToken, email })}
        />
      )
      break
    }

    case "mfa-backup-codes": {
      const { pendingToken, email } = view
      title = "Save your recovery codes"
      body = (
        <BackupCodesDisplay
          codes={view.codes}
          onDone={() =>
            // Rehearsal, not a formality: the token is still mfa_pending-scoped after
            // activation alone - see the module docstring above.
            setView({ name: "mfa-verify", pendingToken, email })
          }
        />
      )
      break
    }

    case "mfa-verify":
      title = "Verification code"
      body = (
        <MfaVerifyForm
          email={view.email}
          pendingToken={view.pendingToken}
          onVerified={(result) => resolveTokenOut(result, view.email)}
          onBack={() => setView({ name: "login" })}
        />
      )
      break

    case "authenticated":
      body = null
      break
  }

  return (
    <div className="flex min-h-svh items-center justify-center bg-muted/40 p-4">
      <Card className="w-full max-w-sm">
        <CardHeader className="text-center">
          <img src="/logo.png" alt="Fraud360" className="mx-auto mb-2 h-10" />
          <CardTitle>{title}</CardTitle>
          <CardDescription>{description}</CardDescription>
        </CardHeader>
        <CardContent>{body}</CardContent>
      </Card>
    </div>
  )
}
