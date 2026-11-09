/** 代码理解模块的类型（契约见 docs/API-code.md）。 */

export type RepoStatus = 'pending' | 'downloading' | 'scanning' | 'ready' | 'failed'
export type AuthoredBy = 'author' | 'user' | 'self'
export type FileRole = 'entry' | 'config' | 'dependency' | 'data' | 'model' | 'train' | 'test' | 'util' | 'doc' | 'other'
export type Certainty = 'resolved' | 'name' | 'ambiguous' | 'external'

export interface EntryPoint {
  path: string
  symbol: string | null
  kind: string
  reason: string
  command?: string | null
  role?: FileRole | null
  /** 在疑似拷贝来的第三方目录里（旧分析结果没有这个字段） */
  vendored?: boolean
}

export interface RepoOverview {
  files: number
  source_files: number
  symbols: number
  calls: number
  languages: Record<string, { files: number; lines: number; bytes: number }>
  frameworks: string[]
  dependency_files: string[]
  config_files: string[]
  entry_points: EntryPoint[]
  /** 文件很多又自带依赖文件的子目录，多半是拷贝来的第三方项目（只含最外层） */
  vendored?: Array<{ path: string; files: number; lines: number }>
  top_dirs: Array<{ path: string; files: number }>
  roles: Record<string, number>
  skipped: { binary: number; too_large: number }
  parse_warnings: Array<{ path: string; message: string }>
}

export interface CodeRepo {
  id: string
  paper_id: string
  origin: 'github' | 'zip' | 'folder' | 'self_implemented'
  authored_by: AuthoredBy
  url: string | null
  owner: string | null
  name: string | null
  branch: string | null
  commit_sha: string | null
  commit_date: string | null
  downloaded_at: string | null
  size_bytes: number | null
  status: RepoStatus
  error: string | null
  warnings: string[]
  overview: RepoOverview | null
  /** 分析逻辑有新版而这个仓库还是旧结果：可以一键重新分析（不用重新下载，论文关联会迁移） */
  analysis_outdated?: boolean
  created_at: string
}

export interface CodeSource {
  url: string
  kind: string
  supported: boolean
  page: number
  ord: number
  context: string
  is_code_statement: boolean
}

export interface RepoInfo {
  owner: string
  name: string
  url: string
  default_branch: string
  branch: string
  size_bytes: number
  commit_sha: string
  commit_date: string | null
  description: string | null
  archived: boolean
  requires_approval: boolean
  too_large: boolean
  warnings: string[]
}

export interface FetchResult {
  repo_id: string
  job_id: string
  requires_approval: boolean
  info: RepoInfo | null
}

export interface CodeFileEntry {
  path: string
  language: string | null
  size: number
  lines: number
  role: FileRole
  skipped: 'binary' | 'too_large' | 'unsafe' | null
}

export interface CodeSymbol {
  id: number
  path: string
  name: string
  qualname: string
  kind: 'function' | 'method' | 'class' | 'module' | 'main'
  parent: string | null
  line_start: number
  line_end: number
  signature: string | null
  doc: string | null
}

export interface FileData {
  path: string
  language: string | null
  size: number
  lines: number
  role: FileRole
  skipped: string | null
  /** 能直接预览的二进制/图片类文件；null 表示只能看文字或看不了 */
  preview?: 'image' | 'pdf' | 'video' | 'audio' | null
  symbols: CodeSymbol[]
  content: string | null
  truncated: boolean
  note: string | null
}

export interface Callee {
  line: number
  callee_name: string
  certainty: Certainty
  callee_id: number | null
  qualname: string | null
  path: string | null
  line_start: number | null
  line_end: number | null
}

export interface Caller {
  line: number
  certainty: Certainty
  caller_id: number
  qualname: string
  path: string
  line_start: number
  line_end: number
  kind: string
}

export interface SymbolGraph {
  symbol: CodeSymbol
  callees: Callee[]
  callers: Caller[]
}

export interface FlowNode {
  id: number
  label: string
  name: string
  path: string
  kind: string
  line_start: number
  line_end: number
  depth: number
  order: number
  signature: string
  doc: string
  phase: string
  uncertain: boolean
}

export interface FlowEdge {
  source: number
  target: number
  line: number
  certainty: Certainty
  order: number
}

export interface FlowAnnotation {
  title: string
  summary: string
  ai: boolean
  model: string
}

export interface FlowRoot {
  symbol_id: number
  path: string
  label: string
  reason: string
  role?: FileRole | null
  vendored?: boolean
}

export interface Flow {
  root: number
  roots: FlowRoot[]
  nodes: FlowNode[]
  edges: FlowEdge[]
  truncated: boolean
  truncated_by?: Array<'depth' | 'nodes'>
  max_depth: number
  max_nodes?: number
  note: string
  annotations: Record<string, FlowAnnotation>
}

/** 一条代码证据：已由后端回查真实文件与行号。 */
export interface CodeEvidence {
  ref: number
  path: string
  line_start: number
  line_end: number
  symbol: string | null
  snippet: string
  verified: boolean
  reason: string | null
  /** code：代码里真实的行；paper：论文原文段落（只作背景，点击跳到论文） */
  kind: 'code' | 'paper'
  ord?: number
  page?: number | null
  section?: string | null
}

/** 解释时一并给了模型的论文段落。source 标明为什么选它：已确认/系统建议/语义相近/用词相近。 */
export type PaperContextSource = 'confirmed' | 'suggested' | 'semantic' | 'lexical'
export interface PaperContextItem {
  ord: number
  page: number
  section: string | null
  source: PaperContextSource
  text?: string
}

export type VerificationStatus = 'verified' | 'partial' | 'no_evidence' | 'unverified'

export interface CodeCard {
  id: string
  repo_id: string
  target: ExplainTarget
  question: string
  status: string
  model: string
  task_type: 'code_explain'
  raw_markdown: string
  answer: {
    direct: string
    detail: string | null
    evidence_notes: Array<{ ref: number; note: string }>
    inferences: Array<{ text: string; basis_refs: number[] }>
    follow_ups: string[]
    paper_context?: PaperContextItem[]
  }
  evidence: CodeEvidence[]
  verification: { status: VerificationStatus; invalid_citations: number[]; notes: string[] }
  usage: { prompt_tokens: number; completion_tokens: number }
  created_at: string
}

export type ExplainKind = 'file' | 'repo' | 'module' | 'function' | 'class' | 'algorithm' | 'dataflow' | 'variable' | 'calls' | 'line_range'

export interface ExplainTarget {
  kind: ExplainKind
  path?: string
  symbol_id?: number
  symbol?: string
  variable?: string
  line_start?: number
  line_end?: number
}

export interface ExplainMeta {
  card_id: string
  model: string
  escalated: boolean
  context: {
    snippets: Array<{ label: string; path: string; line_start: number; line_end: number; role: string }>
    snippet_count: number
    chars_sent: number
    includes: Record<string, boolean>
    papers?: PaperContextItem[]
  }
}

export interface PaperModule {
  name: string
  description: string
  passage_ords: number[]
  pages: number[]
  section: string | null
  keywords?: string[]
}

export interface PaperCodeLink {
  id: string
  paper_id: string
  repo_id: string
  module: PaperModule
  symbol_id: number | null
  path: string
  line_start: number
  line_end: number
  confidence: 'likely' | 'possible'
  method: 'lexical' | 'llm' | 'manual'
  basis: { text: string; matched_terms: string[]; also_in?: string[] }
  status: 'suggested' | 'confirmed' | 'rejected'
  created_at: string
  symbol: { qualname: string | null; kind: string | null; signature: string | null } | null
}

export interface RelatedPassage {
  ord: number
  page: number
  kind: string
  section_title: string | null
  snippet: string
  matched_terms: string[]
  confidence: 'possible'
  method: 'lexical'
}

export interface SymbolPaper {
  symbol: { id: number; qualname: string; path: string; line_start: number; line_end: number }
  paper_id: string
  links: PaperCodeLink[]
  related_passages: RelatedPassage[]
  note: string
}
