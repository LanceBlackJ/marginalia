/** 参考文献：解析、正文引用标记、联网查到的题录与摘要（契约见 docs/API-references.md）。 */

export interface RefItem {
  index: number
  /** 这条参考文献在论文段落里的序号（可跳转） */
  ord: number
  page: number
  ords: number[]
  raw: string
  authors: string[]
  title: string
  year: number | null
  venue: string
  doi: string
  arxiv: string
  url: string
  /** 双栏被合并/一条被拆成几段：文字可能夹杂别的条目的片段 */
  merged: boolean
  /** 抽出的字段不可靠 */
  messy: boolean
}

export interface References {
  style: 'numeric' | 'author-year' | 'unknown'
  count: number
  items: RefItem[]
}

export type MarkerKind = 'sup' | 'bracket' | 'ref' | 'authoryear'

export interface CitationMarker {
  kind: MarkerKind
  /** 标记在段落原文里的文字（如 “[12]”“1,2”“Smith et al. (2020)”） */
  text: string
  start: number
  end: number
  refs: number[]
}

export interface Citations {
  style: string
  ref_count: number
  items: Array<{ ord: number; markers: CitationMarker[] }>
}

export interface RefMatch {
  score: number
  level: 'high' | 'medium' | 'low'
  reason: string
}

export interface RefResolution {
  index: number
  found: boolean
  reason?: string
  title?: string
  authors?: string[]
  year?: number | null
  venue?: string
  doi?: string
  url?: string
  open_access_url?: string
  /** 通过了与标题一致性检查的摘要；没有、或检索到的摘要与标题不相符而被忽略时为 null */
  abstract?: string | null
  abstract_source?: string
  abstract_reason?: string
  /** 摘要被替换/可疑时的说明（来源与原因），界面显示成一行小字 */
  abstract_notes?: string[]
  /** 检索到的摘要与这篇的标题不相符，已被忽略 */
  abstract_mismatch?: boolean
  citations?: number | null
  match?: RefMatch
  source?: string
  network?: string
  cached?: boolean
  in_library?: { paper_id: string; title: string } | null
}

export interface RefContext {
  ord: number
  page: number
  sentence: string
}

export interface SummarizeDone {
  summary: string | null
  reason?: 'no_abstract' | 'not_found'
  message?: string
  link?: string
  model: string | null
  match: RefMatch | null
  notes?: string[]
  label?: string
  basis?: { abstract_source?: string; contexts: Array<{ ord: number; page: number }> }
}
