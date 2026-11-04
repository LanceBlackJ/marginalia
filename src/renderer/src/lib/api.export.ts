/** 导出（引文、阅读成果合集）与知识库关系图/批量操作的 API 客户端。 */
import { apiFetch, apiRequest } from './api'

export type CitationFormat = 'bibtex' | 'ris' | 'csl' | 'gbt7714' | 'apa' | 'mla' | 'ieee'

export const CITATION_FORMATS: Array<{ value: CitationFormat; label: string; hint: string }> = [
  { value: 'bibtex', label: 'BibTeX', hint: '.bib · LaTeX / Zotero' },
  { value: 'ris', label: 'RIS', hint: '.ris · EndNote / Zotero / Mendeley' },
  { value: 'csl', label: 'CSL JSON', hint: '.json · Zotero / Pandoc' },
  { value: 'gbt7714', label: 'GB/T 7714', hint: '.txt · 顺序编码制参考文献' },
  { value: 'apa', label: 'APA 第 7 版', hint: '.txt · 作者-年份制参考文献' },
  { value: 'mla', label: 'MLA 第 9 版', hint: '.txt · 人文社科常用' },
  { value: 'ieee', label: 'IEEE', hint: '.txt · 工程与计算机类期刊会议' }
]

export interface CitationExport {
  filename: string
  content: string
  count: number
  format: CitationFormat
  label: string
  mime: string
}

export interface BundleOptions {
  paper_ids?: string[]
  notes?: boolean
  annotations?: boolean
  cards?: boolean
  items?: boolean
}

export interface BundleMarkdown {
  filename: string
  content: string
  count: number
  counts: { notes: number; annotations: number; cards: number; items: number }
}

export type GraphNodeType = 'item' | 'topic' | 'paper'
export interface GraphNode {
  id: string
  type: GraphNodeType
  label: string
  kind: string | null
  ref: string
  year?: number | null
}
export interface GraphEdge {
  id: string
  source: string
  target: string
  type: 'relation' | 'member' | 'source'
  label: string
  status: 'confirmed' | 'suggested'
  ai_inferred?: boolean
  ref?: string
  description?: string
}
export interface KbGraph {
  nodes: GraphNode[]
  edges: GraphEdge[]
  counts: { items: number; relations: number; suggested: number }
}

const get = <T>(path: string, query?: Record<string, string | number | boolean | undefined>): Promise<T> => apiRequest<T>('GET', path, { query })
const post = <T>(path: string, body: unknown): Promise<T> => apiRequest<T>('POST', path, { body })

export const exportApi2 = {
  citations: (body: { format: CitationFormat; paper_ids?: string[]; tag_id?: string }) => post<CitationExport>('/api/export/citations', body),
  bundleMarkdown: (body: BundleOptions) => post<BundleMarkdown>('/api/export/bundle', { ...body, format: 'md' }),
  /** Word 文件：后端直接返回文件字节。 */
  bundleDocx: async (body: BundleOptions): Promise<Blob> =>
    (
      await apiFetch('/api/export/bundle', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ ...body, format: 'docx' })
      })
    ).blob()
}

export const kbGraphApi = {
  graph: (q: { topic_id?: string; kinds?: string[] } = {}) => get<KbGraph>('/api/kb/graph', { topic_id: q.topic_id, kinds: q.kinds?.join(',') || undefined }),
  bulkItems: (body: { item_ids: string[]; action: 'add_topic' | 'remove_topic' | 'delete'; topic_id?: string }) =>
    post<{ affected: number; skipped: number }>('/api/kb/items/bulk', body),
  mergeTopics: (body: { source_ids: string[]; target_id: string }) =>
    post<{ merged: number; moved_items: number; moved_papers: number }>('/api/kb/topics/merge', body)
}
