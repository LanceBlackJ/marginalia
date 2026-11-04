/** AI 思维导图与主动研究提示 API 客户端（F4）。 */
import { apiFetch, apiPostSSE, apiRequest } from './api'
import type {
  Finding,
  FindingList,
  FindingUserState,
  MindNodeDetail,
  MindNodePatch,
  MindOverview,
  MindSearchHit,
  MindTree,
  MindView
} from './types.mindmap'

type Query = Record<string, string | number | boolean | undefined>
const get = <T>(path: string, query?: Query, signal?: AbortSignal): Promise<T> => apiRequest<T>('GET', path, { query, signal })
const post = <T>(path: string, body?: unknown): Promise<T> => apiRequest<T>('POST', path, { body: body ?? {} })
const put = <T>(path: string, body: unknown): Promise<T> => apiRequest<T>('PUT', path, { body })
const patch = <T>(path: string, body: unknown): Promise<T> => apiRequest<T>('PATCH', path, { body })
const del = <T>(path: string, query?: Query): Promise<T> => apiRequest<T>('DELETE', path, { query })

export type ExportFormat = 'markdown' | 'json'

/** 删除节点时后端返回的完整子树数据：原样传回 restore 即可恢复（同样的 id、内容与顺序）。 */
export interface NodeSnapshot {
  parent_id: string
  index: number
  rows: Array<Record<string, unknown>>
}

export const mindmapApi = {
  overview: (paperId: string) => get<MindOverview>(`/api/papers/${paperId}/mindmap/overview`),
  get: (paperId: string, view: MindView, signal?: AbortSignal) => get<MindTree>(`/api/papers/${paperId}/mindmap`, { view }, signal),
  generate: (paperId: string, view: MindView | 'all', branchId?: string) =>
    post<{ job_id: string; jobs: Record<string, string> }>(`/api/papers/${paperId}/mindmap/generate`, { view, branch_id: branchId }),
  search: (paperId: string, q: string, view?: MindView) =>
    get<{ items: MindSearchHit[] }>(`/api/papers/${paperId}/mindmap/search`, { q, view }),
  savePositions: (paperId: string, view: MindView, items: Array<{ id: string; x: number; y: number }>) =>
    put<{ updated: number }>(`/api/papers/${paperId}/mindmap/positions`, { view, items }),
  resetPositions: (paperId: string, view: MindView) => del<{ updated: number }>(`/api/papers/${paperId}/mindmap/positions`, { view }),
  collapseAll: (paperId: string, view: MindView, collapsed: boolean, minDepth = 2) =>
    post<{ updated: number }>(`/api/papers/${paperId}/mindmap/collapse`, { view, collapsed, min_depth: minDepth }),
  /** 导出为文件内容（Blob）与建议文件名。 */
  export: async (paperId: string, view: MindView, format: ExportFormat): Promise<{ blob: Blob; filename: string }> => {
    const res = await apiFetch(`/api/papers/${paperId}/mindmap/export`, { query: { view, format } })
    const cd = res.headers.get('content-disposition') ?? ''
    const m = /filename\*=UTF-8''([^;]+)/i.exec(cd)
    return { blob: await res.blob(), filename: m ? decodeURIComponent(m[1]) : `mindmap-${view}.${format === 'json' ? 'json' : 'md'}` }
  },

  node: {
    get: (id: string) => get<MindNodeDetail>(`/api/mindmap/nodes/${id}`),
    patch: (id: string, body: MindNodePatch) => patch<MindNodeDetail>(`/api/mindmap/nodes/${id}`, body),
    addChild: (id: string, body: { title: string; summary?: string; kind?: string; index?: number }) =>
      post<MindNodeDetail>(`/api/mindmap/nodes/${id}/children`, body),
    addSibling: (id: string, body: { title: string; summary?: string; kind?: string }) =>
      post<MindNodeDetail>(`/api/mindmap/nodes/${id}/siblings`, body),
    /** 删除节点及其子树；返回的 snapshot 可交给 restore 原样恢复（撤销用）。 */
    remove: (id: string) => del<{ ok: boolean; removed: number; parent_id: string; snapshot: NodeSnapshot }>(`/api/mindmap/nodes/${id}`),
    restore: (snapshot: NodeSnapshot) => post<{ ok: boolean; restored: number; id: string }>('/api/mindmap/nodes/restore', { snapshot }),
    explain: (id: string, question: string | undefined, onEvent: (event: string, data: unknown) => void, signal?: AbortSignal) =>
      apiPostSSE(`/api/mindmap/nodes/${id}/explain`, { question }, onEvent, signal)
  }
}

export const findingsApi = {
  list: (paperId: string, includeIgnored = false) =>
    get<FindingList>(`/api/papers/${paperId}/findings`, { include_ignored: includeIgnored || undefined }),
  scan: (paperId: string) => post<{ job_id: string }>(`/api/papers/${paperId}/findings`),
  patch: (paperId: string, id: string, body: { user_state?: FindingUserState; verified?: boolean }) =>
    patch<Finding>(`/api/papers/${paperId}/findings/${id}`, body),
  remove: (paperId: string, id: string) => del<{ ok: boolean }>(`/api/papers/${paperId}/findings/${id}`)
}
