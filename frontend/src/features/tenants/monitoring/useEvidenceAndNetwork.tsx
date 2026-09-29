import { useState } from "react"

import type { EvidenceEntity } from "@/api/evidence"
import { EvidenceDrawer } from "./EvidenceDrawer"
import { NetworkGraphView } from "./NetworkGraphView"

/** One place to wire "click a row -> see its evidence trail, optionally pivot to the
 * account network behind its case" - every dashboard that lists alert/case/transaction
 * rows wants the same two dialogs, so this is the one piece each of them owns instead
 * of repeating the dialog-state plumbing nine times. */
export function useEvidenceAndNetwork(tenantId: string) {
  const [evidence, setEvidence] = useState<{ entity: EvidenceEntity; ident: string } | null>(null)
  const [network, setNetwork] = useState<{ case_id?: string; account?: string } | null>(null)

  function openEvidence(entity: EvidenceEntity, ident: string) {
    setEvidence({ entity, ident })
  }

  function openNetwork(scope: { case_id?: string; account?: string }) {
    // Both dialogs are the Dialog primitive's own portal, so two open at once stack
    // rather than replace one another - closing the drawer first, not just opening the
    // graph on top of it, keeps exactly one of these two modal views up at a time.
    setEvidence(null)
    setNetwork(scope)
  }

  const dialogs = (
    <>
      {evidence && (
        <EvidenceDrawer
          tenantId={tenantId}
          entity={evidence.entity}
          ident={evidence.ident}
          open={!!evidence}
          onOpenChange={(open) => !open && setEvidence(null)}
          onViewNetwork={(caseId) => openNetwork({ case_id: caseId })}
        />
      )}
      {network && (
        <NetworkGraphView
          tenantId={tenantId}
          scope={network}
          open={!!network}
          onOpenChange={(open) => !open && setNetwork(null)}
        />
      )}
    </>
  )

  return { openEvidence, openNetwork, dialogs }
}
