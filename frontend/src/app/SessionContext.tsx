import { createContext, useContext } from "react"

import type { MeOut } from "@/api/auth"
import type { Session } from "@/features/auth/AuthPage"

export interface SessionContextValue extends Session {
  isPlatformAdmin: boolean
  /** What this role may use, from GET /auth/me (see lib/access.ts for the rules built on it). */
  me: MeOut
  signOut: () => void
}

const SessionContext = createContext<SessionContextValue | null>(null)

export function SessionProvider({
  session,
  me,
  signOut,
  children,
}: {
  session: Session
  me: MeOut
  signOut: () => void
  children: React.ReactNode
}) {
  const value: SessionContextValue = {
    ...session,
    isPlatformAdmin: session.role === "platform_admin",
    me,
    signOut,
  }
  return <SessionContext.Provider value={value}>{children}</SessionContext.Provider>
}

export function useSession(): SessionContextValue {
  const ctx = useContext(SessionContext)
  if (!ctx) throw new Error("useSession() called outside SessionProvider")
  return ctx
}
