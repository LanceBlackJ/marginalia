/** 参考文献 API 客户端。 */
import { apiPostSSE, apiRequest } from './api'
import type { Citations, RefContext, RefResolution, References } from './types.references'

export type AddTarget =
  | { via: 'doi'; doi: string }
  | { via: 'arxiv'; arxiv: string }
  | { via: 'library'; paper_id: string; title: string }
  | { via: 'metadata'; paper_id: string; title: string }

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
  /** 加入文库该走哪条路（后端挑标识符；可信度低的匹配不用）：doi / arxiv 走标准导入，library 已在文库，metadata 已用参考文献本身的题录建好。 */
  addTarget: (paperId: string, index: number) =>
    apiRequest<AddTarget>('POST', `/api/papers/${paperId}/references/${index}/add`, { body: {} })
}
