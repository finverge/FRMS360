import { useState } from "react"

import { AuthPage, type Session } from "@/features/auth/AuthPage"
import { ConsoleApp } from "@/app/ConsoleApp"
import { ThemeProvider } from "@/app/ThemeContext"

export default function App() {
  const [session, setSession] = useState<Session | null>(null)

  return (
    <ThemeProvider>
      {session ? (
        <ConsoleApp session={session} signOut={() => setSession(null)} />
      ) : (
        <AuthPage onAuthenticated={setSession} />
      )}
    </ThemeProvider>
  )
}
