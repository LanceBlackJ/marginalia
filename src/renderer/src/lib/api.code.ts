/** 代码理解模块的 API 客户端（契约见 docs/API-code.md）。 */
import { ApiError } from './api'
import { getBackendInfo } from './backend'
import { readSSE } from './sse'
import type {
  CodeCard,
  CodeFileEntry,
  CodeRepo,
  CodeSource,
  CodeSymbol,
  ConsistencyState,
  ExplainMeta,
  ExplainTarget,
  FetchResult,
  FileData,
  Flow,
  PaperCodeLink,
  PaperModule,
  RepoInfo,
  SymbolGraph,
  SymbolPaper
} from './types.code'
import type { Job } from './types'

type Query = Record<string, string | number | boolean | null | undefined>

function qs(query?: Query): string {
  if (!query) return ''
  const p = new URLSearchParams()
  for (const [k, v] of Object.entries(query)) {
    if (v === undefined || v === null || v === '') continue
    p.set(k, String(v))
  }
  const s = p.toString()
  return s ? `?${s}` : ''
}

async function toApiError(res: Response): Promise<ApiError> {
  let code = 'internal'
  let message = `请求失败，HTTP ${res.status}`
  try {
    const body = (await res.json()) as { error?: { code?: string; message?: string } }
    if (body.error) {
      code = body.error.code ?? code
      message = body.error.message ?? message
    }
  } catch {
    /* 非 JSON 响应 */
  }
  return new ApiError(code, message, res.status)
}

async function doFetch(path: string, init: RequestInit & { query?: Query } = {}): Promise<Response> {
  const { baseUrl, token } = getBackendInfo()
  const { query, headers, ...rest } = init
  let res: Response
  try {
    res = await fetch(`${baseUrl}${path}${qs(query)}`, { ...rest, headers: { Authorization: `Bearer ${token}`, ...(headers as Record<string, string> | undefined) } })
  } catch (err) {
    if (err instanceof DOMException && err.name === 'AbortError') throw err
    throw new ApiError('network', '无法连接到本地服务，请检查后端是否仍在运行。')
  }
  if (!res.ok) throw await toApiError(res)
  return res
}

async function request<T>(method: string, path: string, opts: { query?: Query; body?: unknown; signal?: AbortSignal } = {}): Promise<T> {
  const res = await doFetch(path, {
    method,
    query: opts.query,
    signal: opts.signal,
    headers: opts.body !== undefined ? { 'Content-Type': 'application/json' } : undefined,
    body: opts.body !== undefined ? JSON.stringify(opts.body) : undefined
  })
  return (await res.json()) as T
}

const get = <T>(path: string, query?: Query, signal?: AbortSignal): Promise<T> => request<T>('GET', path, { query, signal })
const post = <T>(path: string, body?: unknown): Promise<T> => request<T>('POST', path, { body: body ?? {} })
const patch = <T>(path: string, body: unknown): Promise<T> => request<T>('PATCH', path, { body })
const del = <T>(path: string): Promise<T> => request<T>('DELETE', path)

export type ExplainEvent =
  | { event: 'meta'; data: ExplainMeta }
  | { event: 'delta'; data: { text: string } }
  | { event: 'escalate'; data: { reason: string; model: string } }
  | { event: 'done'; data: { card: CodeCard } }
  | { event: 'error'; data: { code: string; message: string; card_id?: string } }

export const codeApi = {
  sources: (paperId: string) => get<{ items: CodeSource[] }>(`/api/papers/${paperId}/code/sources`),
  resolve: (paperId: string, url: string, branch?: string) => post<RepoInfo>(`/api/papers/${paperId}/code/resolve`, { url, branch }),
  fetch: (paperId: string, url: string, branch?: string) => post<FetchResult>(`/api/papers/${paperId}/code/fetch`, { url, branch }),
  importLocal: (paperId: string, path: string, kind: 'zip' | 'folder' | 'auto' = 'auto', authoredBy: 'author' | 'user' | 'self' = 'user') =>
    post<{ repo_id: string; job_id: string }>(`/api/papers/${paperId}/code/import`, { path, kind, authored_by: authoredBy }),
  repos: (paperId: string) => get<{ items: CodeRepo[] }>(`/api/papers/${paperId}/code/repos`),
  repo: (repoId: string) => get<CodeRepo>(`/api/code/repos/${repoId}`),
  removeRepo: (repoId: string) => del<{ ok: boolean }>(`/api/code/repos/${repoId}`),
  reanalyze: (repoId: string) => post<{ job_id: string }>(`/api/code/repos/${repoId}/reanalyze`),
  tree: (repoId: string) => get<{ files: CodeFileEntry[] }>(`/api/code/repos/${repoId}/tree`),
  file: (repoId: string, path: string) => get<FileData>(`/api/code/repos/${repoId}/file`, { path }),
  symbols: (repoId: string, q?: string, kind?: string, limit = 100) =>
    get<{ items: CodeSymbol[] }>(`/api/code/repos/${repoId}/symbols`, { q, kind, limit }),
  graph: (repoId: string, symbolId: number) => get<SymbolGraph>(`/api/code/repos/${repoId}/symbols/${symbolId}/graph`),
  flow: (repoId: string, symbolId?: number | null, maxDepth = 4) => get<Flow>(`/api/code/repos/${repoId}/flow`, { symbol_id: symbolId ?? undefined, max_depth: maxDepth }),
  annotateFlow: (repoId: string, symbolId?: number | null) => post<Flow>(`/api/code/repos/${repoId}/flow/annotate`, { symbol_id: symbolId ?? null }),
  cards: (repoId: string) => get<{ items: CodeCard[] }>(`/api/code/repos/${repoId}/cards`),
  removeCard: (cardId: string) => del<{ ok: boolean }>(`/api/code/cards/${cardId}`),

  modules: (paperId: string) => get<{ items: PaperModule[] }>(`/api/papers/${paperId}/code/modules`),
  /** ai=false：只做本地词法匹配，不调用模型、不发送任何内容。 */
  associate: (repoId: string, ai = true) => post<{ job_id: string }>(`/api/code/repos/${repoId}/associate`, { ai }),
  links: (repoId: string) => get<{ items: PaperCodeLink[] }>(`/api/code/repos/${repoId}/links`),
  setLink: (linkId: string, status: PaperCodeLink['status']) => patch<PaperCodeLink>(`/api/code/links/${linkId}`, { status }),
  addLink: (repoId: string, moduleName: string, symbolId: number, passageOrds?: number[]) =>
    post<PaperCodeLink>(`/api/code/repos/${repoId}/links`, { module_name: moduleName, symbol_id: symbolId, passage_ords: passageOrds }),
  symbolPaper: (symbolId: number) => get<SymbolPaper>(`/api/code/symbols/${symbolId}/paper`),
  runConsistency: (repoId: string) => post<{ job_id: string }>(`/api/code/repos/${repoId}/consistency`),
  consistency: (repoId: string) => get<ConsistencyState>(`/api/code/repos/${repoId}/consistency`),

  job: (jobId: string) => get<Job>(`/api/jobs/${jobId}`),
  approveJob: (jobId: string) => post<Job>(`/api/jobs/${jobId}/approve`),
  rejectJob: (jobId: string) => post<Job>(`/api/jobs/${jobId}/reject`),
  cancelJob: (jobId: string) => post<Job>(`/api/jobs/${jobId}/cancel`),

  /** SSE 流式解释。用 AbortSignal 取消。 */
  async explain(
    body: { repo_id: string; question?: string } & ExplainTarget,
    onEvent: (e: ExplainEvent) => void,
    signal?: AbortSignal
  ): Promise<void> {
    const res = await doFetch('/api/code/explain', {
      method: 'POST',
      signal,
      headers: { 'Content-Type': 'application/json', Accept: 'text/event-stream' },
      body: JSON.stringify(body)
    })
    if (!res.body) throw new ApiError('network', '服务没有返回数据流。')
    await readSSE(
      res.body,
      (msg) => {
        let data: unknown = msg.data
        try {
          data = JSON.parse(msg.data)
        } catch {
          /* 保留原始字符串 */
        }
        onEvent({ event: msg.event, data } as ExplainEvent)
      },
      signal
    )
  }
}
