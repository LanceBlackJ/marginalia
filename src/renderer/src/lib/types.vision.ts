/** 公式与图表理解（F5）的前端类型，契约见 docs/API-vision.md。 */
import type { BBox, CardAnswer, Evidence, InsightCard, Verification } from './types'

// ---------- 公式 ----------
export type Confidence = 'high' | 'medium' | 'low'

export interface EquationItem {
  id: number
  page: number
  bbox: BBox
  number: string | null
  /** PDF 文字层（数学字体常为乱码，仅供参考） */
  text: string
  passage_ord: number | null
}

export interface EquationLatex {
  equation_id: number
  latex: string | null
  confidence: Confidence | null
  status: 'none' | 'ok' | 'failed'
  warnings: string[]
  error: string | null
  model: string | null
  cached: boolean
}

export interface LocatedEquation {
  id: number
  page: number
  bbox: BBox
  number: string | null
  passage_ord: number | null
  score: number
}

export interface LocateResult {
  equation: LocatedEquation | null
  candidates: LocatedEquation[]
}

export type SymbolStatus = 'defined' | 'undefined' | 'unsupported'

export interface SymbolEntry {
  symbol: string
  meaning: string
  ords: number[]
  status: SymbolStatus
}

export interface EquationAnswerExtras {
  symbols: SymbolEntry[]
  relations: string | null
  derivation: string | null
  equation?: { id: number; number: string | null; page: number; latex: string | null; confidence: Confidence | null; warnings: string[] }
}

export type EquationCard = InsightCard & { answer: CardAnswer & EquationAnswerExtras }

export function isEquationCard(card: InsightCard): card is EquationCard {
  return card.object_type === 'equation' && Array.isArray((card.answer as Partial<EquationAnswerExtras>).symbols)
}

export interface ExplainFigureRequest {
  paper_id: string
  figure_id: number
  question?: string
  chain_id?: string | null
  conversation_id?: string | null
  save?: boolean | null
}

/** 图解析卡片（object_type=figure）的 answer.figure：AI 看到了什么、读出的数字是图上印的还是估读的 */
export interface FigureAnswerExtras {
  figure: {
    id: number
    label: string | null
    page: number
    answerable: boolean
    seen: string
    numbers: Array<{ value: string; how: 'printed' | 'estimated' }>
    model: string
  }
}
export type FigureCard = InsightCard & { answer: CardAnswer & FigureAnswerExtras }

export function isFigureCard(card: InsightCard): card is FigureCard {
  return card.object_type === 'figure' && !!(card.answer as Partial<FigureAnswerExtras>).figure
}

export interface ExplainEquationRequest {
  paper_id: string
  equation_id: number
  question?: string
  chain_id?: string | null
  conversation_id?: string | null
  save?: boolean | null
  refresh_latex?: boolean
}

// ---------- 图表 ----------
export type AnalysisStatus = 'none' | 'pending' | 'running' | 'ready' | 'failed'
export type FigureKind = 'framework' | 'chart' | 'table'

export interface FigureGroup {
  /** 主区域 id（接口里的 figure_id） */
  id: number
  member_ids: number[]
  page: number
  bbox: BBox
  kind: 'figure' | 'table'
  label: string | null
  caption_ord: number | null
  caption: string
  analysis_status: AnalysisStatus
  analysis_kind: FigureKind | null
  job_id: string | null
}

export interface FrameworkModule {
  id: string
  label: string
  role: string | null
  kind: 'input' | 'process' | 'model' | 'output' | 'data' | 'other'
  group: string | null
  /** 模块名是否出现在图的文字层：true 找到；false 没找到；null 无法核对（位图） */
  in_text: boolean | null
}
export interface FrameworkFlow {
  from: string
  to: string
  label: string
  kind: 'data' | 'control' | 'loss' | 'other'
}
export interface FrameworkData {
  summary: string
  modules: FrameworkModule[]
  flows: FrameworkFlow[]
  groups: Array<{ id: string; label: string; members: string[] }>
  uncertain: string[]
  verifiable: boolean
  warnings: string[]
}

export interface ChartValue {
  text: string
  value: string
  precision: 'exact' | 'approx'
  verified: boolean
}
export interface ChartAxis {
  label: string
  unit: string
  range: string
}
export interface ChartPanel {
  label: string
  chart_type: string
  title: string
  x_axis: ChartAxis
  y_axis: ChartAxis
  series: string[]
  observations: Array<{ text: string; has_unverified_numbers: boolean }>
  values: ChartValue[]
}
export interface ChartData {
  panels: ChartPanel[]
  unreadable: string[]
  verifiable: boolean
  warnings: string[]
}

export interface TableData {
  title: string
  columns: string[]
  rows: string[][]
  notes: string
  unverified_cells: Array<[number, number]>
  verified_ratio: number | null
  verifiable: boolean
  warnings: string[]
}

export interface ModuleMapping {
  description: string | null
  ords: number[]
  found: boolean
}

export interface FigureAnalysisText {
  inferences: Array<{ text: string; basis_ords: number[] }>
  limitations: string[]
  // 框架图
  overview?: string
  flow_explanation?: string
  module_descriptions?: Record<string, ModuleMapping>
  // 实验图 / 表格
  direct?: string
  points?: Array<{ text: string; ords: number[] }>
  paper_claims?: Array<{ text: string; supported: 'yes' | 'partly' | 'no' | 'unclear'; note: string }>
}

export interface FigureVerification extends Verification {
  ungrounded_numbers?: string[]
}

export interface FigureAnalysis {
  figure_id: number
  status: AnalysisStatus
  kind: FigureKind | null
  job_id: string | null
  model: string | null
  error: string | null
  updated_at: string | null
  data: FrameworkData | ChartData | TableData | null
  mapping: { modules: Record<string, ModuleMapping>; dropped_unsupported: number } | null
  analysis: FigureAnalysisText | null
  evidence: Evidence[]
  verification: FigureVerification | null
  warnings: string[]
  derived_ord: number | null
}

export interface FigureAnswer {
  answerable: boolean
  answer: string
  seen: string
  numbers: Array<{ value: string; how: 'printed' | 'estimated' }>
  warnings: string[]
  model: string
  figure_id: number
}

export interface VisionStatus {
  figures: Array<{ figure_id: number; status: AnalysisStatus; kind: string; job_id: string | null }>
  equations: Array<{ equation_id: number; status: 'ok' | 'failed'; confidence: Confidence | null }>
}
