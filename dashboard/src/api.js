// same protocol as the page: an https page talks to https:// and wss://
export const SERVER_URL = `${window.location.protocol}//${window.location.hostname}:8000`

export class ApiError extends Error {
  constructor(status, message) {
    super(message)
    this.status = status
  }
}

// JSON request to the server, with the login token if given. Throws ApiError on non-2xx.
export async function api(path, { token, method = 'GET', body } = {}) {
  const headers = {}
  if (token) headers.Authorization = `Bearer ${token}`
  if (body !== undefined) headers['Content-Type'] = 'application/json'
  const res = await fetch(`${SERVER_URL}${path}`, {
    method,
    headers,
    body: body === undefined ? undefined : JSON.stringify(body),
  })
  const data = await res.json().catch(() => null)
  if (!res.ok) throw new ApiError(res.status, data?.detail || res.statusText)
  return data
}
