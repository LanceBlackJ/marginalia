/** 补充材料与数据集 API 客户端（F1）。 */
import { apiFetch, apiRequest } from './api'
import type {
  AssetAnalysis,
  DiscoverCandidate,
  Recommendation,
  SupplementAsset,
  SupplementAuthorization,
  SupplementList,
  TablePage
} from './types.materials'

const get = <T>(path: string, query?: Record<string, string | number | undefined>, signal?: AbortSignal): Promise<T> =>
  apiRequest<T>('GET', path, { query, signal })
const post = <T>(path: string, body?: unknown): Promise<T> => apiRequest<T>('POST', path, { body: body ?? {} })
const patch = <T>(path: string, body: unknown): Promise<T> => apiRequest<T>('PATCH', path, { body })
const del = <T>(path: string): Promise<T> => apiRequest<T>('DELETE', path)

export const materialsApi = {
  list: (paperId: string) => get<SupplementList>(`/api/papers/${paperId}/supplements`),

  authorization: {
    get: (paperId: string) => get<SupplementAuthorization>(`/api/papers/${paperId}/supplements/authorization`),
    /** allow=null 表示重置（视为本次打开尚未询问） */
    set: (paperId: string, allow: boolean | null) =>
      post<SupplementAuthorization>(`/api/papers/${paperId}/supplements/authorization`, { allow })
  },

  addFiles: (paperId: string, paths: string[], category?: string) =>
    post<{ job_id: string; asset_ids: string[] }>(`/api/papers/${paperId}/supplements/files`, { paths, category }),
  addUrl: (paperId: string, url: string, category?: string) =>
    post<{ items: SupplementAsset[]; skipped_existing: number }>(`/api/papers/${paperId}/supplements/url`, { url, category }),
  discover: (paperId: string) => post<{ job_id: string }>(`/api/papers/${paperId}/supplements/discover`),
  linkRemote: (paperId: string, items: DiscoverCandidate[]) =>
    post<{ items: SupplementAsset[]; skipped_existing: number }>(`/api/papers/${paperId}/supplements/link-remote`, {
      items: items.map((c) => ({ url: c.url, filename: c.filename, size: c.size, source: c.source, basis: c.basis, category: c.category_guess }))
    }),
  recommend: (paperId: string, question: string) =>
    post<Recommendation>(`/api/papers/${paperId}/supplements/recommend`, { question }),
  attachPaper: (paperId: string, otherPaperId: string) =>
    post<SupplementAsset>(`/api/papers/${paperId}/supplements/attach-paper`, { paper_id: otherPaperId }),

  patch: (id: string, body: { category?: string; filename?: string; summary?: string; match_status?: string }) =>
    patch<SupplementAsset>(`/api/supplements/${id}`, body),
  remove: (id: string) => del<{ ok: boolean }>(`/api/supplements/${id}`),
  approve: (id: string) => post<SupplementAsset>(`/api/supplements/${id}/approve`),
  reject: (id: string) => post<SupplementAsset>(`/api/supplements/${id}/reject`),
  retry: (id: string) => post<SupplementAsset>(`/api/supplements/${id}/retry`),
  analyze: (id: string) => post<{ job_id: string }>(`/api/supplements/${id}/analyze`),
  explain: (id: string) => post<{ job_id: string }>(`/api/supplements/${id}/explain`),
  extract: (id: string) => post<{ job_id: string }>(`/api/supplements/${id}/extract`),
  analysis: (id: string) => get<{ asset: SupplementAsset; analysis: AssetAnalysis }>(`/api/supplements/${id}/analysis`),
  table: (id: string, q: { sheet?: string; offset?: number; limit?: number }) =>
    get<TablePage>(`/api/supplements/${id}/table`, { sheet: q.sheet, offset: q.offset, limit: q.limit }),

  /** 取回原文件（Word/Excel 等无法在应用内打开的材料，交给系统保存）。 */
  fileBlob: async (id: string): Promise<Blob> => {
    const res = await apiFetch(`/api/supplements/${id}/file`)
    return res.blob()
  }
}

export const jobsApprovalApi = {
  approve: (jobId: string) => post<unknown>(`/api/jobs/${jobId}/approve`),
  reject: (jobId: string) => post<unknown>(`/api/jobs/${jobId}/reject`)
}
