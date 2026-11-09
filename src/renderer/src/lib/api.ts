import { getBackendInfo } from './backend'
import { readSSE } from './sse'
import type {
  AISettings,
  AITestResult,
  Analysis,
  Annotation,
  AnnotationCreate,
  AskRequest,
  Bookmark,
  Chain,
  Conversation,
  ParaInsightResponse,
  GlossaryData,
  PageRect,
  InsightCard,
  Job,
  JobDetail,
  LocateMatch,
  Note,
  NoteCreate,
  Outline,
  PagePassages,
  PaperDetail,
  PaperProfile,
  LibraryStats,
  PaperTag,
  PaperList,
  PaperPatch,
  Passage,
  PrivacyPage,
  ReparseStatus,
  SearchHit,
  SystemInfo,
  TranslateRequest,
  UsageSummary
} from './types'

export class ApiError extends Error {
  readonly code: string
  readonly status: number
  constructor(code: string, message: string, status = 0) {
    super(message)
    this.name = 'ApiError'
    this.code = code
    this.status = status
  }
}

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
    const body = (await res.json()) as { error?: { code?: string; message?: string }; detail?: unknown }
    if (body.error) {
      code = body.error.code ?? code
      message = body.error.message ?? message
    } else if (typeof body.detail === 'string') {
      message = body.detail
    }
  } catch {
    /* 非 JSON 响应 */
  }
  if (res.status === 401) code = 'unauthorized'
  return new ApiError(code, message, res.status)
}

async function doFetch(path: string, init: RequestInit & { query?: Query } = {}): Promise<Response> {
  const { baseUrl, token } = getBackendInfo()
  const { query, headers, ...rest } = init
  let res: Response
  try {
    res = await fetch(`${baseUrl}${path}${qs(query)}`, {
      ...rest,
      headers: { Authorization: `Bearer ${token}`, ...(headers as Record<string, string> | undefined) }
    })
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
const put = <T>(path: string, body?: unknown): Promise<T> => request<T>('PUT', path, { body: body ?? {} })
const patch = <T>(path: string, body: unknown): Promise<T> => request<T>('PATCH', path, { body })
const del = <T>(path: string): Promise<T> => request<T>('DELETE', path)

/** 发起 SSE 请求，逐个事件回调；用 AbortSignal 取消。 */
async function postSSE(
  path: string,
  body: unknown,
  onEvent: (event: string, data: unknown) => void,
  signal?: AbortSignal
): Promise<void> {
  const res = await doFetch(path, {
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
      onEvent(msg.event, data)
    },
    signal
  )
}

export interface PaperListQuery {
  q?: string
  tag?: string
  view?: 'all' | 'recent' | 'favorite'
  sort?: 'recent' | 'title' | 'author' | 'year' | 'imported' | 'relevance'
  order?: 'asc' | 'desc'
  limit?: number
  offset?: number
  // 多特征筛选（由 features/library/filterModel.ts 的 filtersToQuery 生成）
  tags?: string
  tag_mode?: 'any' | 'all'
  folder?: string
  subfolders?: boolean
  note_q?: string
  annotation_q?: string
  text_q?: string
  card_q?: string
  year_from?: number
  year_to?: number
  author?: string
  venue?: string
  language?: string
  read?: string
  parse?: string
  has_notes?: boolean
  has_annotations?: boolean
  has_supplement?: boolean
  has_code?: boolean
  favorite?: boolean
  imported_from?: string
  imported_to?: string
}

export const api = {
  health: () => get<{ status: string; version: string }>('/api/health'),
  systemInfo: () => get<SystemInfo>('/api/system/info'),

  papers: {
    list: (q: PaperListQuery = {}, signal?: AbortSignal) => get<PaperList>('/api/papers', { ...q }, signal),
    get: (id: string) => get<PaperDetail>(`/api/papers/${id}`),
    /** 文献库的数量统计。 */
    stats: () => get<LibraryStats>('/api/papers/stats'),
    /** 详情页汇总：页数、图表公式数量、笔记标注数量、目录、关键词。 */
    profile: (id: string) => get<PaperProfile>(`/api/papers/${id}/profile`),
    /** 第一页缩略图（书架上的封面）。 */
    cover: async (id: string): Promise<Blob> => (await doFetch(`/api/papers/${id}/cover`)).blob(),
    patch: (id: string, body: PaperPatch) => patch<PaperDetail>(`/api/papers/${id}`, body),
    remove: (id: string) => del<{ ok: boolean; deleted_supplements?: number; warnings?: string[] }>(`/api/papers/${id}`),
    /** 标签即知识库主题：名字不存在时后端自动创建。 */
    addTag: (id: string, name: string) => post<PaperTag>(`/api/papers/${id}/tags`, { name }),
    removeTag: (id: string, tagId: string) => del<{ ok: boolean }>(`/api/papers/${id}/tags/${tagId}`),
    /** PDF 字节（交给 pdf.js）。 */
    file: async (id: string, signal?: AbortSignal): Promise<ArrayBuffer> => {
      const res = await doFetch(`/api/papers/${id}/file`, { signal })
      return res.arrayBuffer()
    },
    outline: (id: string) => get<Outline>(`/api/papers/${id}/outline`),
    pagePassages: (id: string, page: number) => get<PagePassages>(`/api/papers/${id}/passages`, { page }),
    passage: (id: string, ord: number) => get<Passage>(`/api/papers/${id}/passages/${ord}`),
    /** 这一段在整篇论证里的作用（按需分析，结果缓存；force 重新分析） */
    insight: (id: string, ord: number, force = false) => post<ParaInsightResponse>(`/api/papers/${id}/passages/${ord}/insight`, { force }),
    /** 术语表：读论文之前先扫清概念障碍（导入后在后台做好） */
    glossary: (id: string) => get<GlossaryData>(`/api/papers/${id}/glossary`),
    glossaryStart: (id: string) => post<{ job_id: string | null }>(`/api/papers/${id}/glossary/start`, {}),
    /** 这一段在 PDF 里真实占的位置（跨栏、跨页的续行也包含） */
    passageRects: (id: string, ord: number) => get<{ rects: PageRect[]; exact: boolean }>(`/api/papers/${id}/passages/${ord}/rects`),
    locate: (id: string, text: string, page?: number) =>
      post<{ matches: LocateMatch[] }>(`/api/papers/${id}/locate`, { text, page }),
    search: (id: string, q: string, limit = 50, signal?: AbortSignal) =>
      get<{ items: SearchHit[] }>(`/api/papers/${id}/search`, { q, limit }, signal),
    /** 只有题录的论文：自动找开放获取的全文并附加（后台任务） */
    fetchFulltext: (id: string) => post<{ job_id: string }>(`/api/papers/${id}/fetch_fulltext`, {}),
    attachPdf: (id: string, path: string) => post<{ job_id: string }>(`/api/papers/${id}/attach_pdf`, { path }),
    /** 重新解析（后台任务，可取消）：用当前版本的解析器重新读 PDF，笔记等用户数据会重新映射；force=true 在新解析段落数少得可疑时仍然替换。 */
    reparse: (id: string, force = false) => post<{ job_id: string }>(`/api/papers/${id}/reparse`, { force }),
    reparseStatus: (id: string) => get<ReparseStatus>(`/api/papers/${id}/reparse`),
    /** 撤销最近一次重新解析（保留 7 天）。 */
    undoReparse: (id: string) => post<{ ok: boolean; restored: Record<string, number> }>(`/api/papers/${id}/reparse/undo`)
  },

  bookmarks: {
    list: (paperId: string) => get<{ items: Bookmark[] }>(`/api/papers/${paperId}/bookmarks`),
    create: (paperId: string, page: number, label: string, at?: { passage_ord: number; quote: string }) =>
      post<Bookmark>(`/api/papers/${paperId}/bookmarks`, { page, label, ...at }),
    remove: (paperId: string, id: string) => del<{ ok: boolean }>(`/api/papers/${paperId}/bookmarks/${id}`)
  },

  notes: {
    list: (paperId: string) => get<{ items: Note[] }>(`/api/papers/${paperId}/notes`),
    create: (paperId: string, body: NoteCreate) => post<Note>(`/api/papers/${paperId}/notes`, body),
    update: (id: string, text: string) => patch<Note>(`/api/notes/${id}`, { text }),
    remove: (id: string) => del<{ ok: boolean }>(`/api/notes/${id}`)
  },

  annotations: {
    list: (paperId: string) => get<{ items: Annotation[] }>(`/api/papers/${paperId}/annotations`),
    create: (paperId: string, body: AnnotationCreate) => post<Annotation>(`/api/papers/${paperId}/annotations`, body),
    remove: (paperId: string, id: string) => del<{ ok: boolean }>(`/api/papers/${paperId}/annotations/${id}`)
  },

  imports: {
    files: (paths: string[]) => post<{ job_id: string }>('/api/import/files', { paths }),
    folder: (path: string, recursive = true) => post<{ job_id: string }>('/api/import/folder', { path, recursive }),
    doi: (dois: string[], metadataOnly = false) => post<{ job_id: string }>('/api/import/doi', { dois, metadata_only: metadataOnly }),
    url: (url: string, metadataOnly = false) => post<{ job_id: string }>('/api/import/url', { url, metadata_only: metadataOnly })
  },

  jobs: {
    list: (q: { status?: string; type?: string; limit?: number } = {}) => get<{ items: Job[] }>('/api/jobs', { ...q }),
    get: (id: string) => get<JobDetail>(`/api/jobs/${id}`),
    cancel: (id: string) => post<Job>(`/api/jobs/${id}/cancel`),
    retry: (id: string) => post<{ job_id: string }>(`/api/jobs/${id}/retry`),
    remove: (id: string) => del<{ ok: boolean }>(`/api/jobs/${id}`),
    clearFinished: () => del<{ deleted: number }>('/api/jobs')
  },

  settings: {
    getAI: () => get<AISettings>('/api/settings/ai'),
    putAI: (body: Partial<AISettings>) => put<AISettings>('/api/settings/ai', body),
    /** 仅 Web 调试模式使用；Electron 下 API Key 只经主进程 IPC 推送。 */
    putSecret: (apiKey: string) => put<unknown>('/api/settings/ai/secret', { api_key: apiKey }),
    deleteSecret: () => del<unknown>('/api/settings/ai/secret'),
    testAI: () => post<AITestResult>('/api/settings/ai/test')
  },

  usage: (month?: string) => get<UsageSummary>('/api/usage', { month }),
  privacyRequests: (q: { limit?: number; before_id?: number; task_type?: string } = {}) => get<PrivacyPage>('/api/privacy/requests', { limit: 50, ...q }),

  ai: {
    /** 翻译过的文字直接取回译文（不调用模型）；没翻译过是 null */
    lookupTranslation: (paperId: string, text: string) => post<{ translation: string | null }>('/api/ai/translate/lookup', { paper_id: paperId, text }),
    translate: (body: TranslateRequest, onEvent: (event: string, data: unknown) => void, signal?: AbortSignal) =>
      postSSE('/api/ai/translate', body, onEvent, signal),
    ask: (body: AskRequest, onEvent: (event: string, data: unknown) => void, signal?: AbortSignal) =>
      postSSE('/api/ai/ask', body, onEvent, signal)
  },

  cards: {
    list: (paperId: string, q: { chain_id?: string; saved?: boolean; conversation_id?: string } = {}) =>
      get<{ items: InsightCard[] }>(`/api/papers/${paperId}/cards`, { ...q }),
    patch: (id: string, body: { saved?: boolean; chain_id?: string }) => patch<InsightCard>(`/api/cards/${id}`, body),
    remove: (id: string) => del<{ ok: boolean }>(`/api/cards/${id}`)
  },

  conversations: {
    list: (paperId: string) => get<{ items: Conversation[] }>(`/api/papers/${paperId}/conversations`),
    create: (paperId: string) => post<Conversation>(`/api/papers/${paperId}/conversations`, {}),
    rename: (id: string, title: string) => patch<Conversation>(`/api/conversations/${id}`, { title }),
    remove: (id: string) => del<{ ok: boolean }>(`/api/conversations/${id}`)
  },

  chains: {
    list: (paperId: string) => get<{ items: Chain[] }>(`/api/papers/${paperId}/chains`),
    rename: (id: string, title: string) => patch<Chain>(`/api/chains/${id}`, { title }),
    remove: (id: string) => del<{ ok: boolean }>(`/api/chains/${id}`)
  },

  analysis: {
    get: (paperId: string) => get<Analysis>(`/api/papers/${paperId}/analysis`),
    run: (paperId: string) => post<{ job_id: string }>(`/api/papers/${paperId}/analysis/run`)
  }
}

export function errorMessage(err: unknown): string {
  if (err instanceof ApiError) return err.message
  if (err instanceof Error) return err.message
  return String(err)
}

/** 供功能模块的 API 客户端（lib/api.<模块>.ts）复用的底层请求函数。 */
export { request as apiRequest, doFetch as apiFetch, postSSE as apiPostSSE }
