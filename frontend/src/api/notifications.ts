import { apiFetch } from "./client"

// Mirrors services/notification_service/app/routes/notify.py's tenant-admin surface
// (channel config, test send, delivery problems). The personal inbox/preferences
// endpoints are a signed-in user's own settings, not a tenant-admin concern, and are
// out of scope here.

export interface ChannelConfig {
  host?: string
  port?: number
  from?: string
  username?: string
  starttls?: boolean
  url?: string
  max_attempts?: number
  retry_backoff_minutes?: number[]
  timeout_seconds?: number
}

export interface ChannelOut {
  channel: "email" | "webhook"
  enabled: boolean
  min_severity: "info" | "warn" | "urgent"
  // Credentials (password, headers) are never returned - not even to the tenant's own
  // administrator - only whether one is on file, so the console can show "unchanged"
  // instead of a blank that looks like nothing was ever set.
  config: ChannelConfig
  has_credentials: boolean
}

export interface ChannelWrite {
  channel: "email" | "webhook"
  enabled: boolean
  min_severity: string
  config: ChannelConfig & { password?: string; headers?: Record<string, string> }
}

export function listChannels(tenantId: string, token: string) {
  return apiFetch<ChannelOut[]>(`/notifications/${tenantId}/channels`, { token })
}

export function setChannel(tenantId: string, payload: ChannelWrite, token: string) {
  return apiFetch<{ channel: string; enabled: boolean }>(`/notifications/${tenantId}/channels`, {
    method: "PUT",
    body: payload,
    token,
  })
}

export interface TestSendResult {
  ok: boolean
  channel: string
  to: string
  detail: string
}

export function sendTest(tenantId: string, channel: string, to: string, token: string) {
  return apiFetch<TestSendResult>(`/notifications/${tenantId}/channels/test`, {
    method: "POST",
    body: { channel, to },
    token,
  })
}

export interface DeliveryOut {
  id: string
  channel: string
  target: string
  status: string
  attempts: number
  last_error: string
  created_at: string
  sent_at: string | null
  next_attempt_at: string | null
  subject: string
  recipient: string
}

export interface DeliveriesOut {
  counts: Record<string, number>
  items: DeliveryOut[]
}

export function listDeliveries(tenantId: string, token: string, status: string = "problems") {
  return apiFetch<DeliveriesOut>(`/notifications/${tenantId}/deliveries?status=${status}`, { token })
}

export function retryDelivery(tenantId: string, deliveryId: string, token: string) {
  return apiFetch<{ ok?: boolean }>(`/notifications/${tenantId}/deliveries/${deliveryId}/retry`, {
    method: "POST",
    token,
  })
}

export function retryAll(tenantId: string, token: string, channel: string = "") {
  return apiFetch<{ requeued: number }>(`/notifications/${tenantId}/deliveries/retry-all`, {
    method: "POST",
    body: { channel, include_retrying: true },
    token,
  })
}
