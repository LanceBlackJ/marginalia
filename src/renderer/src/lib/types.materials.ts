/** 补充材料与数据集（F1）。对应后端 docs/API-materials.md。 */
import type { Evidence } from './types'

export const SUPPLEMENT_CATEGORIES = ['方法补充', '公式推导', '实验配置', '额外实验', '数据集', '代码', '其他'] as const
export type SupplementCategory = (typeof SUPPLEMENT_CATEGORIES)[number]

export type SupplementKind = 'pdf' | 'table' | 'document' | 'archive' | 'code' | 'other'

export type DownloadStatus = 'pending' | 'waiting_approval' | 'downloading' | 'processing' | 'done' | 'failed' | 'rejected'

export interface AssetAnalysisBrief {
  kind: 'table' | 'archive' | 'document' | 'error' | string
  sheets?: number
  rows?: number
  columns?: number
  partial?: boolean
  has_explanation?: boolean
  total_files?: number
  suspicious?: number
  paragraphs?: number
  headings?: number
  error?: string | null
}

export interface SupplementAsset {
  id: string
  paper_id: string
  filename: string
  mime: string | null
  size: number | null
  kind: SupplementKind
  source: 'local' | 'url' | 'auto'
  source_url: string | null
  category: SupplementCategory
  category_source: 'rule' | 'ai' | 'user'
  summary: string | null
  match_status: '用户确认' | '待确认'
  match_basis: string | null
  download_status: DownloadStatus
  download_error: string | null
  analysis_status: 'none' | 'pending' | 'running' | 'ready' | 'failed'
  analysis: AssetAnalysisBrief | null
  child_paper_id: string | null
  child: { page_count: number | null; status: string; title: string } | null
  job_id: string | null
  created_at: string
}

export interface SupplementAuthorization {
  granted: boolean
  /** null = 本次打开尚未询问 */
  decision: 'allow' | 'deny' | null
  decided_at: string | null
  ttl_seconds: number
}

export interface SupplementList {
  items: SupplementAsset[]
  authorization: SupplementAuthorization
  categories: SupplementCategory[]
}

export interface DiscoverCandidate {
  id: string
  url: string
  filename: string
  size: number | null
  mime: string
  source: 'publisher' | 'crossref' | 'arxiv' | 'code'
  basis: string
  kind: SupplementKind
  category_guess: SupplementCategory
  label: string
  already_linked?: boolean
}

export interface DiscoverResult {
  candidates: DiscoverCandidate[]
  notes: string[]
}

export interface RecommendItem {
  asset_id: string
  order: number
  reason: string
}

export interface Recommendation {
  items: RecommendItem[]
  source: 'ai' | 'rules'
  note?: string
  model?: string
}

/** SSE 事件 needs_supplement_permission：回答可能需要补充材料，但用户还没有授权。 */
export interface NeedsSupplementPermission {
  card_id: string
  labels: string[]
  reason: string
  supplements: Array<{ asset_id: string; filename: string; category: SupplementCategory; size: number | null; paper_id: string }>
}

// ---------- 数据集检查 ----------

export interface ColumnStats {
  min?: number | string | null
  max?: number | string | null
  mean?: number | null
  std?: number | null
  median?: number | null
  q25?: number | null
  q75?: number | null
}

export interface HistogramBin {
  start: number
  end: number
  count: number
}

export interface DatasetColumn {
  name: string
  dtype: 'integer' | 'float' | 'string' | 'boolean' | 'datetime' | 'mixed'
  non_null: number
  missing: number
  missing_rate: number
  unique: number
  unique_exact: boolean
  stats: ColumnStats | null
  histogram: { bins: HistogramBin[]; sampled: boolean } | null
  top_values: Array<{ value: string; count: number }> | null
}

export interface DatasetSheet {
  name: string
  rows: number
  rows_scanned: number
  partial: boolean
  rows_approx?: boolean
  columns: DatasetColumn[]
  preview: { columns: string[]; rows: Array<Array<string | number | boolean | null>> }
}

export interface DatasetExplanation {
  markdown: string
  answer: {
    direct: string
    detail: string | null
    evidence_notes: Array<{ ord: number; note: string; ref?: string }>
    inferences: Array<{ text: string; basis_ords: number[]; basis_refs?: string[] }>
    follow_ups: string[]
  }
  evidence: Evidence[]
  verification: { status: string; invalid_citations: number[]; invalid_refs?: string[]; notes: string[] }
  model: string
  at: string
}

export interface TableAnalysis {
  kind: 'table'
  format: string
  encoding?: string
  delimiter?: string
  size: number
  sheets: DatasetSheet[]
  sheet_names?: string[]
  explanation?: DatasetExplanation
}

export interface ArchiveAnalysis {
  kind: 'archive'
  total_files: number
  total_uncompressed: number
  ratio: number
  suspicious: string[]
  entries: Array<{ name: string; size: number; is_dir: boolean }>
  truncated: boolean
  extensions: Record<string, number>
  extracted?: { dir: string; files: number }
}

export interface DocumentAnalysis {
  kind: 'document'
  format: string
  paragraphs: number
  tables: number
  headings: string[]
  chars: number
  text_sample: string
}

export type AssetAnalysis = TableAnalysis | ArchiveAnalysis | DocumentAnalysis | { kind: 'error'; error: string } | null

export interface TablePage {
  sheet: string
  columns: string[]
  offset: number
  limit: number
  total_rows: number | null
  rows: Array<Array<string | number | boolean | null>>
}
