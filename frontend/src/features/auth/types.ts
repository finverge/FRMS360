export type AuthView =
  | { name: "login" }
  | { name: "mfa-verify"; pendingToken: string; email: string }
  | { name: "mfa-enrol"; pendingToken: string; email: string }
  | { name: "mfa-backup-codes"; codes: string[]; pendingToken: string; email: string }
  | { name: "change-password"; token: string }
  | { name: "sandbox-signup" }
  | { name: "authenticated"; role: string; tenantId: string | null }
