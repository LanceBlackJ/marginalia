/** 文库的文件夹、多特征筛选、智能视图与建议归类（契约见 docs/API.md「文件夹与筛选」）。 */

export type ReadState = 'unread' | 'reading' | 'done'
export type ParseState = 'ready' | 'parsing' | 'failed' | 'metadata_only' | 'ocr'

/** 筛选条件：全部可选，彼此 AND。 */
export interface PaperFilters {
  /** 笔记内容（含笔记引用的原文） */
  note_q?: string
  /** 批注与高亮文字 */
  annotation_q?: string
  /** 论文正文 */
  text_q?: string
  /** AI 研究卡片（AI 生成的内容） */
  card_q?: string
  tags?: string[]
  tag_mode?: 'any' | 'all'
  /** 文件夹 id；'none' = 未分类 */
  folder?: string
  year_from?: number
  year_to?: number
  author?: string
  venue?: string
  language?: string
  read?: ReadState
  parse?: ParseState
  has_notes?: boolean
  has_annotations?: boolean
  has_supplement?: boolean
  has_code?: boolean
  favorite?: boolean
  /** yyyy-mm-dd（含当天） */
  imported_from?: string
  imported_to?: string
}

export interface FolderItem {
  id: string
  name: string
  parent_id: string | null
  sort: number
  /** 直接放在这个文件夹里的论文数 */
  paper_count: number
  /** 含所有子文件夹 */
  total_count: number
}

export interface FolderList {
  items: FolderItem[]
  counts: { all: number; unfiled: number; favorite: number; recent: number }
}

export interface FolderNode extends FolderItem {
  children: FolderNode[]
  depth: number
}

export interface SmartView {
  id: string
  name: string
  query: PaperFilters
  sort: number
}

/** 命中片段：hit 是 text 里的 [start, end) 下标，界面据此高亮。 */
export interface MatchSnippet {
  id?: number | string
  ord?: number
  page: number | null
  from: string
  text: string
  hit: [number, number]
}

export interface PaperMatches {
  notes?: MatchSnippet[]
  annotations?: MatchSnippet[]
  fulltext?: MatchSnippet[]
  cards?: MatchSnippet[]
}

export interface FolderSuggestion {
  paper_id: string
  paper_title: string
  folder_id: string
  folder_name: string
  folder_path: string
  score: number
  confidence: 'high' | 'medium' | 'low'
  basis: string
}

export interface FolderSuggestions {
  method: 'embedding' | 'keywords' | 'none'
  items: FolderSuggestion[]
  reason: string | null
}

export interface PaperFacets {
  total: number
  years: Array<{ year: number; count: number }>
  authors: Array<{ author: string; count: number }>
  venues: Array<{ venue: string; count: number }>
  tags: Array<{ id: string; name: string; count: number }>
  languages: Array<{ language: string; count: number }>
  parse: Record<string, number>
  read: { unread: number; reading: number; done: number }
  flags: { has_notes: number; has_annotations: number; has_code: number; has_supplement: number; favorite: number }
}
