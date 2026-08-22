/**
 * The one place that calls fetch.
 *
 * Everything above this file works in typed objects and `ApiError`, and no
 * component builds a URL or reads a status code. Two things this centralises
 * that are easy to get wrong per-call-site:
 *
 * - **A failed request is an exception.** `fetch` resolves for a 500 as
 *   happily as for a 200, so every caller would otherwise need its own `if
 *   (!response.ok)`, and the one that forgets renders an error body as data.
 * - **The user-facing message is chosen here.** The backend deliberately keeps
 *   provider and database internals out of its error bodies, and this keeps the
 *   frontend from putting them back by rendering a raw `detail`.
 */

const BASE_URL = (import.meta.env.VITE_API_URL ?? 'http://localhost:8000').replace(/\/$/, '')

export const API_PREFIX = '/api/v1'

/** A request that did not succeed. `status` is 0 when it never reached the server. */
export class ApiError extends Error {
  readonly status: number

  constructor(message: string, status: number) {
    super(message)
    this.name = 'ApiError'
    this.status = status
  }

  /** True when retrying the same request could plausibly work. */
  get isRetryable(): boolean {
    return this.status === 0 || this.status === 503 || this.status >= 500
  }
}

/** What to tell the user, by status. Short, plain, and never a stack trace. */
function messageFor(status: number, detail: string | null): string {
  if (status === 0) {
    return 'Could not reach the server. Check that the backend is running.'
  }
  if (status === 404) {
    return 'That conversation could not be found.'
  }
  if (status === 422) {
    // The only 422s reachable from the UI are message validation, and the
    // composer already prevents them -- so this is worth stating plainly.
    return detail ?? 'That message could not be sent.'
  }
  if (status === 503) {
    return 'The server is temporarily unavailable. Please try again.'
  }
  if (status >= 500) {
    return 'Something went wrong on the server. Please try again.'
  }
  return detail ?? 'Something went wrong. Please try again.'
}

/** Pull FastAPI's `detail` out of an error body, if there is a readable one. */
async function detailOf(response: Response): Promise<string | null> {
  try {
    const body = await response.json()
    const detail = (body as { detail?: unknown }).detail
    return typeof detail === 'string' ? detail : null
  } catch {
    return null
  }
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  let response: Response
  try {
    response = await fetch(`${BASE_URL}${API_PREFIX}${path}`, {
      ...init,
      headers: { 'Content-Type': 'application/json', ...init?.headers },
    })
  } catch (error) {
    // Network-level: the server was never reached, so there is no status.
    console.error('request failed before reaching the server', error)
    throw new ApiError(messageFor(0, null), 0)
  }

  if (!response.ok) {
    const detail = await detailOf(response)
    console.error(`${init?.method ?? 'GET'} ${path} -> ${response.status}`, detail)
    throw new ApiError(messageFor(response.status, detail), response.status)
  }

  return (await response.json()) as T
}

export const api = {
  get: <T>(path: string) => request<T>(path),
  post: <T>(path: string, body?: unknown) =>
    request<T>(path, {
      method: 'POST',
      body: body === undefined ? undefined : JSON.stringify(body),
    }),
}
