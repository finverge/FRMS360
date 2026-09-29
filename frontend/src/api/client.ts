/**
 * Thin fetch wrapper against the gateway's /api/* surface (services/gateway/app/main.py
 * proxies /api/{path} to the owning service - see vite.config.ts for the dev-time proxy
 * that makes this the same base path in dev and in production).
 *
 * The uniform error envelope every service returns - {"error": {"code", "message"}} -
 * (packages/cp_common/cp_common/errors.py) is unpacked here once, so every caller gets a
 * real ApiError with .code and .message instead of re-parsing the body itself.
 */
const API_BASE = "/api"

export class ApiError extends Error {
  code: string
  status: number
  constructor(message: string, code: string, status: number) {
    super(message)
    this.code = code
    this.status = status
  }
}

export interface RequestOptions {
  method?: "GET" | "POST" | "PUT" | "PATCH" | "DELETE"
  body?: unknown
  token?: string | null
}

export async function apiFetch<T>(path: string, options: RequestOptions = {}): Promise<T> {
  const { method = "GET", body, token } = options
  const headers: Record<string, string> = { "Content-Type": "application/json" }
  if (token) headers["Authorization"] = `Bearer ${token}`

  const res = await fetch(`${API_BASE}${path}`, {
    method,
    headers,
    body: body !== undefined ? JSON.stringify(body) : undefined,
  })

  const text = await res.text()
  const data = text ? JSON.parse(text) : null

  if (!res.ok) {
    const err = data?.error ?? { code: "unknown_error", message: res.statusText }
    throw new ApiError(err.message ?? "Something went wrong.", err.code ?? "unknown_error", res.status)
  }
  return data as T
}
