/** 复现（第 4 阶段）的数据类型（契约见 docs/API-code.md 的“复现”一节）。 */

export interface SandboxInfo {
  docker: { found: boolean; running: boolean; detail: string }
  wsl2: { found: boolean; distros: string[]; detail: string }
  available: boolean
  /** Docker 在运行时为 true：可以在隔离容器里运行；否则不会在本机运行任何代码。 */
  can_run_here: boolean
  reason: string
}

export type EvidenceCode = { type: 'code'; path: string; line_start: number; line_end: number; quote: string }
export type EvidencePaper = { type: 'paper'; page: number | null; ord: number | null; quote: string }
/** 仓库级事实（来源、提交日期、扫描范围等）：不对应某一行代码，也没有链接可跳转。 */
export type EvidenceRepo = { type: 'repo'; quote: string }
export type Evidence = EvidenceCode | EvidencePaper | EvidenceRepo

export type AssessStatus = 'found' | 'missing' | 'uncertain' | 'risk' | 'info'

export interface AssessItem {
  key: string
  status: AssessStatus
  title: string
  text: string
  evidence: Evidence[]
  note: string
}

export interface AssessReport {
  generated_at: string
  ai_used: boolean
  repo: { id: string; url: string | null; commit_sha: string | null; commit_date: string | null; authored_by: string }
  sections: Array<{ key: string; title: string; items: AssessItem[] }>
  effort: { level: '低' | '中' | '高'; basis: string[]; note: string }
  facts: Record<string, unknown>
  note: string
}

export interface AssessmentState {
  repo_id: string
  status: 'none' | 'ready' | 'failed'
  report: AssessReport | null
  updated_at: string | null
  error: string | null
}

export type Approval = 'none' | 'pending' | 'approved' | 'rejected'

export interface ApprovalLog {
  decision: 'approved' | 'rejected' | 'reset'
  actor: string
  command: string
  note: string
  at: string
}

export interface PlanStep {
  id: string
  ord: number
  phase: string
  phase_label: string
  title: string
  detail: string
  command: string
  cwd: string
  side_effects: string[]
  risk: string
  evidence: Evidence[]
  needs_approval: boolean
  approval: Approval
  done: boolean
  note: string
  origin: 'generated' | 'user'
  updated_at: string
  approval_log: ApprovalLog[]
}

export interface ReproPlan {
  id: string
  paper_id: string
  repo_id: string
  title: string
  created_at: string
  updated_at: string
  steps: PlanStep[]
  summary: { pending: number; approved: number; rejected: number; done: number }
  note: string
}

export interface PlanState {
  repo_id: string
  plan: ReproPlan | null
  /** 批准接口的返回里带有：批准只是记录，没有执行任何东西。 */
  executed?: boolean
}

export interface ExportFile {
  filename: string
  format: 'md' | 'sh'
  content: string
}

export interface TargetCandidate {
  ord: number
  page: number
  kind: string
  label: string
  value: number
  std: number | null
  unit: string
  quote: string
  how: string
}

export interface ReproTarget {
  id: string
  name: string
  value: number
  std: number | null
  unit: string
  tol_rel: number | null
  tol_abs: number | null
  comparable: boolean
  page: number | null
  ord: number | null
  quote: string
  /** 用户填写的出处原句是否真的出现在该段落里；没填出处时为 null。 */
  quote_verified: boolean | null
  source: 'table' | 'text' | 'user'
  created_at: string
}

/** 数值的来源：哪次隔离运行的哪个输出文件，或日志的哪一行。 */
export interface MetricSource {
  run_id: string
  kind: 'file' | 'log'
  path: string
  line: number | null
}

export interface RunMetric {
  name: string
  value: number
  std: number | null
  n: number
  raw: string
  source?: MetricSource
}

export interface ReproRun {
  id: string
  paper_id: string
  repo_id: string | null
  name: string
  source_format: 'csv' | 'json' | 'text'
  metrics: RunMetric[] | null
  metric_count?: number
  env: Record<string, string>
  notes: string
  conclusion: string
  created_at: string
  mappings?: Record<string, string>
  warnings?: string[]
}

export type CompareStatus =
  | 'within_tolerance'
  | 'outside_tolerance'
  | 'within_reported_range'
  | 'outside_reported_range'
  | 'no_tolerance'
  | 'unmapped'
  | 'not_comparable'

export interface CompareItem {
  target: ReproTarget
  metric: RunMetric | null
  mapping: 'user' | 'auto' | null
  diff: number | null
  rel_error: number | null
  status: CompareStatus
  notes: string[]
}

export interface CompareResult {
  run_id: string
  items: CompareItem[]
  summary: Record<string, number>
  reasons: Array<{ kind: 'fact' | 'conjecture'; text: string }>
  /** 恒为 null：系统不判断“复现成功/失败”。 */
  verdict: null
  note: string
}

export interface ExecLimits {
  memory_mb: number
  cpus: number
  pids: number
  timeout_s: number
}

export interface ExecOptions {
  available: boolean
  reason: string
  default_image: string
  image: string
  image_present: boolean
  pull: { image: string; size_hint: string } | null
  local_images: Array<{ image: string; size: string }>
  defaults: ExecLimits
  maxima: ExecLimits
  minima: ExecLimits
  mounts: string[]
}

export type ExecStatus = 'queued' | 'running' | 'succeeded' | 'failed' | 'timeout' | 'cancelled'

export interface ExecRun {
  id: string
  paper_id: string
  repo_id: string
  step_id: string | null
  job_id: string | null
  title: string
  command: string
  cwd: string
  image: string
  network: boolean
  limits: ExecLimits
  status: ExecStatus
  reason: string
  exit_code: number | null
  files: Array<{ path: string; size: number; parsable: boolean }>
  imported_run_id: string | null
  created_at: string
  started_at: string | null
  finished_at: string | null
  log?: string[]
}

export interface ExecMetrics {
  exec_id: string
  label: string
  status: ExecStatus
  metrics: RunMetric[]
  warnings: string[]
}
