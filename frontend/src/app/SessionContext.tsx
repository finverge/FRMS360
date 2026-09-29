import { createContext, useContext } from "react"

import type { Session } from "@/features/auth/AuthPage"

export interface SessionContextValue extends Session {
  isPlatformAdmin: boolean
  signOut: () => void
}

const SessionContext = createContext<SessionContextValue | null>(null)

export function SessionProvider({
  session,
  signOut,
  children,
}: {
  session: Session
  signOut: () => void
  children: React.ReactNode
}) {
  const value: SessionContextValue = {
    ...session,
    isPlatformAdmin: session.role === "platform_admin",
    signOut,
  }
  return <SessionContext.Provider value={value}>{children}</SessionContext.Provider>
}

export function useSession(): SessionContextValue {
  const ctx = useContext(SessionContext)
  if (!ctx) throw new Error("useSession() called outside SessionProvider")
  return ctx
}
