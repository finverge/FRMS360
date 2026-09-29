import { Card, CardContent } from "@/components/ui/card"
import { StatCell } from "@/components/ui/sidebar"

// CHN-04 (device-posture) / CHN-05 (behavioural-biometric anomaly) - see
// services/config_service/app/ews_catalogue.py. Both are real, already-wired EWS rules:
// the score is sourced from an integrated device-fingerprinting / behavioural-biometrics
// provider (e.g. VideoPD, the Human Fraud Detection Framework) - Fraud360 consumes the
// score as a transaction feature, it does not compute one itself, which is why this
// panel is usually empty (see detection/features.py's NEEDS_EXTERNAL_DATA: unmeasurable
// until a tenant's feed actually supplies it, not "checked and clean").
const THRESHOLD = 0.7

function ScoreRow({
  label, score, description,
}: {
  label: string
  score: number | null | undefined
  description: string
}) {
  if (score == null) {
    return (
      <Card className="py-3">
        <CardContent className="px-4">
          <div className="text-[11px] font-medium tracking-wide text-muted-foreground uppercase">{label}</div>
          <div className="mt-1 text-sm text-muted-foreground">Not received for this transaction</div>
        </CardContent>
      </Card>
    )
  }
  const pct = Math.round(score * 100)
  const flagged = score >= THRESHOLD
  return (
    <Card className="py-3">
      <CardContent className="space-y-1.5 px-4">
        <StatCell
          label={label}
          value={`${pct}%`}
          tone={flagged ? "warning" : "default"}
        />
        <div className="h-1.5 w-full overflow-hidden rounded-full bg-muted">
          <div
            className={"h-full rounded-full " + (flagged ? "bg-warning" : "bg-primary")}
            style={{ width: `${pct}%` }}
          />
        </div>
        <p className="text-xs text-muted-foreground">
          {flagged ? `Above the ${THRESHOLD * 100}% alerting threshold — ` : ""}{description}
        </p>
      </CardContent>
    </Card>
  )
}

export function BehavioralBiometricsPanel({ transaction }: { transaction: Record<string, unknown> }) {
  const deviceRiskScore = transaction.device_risk_score as number | null | undefined
  const behaviorAnomalyScore = transaction.behavior_anomaly_score as number | null | undefined
  const noSignal = deviceRiskScore == null && behaviorAnomalyScore == null

  return (
    <div className="space-y-3">
      <div className="grid gap-3 sm:grid-cols-2">
        <ScoreRow
          label="Device-posture risk (CHN-04)"
          score={deviceRiskScore}
          description="jailbreak/root, emulator, SIM-swap or similar device-integrity signals"
        />
        <ScoreRow
          label="Behavioural-biometric anomaly (CHN-05)"
          score={behaviorAnomalyScore}
          description="typing cadence, touch pressure, session/gesture pattern deviation"
        />
      </div>
      <p className="text-xs text-muted-foreground">
        {noSignal
          ? "No device-fingerprinting or behavioural-biometrics signal was received for this transaction. " +
            "Fraud360 consumes these scores from an integrated provider (e.g. VideoPD, the Human Fraud " +
            "Detection Framework) - it does not capture device or behavioural telemetry itself, since it has " +
            "no presence in the bank's own banking app. Wiring a live provider is a separate integration " +
            "decision, tracked as CHN-04/CHN-05 in the rule catalogue."
          : "Sourced from an integrated device-fingerprinting / behavioural-biometrics provider at the time " +
            "this transaction was scored - Fraud360 consumes this score as a feature, it does not compute it."}
      </p>
    </div>
  )
}
