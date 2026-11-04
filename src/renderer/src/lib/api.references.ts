/** 参考文献 API 客户端。 */
import { apiPostSSE, apiRequest } from './api'
import type { Citations, RefContext, RefResolution, References } from './types.references'

const get = <T>(path: string): Promise<T> => apiRequest<T>('GET', path, {})

export const referencesApi = {
  list: (paperId: string) => get<References>(`/api/papers/${paperId}/references`),
  citations: (paperId: string) => get<Citations>(`/api/papers/${paperId}/citations`),
  contexts: (paperId: string, index: number) => get<{ items: RefContext[] }>(`/api/papers/${paperId}/references/${index}/contexts`),
  /** 联网查题录与摘要（只发这条参考文献的题录，不发论文正文）；结果在后端缓存，force 才重新联网。 */
  resolve: (paperId: string, index: number, force = false) =>
    apiRequest<RefResolution>('POST', `/api/papers/${paperId}/references/${index}/resolve`, { body: { force } }),
  /** AI 概括：依据摘要和本文引用句；没有摘要时 done 事件里 summary 为 null。 */
  summarize: (paperId: string, index: number, onEvent: (event: string, data: unknown) => void, signal?: AbortSignal) =>
    apiPostSSE('/api/ai/summarize-reference', { paper_id: paperId, index }, onEvent, signal),
  /** 加入文库：只保存元数据，不下载 PDF。 */
  importMetadata: (doi: string) => apiRequest<{ job_id: string }>('POST', '/api/import/doi', { body: { dois: [doi], metadata_only: true } })
}
