import type { Approval, AssessStatus, CompareStatus, Evidence, ExecLimits, ExecRun, ExecStatus, ExportFile, MetricSource, PlanStep } from '@/lib/types.repro'

export const ASSESS_STATUS_LABEL: Record<AssessStatus, string> = {
  found: '已找到',
  missing: '没找到',
  uncertain: '不确定',
  risk: '风险',
  info: '提示'
}

export const APPROVAL_LABEL: Record<Approval, string> = {
  none: '无需批准',
  pending: '待批准',
  approved: '已批准',
  rejected: '已拒绝'
}

export const COMPARE_LABEL: Record<CompareStatus, string> = {
  within_tolerance: '在容差内',
  outside_tolerance: '超出容差',
  within_reported_range: '在论文报告的波动范围内',
  outside_reported_range: '超出论文报告的波动范围',
  no_tolerance: '未设容差，只给差值',
  unmapped: '没有对应指标',
  not_comparable: '无法比较'
}

/** 数字显示：最多 4 位有效数字，去掉多余的 0；空值显示破折号。 */
export function formatNum(x: number | null | undefined, digits = 4): string {
  if (x === null || x === undefined || !Number.isFinite(x)) return '—'
  if (x === 0) return '0'
  const abs = Math.abs(x)
  if (abs >= 1e6 || abs < 1e-4) return x.toExponential(Math.max(0, digits - 1)).replace(/\.?0+e/, 'e')
  return String(Number(x.toPrecision(digits)))
}

export function formatRel(rel: number | null | undefined): string {
  if (rel === null || rel === undefined || !Number.isFinite(rel)) return '—'
  return `${formatNum(rel * 100, 3)}%`
}

/** 用户输入的“容差百分比”（如 5 表示 5%）→ 相对容差 0.05；空串表示不设（null）；非法输入返回 undefined。 */
export function tolPercentToRel(input: string): number | null | undefined {
  const s = input.trim().replace(/%$/, '')
  if (!s) return null
  const n = Number(s)
  return Number.isFinite(n) && n >= 0 ? n / 100 : undefined
}

export function relToTolPercent(rel: number | null | undefined): string {
  return rel === null || rel === undefined ? '' : formatNum(rel * 100, 4)
}

/** 环境信息：每行 “键=值”（也接受“键: 值”）。 */
export function parseEnvText(text: string): Record<string, string> {
  const out: Record<string, string> = {}
  for (const raw of text.split('\n')) {
    const m = raw.match(/^\s*([^=:：\s][^=:：]*?)\s*[=:：]\s*(.+?)\s*$/)
    if (m && Object.keys(out).length < 40) out[m[1].slice(0, 40)] = m[2].slice(0, 300)
  }
  return out
}

export function envToText(env: Record<string, string>): string {
  return Object.entries(env)
    .map(([k, v]) => `${k}=${v}`)
    .join('\n')
}

export function evidenceLabel(e: Evidence): string {
  if (e.type === 'code') {
    if (e.line_start <= 0) return e.path
    return e.line_end > e.line_start ? `${e.path}:${e.line_start}-${e.line_end}` : `${e.path}:${e.line_start}`
  }
  if (e.type === 'repo') return '仓库记录'
  return e.page ? `论文第 ${e.page} 页` : '论文'
}

/** 计划里等待用户处理的步骤数（有命令、还没批准也没拒绝）。 */
export function pendingCount(steps: PlanStep[]): number {
  return steps.filter((s) => s.needs_approval && s.approval === 'pending').length
}

/** 一步的命令能不能被导出的脚本启用：只有“已批准”。 */
export function commandEnabled(step: PlanStep): boolean {
  return step.command.trim() !== '' && step.approval === 'approved'
}

export function downloadText(file: ExportFile): void {
  const mime = file.format === 'sh' ? 'text/x-shellscript' : 'text/markdown'
  const url = URL.createObjectURL(new Blob([file.content], { type: `${mime};charset=utf-8` }))
  const a = document.createElement('a')
  a.href = url
  a.download = file.filename
  a.click()
  URL.revokeObjectURL(url)
}

export const EXEC_STATUS_LABEL: Record<ExecStatus, string> = {
  queued: '排队中',
  running: '运行中',
  succeeded: '成功',
  failed: '失败',
  timeout: '超时',
  cancelled: '已取消'
}

export const LIMIT_LABEL: Record<keyof ExecLimits, string> = {
  memory_mb: '内存 MB',
  cpus: 'CPU 核数',
  pids: '进程数',
  timeout_s: '时长 秒'
}

export function execActive(run: Pick<ExecRun, 'status'>): boolean {
  return run.status === 'queued' || run.status === 'running'
}

/** 把输入框里的上限解析成数字：必须在 [min, max] 内，否则返回 null。 */
export function parseLimit(text: string, min: number, max: number): number | null {
  const n = Number(text.trim())
  if (!text.trim() || !Number.isFinite(n) || n < min || n > max) return null
  return n
}

/** 数值来源的显示：文件路径，或日志第几行。 */
export function sourceLabel(src: MetricSource | undefined): string {
  if (!src) return '你导入的内容'
  return src.kind === 'file' ? `输出文件 ${src.path}` : `日志第 ${src.line} 行`
}

export function limitsText(l: ExecLimits): string {
  return `内存 ${l.memory_mb} MB，CPU ${l.cpus} 核，进程 ${l.pids} 个，时长 ${l.timeout_s} 秒`
}
