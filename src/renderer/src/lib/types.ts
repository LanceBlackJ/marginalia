/** docs/API.md 中全部对象的类型定义。 */
import type { PaperMatches } from './types.library'

export type PaperStatus = 'ready' | 'parsing' | 'failed' | 'metadata_only'
export type AnalysisStatus = 'none' | 'pending' | 'running' | 'ready' | 'failed'

export interface ProcessingStep {
  state: 'done' | 'running' | 'pending' | 'failed' | 'skipped'
  done: number
  total: number
  /** 失败或跳过的原因（例如“未配置视觉模型，公式仍是截图”） */
  reason: string
  attention: boolean
}

export interface ProcessingSummary {
  overall: 'processing' | 'waiting' | 'attention' | 'done'
  label: string
  steps: { analysis: ProcessingStep; formulas: ProcessingStep; tables: ProcessingStep; index: ProcessingStep }
  reasons: string[]
  /** 正在重新解析：段落、公式、表格都在被替换，不能说“已完成” */
  reparsing?: boolean
}
export type BBox = [number, number, number, number]

/** 论文标签（即知识库主题）。 */
export interface PaperTag {
  id: string
  name: string
}

export interface PaperSummary {
  id: string
  title: string
  /** 备注名：用户给论文起的名字，显示在标题的位置；原标题不变 */
  alias?: string | null
  authors: string[]
  year: number | null
  venue: string | null
  doi: string | null
  has_pdf: boolean
  status: PaperStatus
  parse_error: string | null
  page_count: number | null
  language: string | null
  favorite: boolean
  reading_page: number
  read_progress: number
  last_opened_at: string | null
  imported_at: string
  source_folder: string | null
  analysis_status: AnalysisStatus
  /** 导入后自动处理的进度（公式、表格、分析、索引）：只有 overall=done 才能说“已完成”。 */
  processing?: ProcessingSummary
  /** 解析器版本：低于当前版本且有 PDF 时 parser_outdated=true，可以“重新解析”获得新版解析结果 */
  parser_version?: number
  parser_outdated?: boolean
  /** 补充材料（F1）：role='supplement' 的论文是某篇论文的子论文，不出现在文献库列表里 */
  role?: 'paper' | 'supplement'
  parent_id?: string | null
  supplement_count?: number
  tags?: PaperTag[]
  /** 文件夹：一篇论文最多在一个文件夹里（标签可以有多个） */
  folder_id?: string | null
  /** 文件夹的完整路径，如“图神经网络 / 注意力” */
  folder_path?: string | null
  /** 有笔记/批注/正文/卡片筛选条件时，每篇论文的命中片段 */
  matches?: PaperMatches
}

export interface AccessLink {
  label: string
  url: string
}

/** 前端自定义的阅读状态，后端原样存取。 */
export interface ReadingState {
  zoomMode?: 'custom' | 'fit-width' | 'fit-page'
  scale?: number
  mode?: 'continuous' | 'single' | 'double'
  leftOpen?: boolean
  /** 左侧栏收成窄条（只显示章节序号竖条） */
  leftNarrow?: boolean
  /** 文字界面最近可见的段落序号：重开论文时回到这里 */
  textAnchor?: number
  /** PDF 界面当前页内的垂直偏移（0–1，相对页高） */
  pdfOffset?: number
  leftTab?: 'outline' | 'thumbnails' | 'bookmarks'
  rightOpen?: boolean
  rightTab?: 'ai' | 'glossary' | 'overview' | 'notes' | 'materials' | 'mindmap' | 'findings' | 'figures' | 'quiz'
  /** 主阅读界面：文字 / 对照 / 译文 / PDF */
  readView?: 'text' | 'both' | 'target' | 'pdf' | 'split'
  textSize?: number
  textWide?: boolean
  rightWidth?: number
  /** 已读到的最远页码，用于计算阅读进度 */
  maxPage?: number
}

export interface PaperDetail extends PaperSummary {
  /** role='supplement' 时：所属主论文的标题 */
  parent_title?: string | null
  abstract: string | null
  source_url: string | null
  original_path: string | null
  arxiv_id: string | null
  metadata_source: 'pdf' | 'crossref' | 'openalex' | 'manual' | null
  reading_state: ReadingState | null
  access_info: AccessLink[]
}

export interface CountItem {
  name: string
  count: number
}

/** 文献库的数量统计（后端从已有记录数出来的）。 */
export interface ActivityDay {
  date: string
  count: number
  imports: number
  opened: number
  notes: number
  annotations: number
  bookmarks: number
  ai: number
}

export interface ActivityStats {
  days: ActivityDay[]
  active_days: number
  total: number
  streak: number
  longest: number
}

export interface LibraryStats {
  total: number
  with_pdf: number
  metadata_only: number
  failed: number
  parsing: number
  favorites: number
  reading: { unread: number; reading: number; finished: number }
  years: Array<{ year: number; count: number }>
  no_year: number
  venues: CountItem[]
  no_venue: number
  folders: CountItem[]
  unfiled: number
  tags: CountItem[]
  imported_by_month: Array<{ month: string; count: number }>
  mine: { notes: number; annotations: number; bookmarks: number; ai_cards: number }
  activity: ActivityStats
}

export interface ProfileItem {
  id: number
  page: number
  label: string | null
  caption: string
  /** 图：合并后的区域（PDF point）；表没有，用表题序号取截图 */
  bbox?: number[]
}

/** 论文详情页汇总（后端按已有数据统计；references 为 null 表示还没解析过参考文献）。 */
export interface PaperProfile {
  id: string
  pages: number | null
  equations: number
  notes: number
  annotations: number
  bookmarks: number
  cards: number
  references: number | null
  figures: ProfileItem[]
  tables: ProfileItem[]
  outline: Array<{ title: string; level: number; page: number }>
  keywords: string[]
  /** 解析里可能不准的地方（没取到摘要/作者/参考文献等），只列有证据的 */
  quality_notes?: string[]
}

export interface PaperList {
  items: PaperSummary[]
  total: number
}

export interface PaperPatch {
  /** 备注名；空字符串清除 */
  alias?: string
  favorite?: boolean
  title?: string
  reading_page?: number
  read_progress?: number
  reading_state?: ReadingState
  touch?: boolean
}

export interface OutlineItem {
  id: string
  title: string
  level: number
  page: number
}

export interface Outline {
  source: 'detected' | 'toc' | 'none'
  items: OutlineItem[]
}

export interface Passage {
  ord: number
  page: number
  kind: 'paragraph' | 'caption' | 'heading' | 'reference'
  section_title: string | null
  /** 文字来源：text = PDF 文字层；ocr = 扫描页的本地 OCR（可能有识别错误，界面应提示） */
  source?: 'text' | 'ocr'
  text: string
  bbox: BBox
}

export interface PagePassages {
  page: number
  width: number
  height: number
  items: Passage[]
}

export interface ParaInsight {
  role: string
  does: string
  takeaway: string
  /** 这一段里的原话（后端核对过） */
  key_sentence: string | null
  from: string
  to: string
  watch: string[]
  see: Array<{ ord: number; why: string }>
}

export interface ParaInsightResponse {
  passage: { ord: number; page: number; bbox: BBox; section_title: string | null; text: string }
  insight: ParaInsight
  cached: boolean
  model?: string
}

export interface PageRect {
  page: number
  bbox: BBox
}

export interface GlossTerm {
  /** 论文里的写法（英文原词、缩写或符号） */
  term: string
  zh: string
  kind: string
  /** 用白话解释它是什么 */
  plain: string
  why: string
  /** true = 论文自己定义/解释了它，quote 是原话（后端核对过）；false = 论文默认读者已懂，解释来自 AI 自己的知识 */
  in_paper: boolean
  ord: number | null
  quote: string | null
}

export interface GlossaryData {
  terms: GlossTerm[]
  status: { state: 'done' | 'running' | 'pending' | 'failed' | 'unavailable'; error: string | null }
}

export interface LocateMatch {
  ord: number
  page: number
  bbox: BBox
  score: number
  section_title: string | null
}

export interface SearchHit {
  ord: number
  page: number
  bbox: BBox
  section_title: string | null
  snippet: string
  highlights: Array<[number, number]>
  score: number
}

export interface Bookmark {
  id: string
  page: number
  label: string
  created_at: string
}

export interface Note {
  id: string
  paper_id: string
  page: number | null
  quote: string | null
  passage_ord: number | null
  text: string
  card_id: string | null
  created_at: string
  updated_at: string
  /** 重新解析后引用位置对不上：保留引用原文与页码，标为“位置已失效” */
  stale?: boolean
}

export interface NoteCreate {
  text: string
  page?: number | null
  quote?: string | null
  passage_ord?: number | null
  card_id?: string | null
}

export type HighlightColor = 'yellow' | 'blue' | 'green' | 'pink'

export interface Annotation {
  id: string
  page: number
  kind: 'highlight'
  color: HighlightColor
  text: string
  rects: BBox[]
  note: string | null
  created_at: string
}

export interface AnnotationCreate {
  page: number
  kind: 'highlight'
  color: HighlightColor
  text: string
  rects: BBox[]
  note?: string | null
}

// ---------- 任务 ----------

export type JobStatus = 'pending' | 'running' | 'waiting_approval' | 'paused' | 'completed' | 'failed' | 'cancelled'
export type JobType = 'import' | 'import_doi' | 'import_url' | 'attach_pdf' | 'paper_analysis'

export interface Job {
  id: string
  type: JobType | string
  title: string
  paper_id: string | null
  status: JobStatus
  progress: number
  message: string
  error: string | null
  result: unknown
  created_at: string
  started_at: string | null
  finished_at: string | null
  can_cancel: boolean
  can_retry: boolean
  /** waiting_approval 状态的任务可以批准/拒绝（如大文件下载） */
  can_approve?: boolean
}

export interface JobDetail extends Job {
  log: string[]
}

export interface ImportResultItem {
  input: string
  status: 'imported' | 'duplicate' | 'metadata_only' | 'failed'
  paper_id: string | null
  message: string | null
  /** 试过自动下载全文但没有下到（不是用户选了“仅保存元数据”）：前端弹出“去下载”的窗口 */
  needs_manual_download?: boolean
  /** 与已有论文同 DOI 但文件不同：可一键“作为补充材料添加”到 target_paper_id（F1） */
  can_attach_as_supplement?: boolean
  target_paper_id?: string | null
  supplement_hint?: boolean
  /** 扫描版 PDF：后台本地 OCR 任务（识别完成前没有文字层） */
  ocr_job_id?: string | null
  ocr_pages?: number
}

export interface ImportResult {
  items: ImportResultItem[]
}

// ---------- AI 设置 / 用量 / 隐私 ----------

export type ProviderKind = 'openai_compatible' | 'anthropic'
export type ModelMode = 'auto' | 'fast' | 'strong'

export interface AISettings {
  provider_kind: ProviderKind
  base_url: string
  fast_model: string
  strong_model: string
  vision_model: string
  mode: ModelMode
  timeout_s: number
  max_retries: number
  temperature: number
  max_output_tokens: number
  auto_analyze: boolean
  price_per_mtok: Record<string, { input: number; output: number }>
  monthly_budget: number | null
  has_api_key: boolean
  configured: boolean
}

export interface ModelTestResult {
  ok: boolean
  model: string
  latency_ms: number | null
  error: string | null
}

export interface AITestResult {
  fast: ModelTestResult | null
  strong: ModelTestResult | null
}

export interface UsageByModel {
  model: string
  calls: number
  prompt_tokens: number
  completion_tokens: number
  estimated_cost: number | null
}

export interface UsageByTask {
  task_type: string
  calls: number
  prompt_tokens: number
  completion_tokens: number
}

export interface UsageSummary {
  month: string
  calls: number
  prompt_tokens: number
  completion_tokens: number
  estimated_cost: number | null
  by_model: UsageByModel[]
  by_task_type: UsageByTask[]
  budget: number | null
  over_budget: boolean
}

export interface PrivacyIncludes {
  question: boolean
  selection: boolean
  notes: boolean
  supplements: boolean
  code: boolean
}

export interface PrivacyRequest {
  id: string
  ts: string
  task_type: string
  model: string
  paper_id: string | null
  paper_title: string | null
  passage_count: number
  chars_sent: number
  includes: PrivacyIncludes
  summary: string
}

/** 发送记录的一页：新的在前，按游标（before_id）取更早的；total 是符合筛选的总条数。 */
export interface PrivacyPage {
  items: PrivacyRequest[]
  has_more: boolean
  total: number
  task_types: Array<{ task_type: string; count: number }>
}

export interface SystemInfo {
  version: string
  data_dir: string
  db_path: string
  papers_dir: string
  paper_count: number
  disk_usage_bytes: number
}

// ---------- AI 问答 / 研究卡片 ----------

export type AskIntent = 'free' | 'explain' | 'summarize' | 'term' | 'motivation' | 'formula' | 'figure' | 'experiment'

export interface SelectionRef {
  text: string
  page: number
  passage_ords?: number[]
}

export interface AskRequest {
  paper_id: string
  question: string
  intent: AskIntent
  selection?: SelectionRef | null
  chain_id?: string | null
  save?: boolean | null
  /** 属于哪个对话（一篇论文可以有多个互相独立的对话） */
  conversation_id?: string | null
  /** 论文里没有时联网查资料补充 */
  web?: boolean
  /** 截图/图片、PDF、文本文件（base64），只用于这一次提问 */
  attachments?: Array<{ name: string; mime: string; data: string }>
  /** 修改这张卡片的提问并重新回答（只能是对话里最后一张）：后端原地改写，失败时还原 */
  edit_card_id?: string | null
}

export interface TranslateRequest {
  /** 论文不在文献库里（发现页里的摘要）时不给：只翻译这段文字，不缓存 */
  paper_id?: string
  /** 没有 paper_id 时用来帮助翻译的论文标题 */
  context_title?: string
  text: string
  page?: number
  passage_ord?: number
  force?: boolean
}

export interface ContextIncludes extends PrivacyIncludes {}

export interface AskMeta {
  card_id: string
  chain_id: string
  chain_title: string
  task_type: string
  model: string
  escalated: boolean
  context: {
    passage_ords: number[]
    passage_count: number
    chars_sent: number
    includes: ContextIncludes
    /** 已授权并用于本次回答的补充文档（F1） */
    supplement_docs?: Array<{ n: number; paper_id: string; asset_id: string; label: string }>
    supplement_passages?: Array<{ doc: number; ord: number }>
  }
}

/** 证据来源：缺省为正文；role='supplement' 表示来自补充材料（点击后到子论文的阅读器定位）。 */
export interface EvidenceSource {
  paper_id: string | null
  role: 'supplement'
  doc: number
  label: string
  asset_id?: string | null
}

export interface Evidence {
  ord: number
  page: number
  section_title: string | null
  quote: string
  bbox: BBox
  kind: string
  /** 引用标记：正文 "P12"，补充材料 "S1P12" */
  ref?: string
  source?: EvidenceSource
}

export type VerificationStatus = 'verified' | 'partial' | 'no_evidence' | 'unverified'

export interface Verification {
  status: VerificationStatus
  invalid_citations: number[]
  /** 全部无法核实的引用标记（含补充材料，如 "S1P99"） */
  invalid_refs?: string[]
  notes: string[]
}

export interface CardAnswer {
  direct: string
  detail: string | null
  evidence_notes: Array<{ ord: number; note: string; ref?: string }>
  inferences: Array<{ text: string; basis_ords: number[]; basis_refs?: string[] }>
  follow_ups: string[]
  /** 论文之外：模型自身知识/网络资料补充的内容，不是论文原文 */
  outside?: string | null
  /** 回答里引用到的网络资料（[W序号]） */
  web?: Array<{ n: number; title: string; url: string; source: string }>
}

export interface InsightCard {
  id: string
  paper_id: string
  chain_id: string
  object_type: 'selection' | 'passage' | 'free' | string
  object_label: string
  selection: SelectionRef | null
  question: string
  intent: AskIntent | string
  task_type: string
  model: string
  status: 'complete' | 'failed' | 'cancelled'
  saved: boolean
  conversation_id?: string | null
  /** 这次提问附带的文件（只存名字） */
  attachments?: Array<{ name: string; kind: 'image' | 'pdf' | 'text' }>
  answer: CardAnswer
  raw_markdown: string
  evidence: Evidence[]
  verification: Verification
  usage: { prompt_tokens: number; completion_tokens: number }
  created_at: string
}

export interface Conversation {
  id: string
  paper_id: string
  title: string
  card_count: number
  created_at: string
  updated_at: string
}

export interface Chain {
  id: string
  title: string
  object_type: string
  object_label: string
  card_count: number
  updated_at: string
}

export interface AnalysisSection {
  text: string
  evidence_ords: number[]
}

export const ANALYSIS_KEYS = [
  'background',
  'motivation',
  'challenges',
  'method',
  'modules',
  'experiment_design',
  'results',
  'conclusion',
  'limitations'
] as const
export type AnalysisKey = (typeof ANALYSIS_KEYS)[number]

export interface Analysis {
  status: AnalysisStatus
  job_id: string | null
  model: string | null
  updated_at: string | null
  error: string | null
  content: Record<AnalysisKey, AnalysisSection> | null
  evidence?: Evidence[]
  verification?: Verification
}

/** 重新解析的状态（GET /papers/{id}/reparse）。backup 不为空表示可以撤销最近一次重新解析。 */
export interface ReparseStatus {
  can_reparse: boolean
  parser_version: number
  current_version: number
  outdated: boolean
  active_job_id: string | null
  backup: {
    created_at: string
    expires_at: string
    from_version: number
    to_version: number
    stats: ReparseStats
  } | null
}

export interface ReparseStats {
  passages?: { old: number; new: number; mapped: number; unmapped: number }
  figures?: { old: number; new: number; matched: number }
  equations?: { old: number; new: number; matched: number }
  notes?: { total: number; mapped: number; stale: number }
  [table: string]: unknown
}
