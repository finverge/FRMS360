import { AlertCircle } from "lucide-react"

import { Button } from "@/components/ui/button"
import { Alert, AlertDescription } from "@/components/ui/alert"

export function BackupCodesDisplay({ codes, onDone }: { codes: string[]; onDone: () => void }) {
  return (
    <div className="space-y-4">
      <Alert variant="destructive">
        <AlertCircle />
        <AlertDescription>
          Store these recovery codes now. Each works once, and they cannot be shown again.
        </AlertDescription>
      </Alert>
      <div className="grid grid-cols-2 gap-2 rounded-md bg-muted p-4 font-mono text-sm">
        {codes.map((c) => (
          <span key={c}>{c}</span>
        ))}
      </div>
      <Button type="button" className="w-full" onClick={onDone}>
        I've saved these — continue
      </Button>
    </div>
  )
}
