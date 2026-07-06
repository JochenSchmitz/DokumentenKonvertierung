export interface DocumentOut {
  id: string
  filename: string
  mime: string
  size_bytes: number
  status: 'pending' | 'processing' | 'done' | 'error'
  error: string | null
  page_count: number | null
  tags: string[]
  summary: string | null
  doc_date: string | null
  uploaded_at: string
  processed_at: string | null
}

export interface PageOut {
  page_no: number
  content_md: string
}

export interface DocumentDetail extends DocumentOut {
  pages: PageOut[]
}

export interface AppConfig {
  onlyofficeUrl: string
  apiBaseUrl: string
  ocrModelUp: boolean
}

export interface ProcessingStatus {
  processing: string[]
  currentPages: number | null
  pending: number
  modelUp: boolean
  runningRequests: number
  waitingRequests: number
  generatedTokens: number
  promptTokens: number
}

async function request<T>(url: string, init?: RequestInit): Promise<T> {
  const resp = await fetch(url, init)
  if (resp.status === 401) {
    // Sitzung abgelaufen -> zur Anmeldung
    window.location.href = `/login?weiter=${encodeURIComponent(location.pathname)}`
    throw new Error('Nicht angemeldet')
  }
  if (!resp.ok) {
    let detail = resp.statusText
    try {
      detail = (await resp.json()).detail ?? detail
    } catch { /* Antwort ohne JSON-Body */ }
    throw new Error(detail)
  }
  if (resp.status === 204) return undefined as T
  return resp.json()
}

export const api = {
  config: () => request<AppConfig>('/api/config'),
  status: () => request<ProcessingStatus>('/api/status'),
  list: (q = '') =>
    request<DocumentOut[]>(`/api/documents?q=${encodeURIComponent(q)}`),
  get: (id: string) => request<DocumentDetail>(`/api/documents/${id}`),
  upload: (files: File[]) => {
    const body = new FormData()
    files.forEach((f) => body.append('files', f))
    return request<DocumentOut[]>('/api/documents', { method: 'POST', body })
  },
  remove: (id: string) =>
    request<void>(`/api/documents/${id}`, { method: 'DELETE' }),
  reprocess: (id: string) =>
    request<DocumentOut>(`/api/documents/${id}/reprocess`, { method: 'POST' }),
  onlyofficeConfig: (id: string, side: 'original' | 'result') =>
    request<Record<string, unknown>>(`/api/onlyoffice/${id}/${side}`),
}
