/** AI 思维导图与主动研究提示的类型（F4）。与后端 docs/API-mindmap.md 一一对应。 */
import type { BBox, Evidence, InsightCard } from './types'

export type MindView = 'logic' | 'method' | 'experiment'
export const MIND_VIEWS: MindView[] = ['logic', 'method', 'experiment']
export const MIND_VIEW_LABELS: Record<MindView, string> = { logic: '逻辑', method: '方法', experiment: '实验' }
export const MIND_VIEW_HINTS: Record<MindView, string> = {
  logic: '问题 → 动机 → 思想 → 方法 → 实验 → 结论',
  method: '框架 → 模块 → 公式与步骤',
  experiment: '设置 → 基线与指标 → 结果 → 消融'
}

export type MindStatus = 'none' | 'pending' | 'running' | 'ready' | 'failed'

export interface MindEvidenceIds {
  ords: number[]
  figure_ids: number[]
  equation_ids: number[]
  /** 分组节点自己没有直接引用，依据汇总自下级节点 */
  derived: boolean
}

export interface MindNode {
  id: string
  paper_id: string
  view: MindView
  parent_id: string | null
  ord: number
  /** 根为 1 */
  depth: number
  child_count: number
  title: string
  summary: string
  kind: string
  kind_label: string
  origin: 'ai' | 'user'
  user_edited: boolean
  collapsed: boolean
  x: number | null
  y: number | null
  evidence: MindEvidenceIds
  /** 后端从数据库解析出的真实原文依据（页码、区域、原文片段） */
  refs: Evidence[]
  created_at: string
  updated_at: string
}

export interface MindVerification {
  report: {
    dropped_refs: number
    dropped_nodes: number
    dropped_figure_refs: number
    trimmed_text: number
    depth_cut: number
    child_cut: number
    duplicates: number
    notes: string[]
  }
  merge: { added: number; updated: number; removed: number; kept_user: number; kept_scaffold: number }
  attempts: number
  context: { passages: number; chars: number }
  branch: boolean
}

export interface MindTree {
  paper_id: string
  view: MindView
  view_label: string
  status: MindStatus
  job_id: string | null
  /** 正在重新生成的分支根节点 id；为空表示整张图 */
  scope: string | null
  model: string | null
  error: string | null
  verification: MindVerification | null
  generated_at: string | null
  nodes: MindNode[]
  stats: { count: number; max_depth: number; user_nodes: number; edited_nodes: number }
}

export interface MindOverview {
  views: Record<MindView, { status: MindStatus; count: number; job_id: string | null; generated_at: string | null }>
  kinds: Record<string, string>
}

export interface MindFigureRef {
  id: number
  page: number
  bbox: BBox
  kind: 'figure' | 'table' | string
  label: string | null
  caption: string
}
export interface MindEquationRef {
  id: number
  page: number
  bbox: BBox
  number: string | null
}
export interface MindCardBrief {
  id: string
  title: string
  question: string
  relation: 'explain' | 'evidence'
  direct: string
  saved: boolean
  created_at: string
}
export interface MindNoteRef {
  id: number
  text: string
  page: number | null
  passage_ord: number | null
  card_id: string | null
}
export interface MindCodeRef {
  id: string
  repo_id: string
  module: string | null
  path: string
  line_start: number
  line_end: number
  confidence: string
  status: string
}

export interface MindNodeDetail extends MindNode {
  path: Array<{ id: string; title: string }>
  figures: MindFigureRef[]
  equations: MindEquationRef[]
  cards: MindCardBrief[]
  notes: MindNoteRef[]
  code: MindCodeRef[]
  children: Array<{ id: string; title: string; kind: string }>
}

export interface MindSearchHit {
  id: string
  view: MindView
  title: string
  summary: string
  kind: string
  kind_label: string
  path: string[]
  match: 'title' | 'summary'
}

export interface MindNodePatch {
  title?: string
  summary?: string
  kind?: string
  collapsed?: boolean
  x?: number | null
  y?: number | null
  parent_id?: string
  index?: number
}

/** 解释节点（SSE）流式状态。 */
export interface ExplainState {
  nodeId: string
  status: 'streaming' | 'done' | 'error'
  text: string
  card: InsightCard | null
  error: string | null
}

// ---------------------------------------------------------------- 主动研究提示
export type FindingType = 'weak_motivation' | 'weak_support' | 'unexplained_param' | 'overreach' | 'further_reading'
export type CheckStatus = 'unchecked' | 'inferred' | 'verified'
export type FindingUserState = 'new' | 'read' | 'ignored' | 'useful'

export interface Finding {
  id: string
  paper_id: string
  type: FindingType
  type_label: string
  title: string
  detail: string
  anchor: string | null
  evidence_ords: number[]
  evidence: Evidence[]
  check_status: CheckStatus
  check_label: string
  ai_check_status: Exclude<CheckStatus, 'verified'>
  confidence: 'high' | 'medium'
  user_state: FindingUserState
  note: string | null
  created_at: string
  updated_at: string
}

export interface FindingList {
  status: MindStatus
  job_id: string | null
  model: string | null
  error: string | null
  verdict: 'issues' | 'no_issues' | null
  summary: string | null
  scanned_at: string | null
  verification: { report: Record<string, number>; attempts: number } | null
  items: Finding[]
  ignored_count: number
  types: Record<string, string>
  check_labels: Record<string, string>
}
