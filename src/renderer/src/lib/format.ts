export function formatRelativeTime(iso: string | null | undefined): string {
  if (!iso) return '从未'
  const t = new Date(iso).getTime()
  if (Number.isNaN(t)) return ''
  const diff = Date.now() - t
  const min = Math.floor(diff / 60_000)
  if (min < 1) return '刚刚'
  if (min < 60) return `${min} 分钟前`
  const hr = Math.floor(min / 60)
  if (hr < 24) return `${hr} 小时前`
  const day = Math.floor(hr / 24)
  if (day < 30) return `${day} 天前`
  return formatDate(iso)
}

export function formatDate(iso: string | null | undefined): string {
  if (!iso) return ''
  const d = new Date(iso)
  if (Number.isNaN(d.getTime())) return ''
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`
}

export function formatDateTime(iso: string | null | undefined): string {
  if (!iso) return ''
  const d = new Date(iso)
  if (Number.isNaN(d.getTime())) return ''
  const hh = String(d.getHours()).padStart(2, '0')
  const mm = String(d.getMinutes()).padStart(2, '0')
  return `${formatDate(iso)} ${hh}:${mm}`
}

export function formatBytes(n: number): string {
  if (!Number.isFinite(n) || n < 0) return '-'
  if (n < 1024) return `${n} B`
  const units = ['KB', 'MB', 'GB', 'TB']
  let v = n / 1024
  let i = 0
  while (v >= 1024 && i < units.length - 1) {
    v /= 1024
    i++
  }
  return `${v.toFixed(v >= 100 ? 0 : 1)} ${units[i]}`
}

export function formatNumber(n: number): string {
  return n.toLocaleString('zh-CN')
}

export function formatAuthors(authors: string[], max = 3): string {
  if (authors.length === 0) return '作者未知'
  if (authors.length <= max) return authors.join('、')
  return `${authors.slice(0, max).join('、')} 等`
}

export function formatPercent(v: number): string {
  return `${Math.round(Math.min(1, Math.max(0, v)) * 100)}%`
}

export function truncate(text: string, max: number): string {
  const t = text.replace(/\s+/g, ' ').trim()
  return t.length > max ? `${t.slice(0, max)}…` : t
}

const TASK_LABELS: Record<string, string> = {
  translate: '翻译',
  explain: '解释',
  summarize: '总结',
  term: '术语解释',
  motivation: '设计动机',
  design_motivation: '设计动机',
  query_plan: '检索规划',
  method_analysis: '方法分析',
  formula: '公式',
  figure: '图表',
  experiment: '实验',
  free: '自由提问',
  free_qa: '自由提问',
  deep_qa: '深入分析',
  analysis: '整体分析',
  paper_analysis: '整体分析'
}

export function taskTypeLabel(t: string): string {
  return TASK_LABELS[t] ?? t
}

const JOB_TYPE_LABELS: Record<string, string> = {
  import: '导入 PDF',
  import_doi: 'DOI 导入',
  import_url: '链接导入',
  attach_pdf: '附加 PDF',
  paper_analysis: '论文分析',
  supplement_add: '添加补充材料',
  supplement_download: '下载补充材料',
  supplement_discover: '查找补充材料',
  supplement_analyze: '分析补充材料',
  supplement_explain: '解释数据集',
  supplement_extract: '解压补充材料',
  translate_paper: '全文翻译',
  embed_paper: '建立语义索引',
  ocr_paper: '扫描版文字识别',
  vision_figure: '图表理解',
  equations_latex: '公式识别',
  tables_analysis: '表格识别',
  mindmap: '思维导图',
  proactive_scan: '研究提示',
  kb_extract: '提取知识',
  paper_compare: '论文比较',
  code_fetch: '获取代码仓库',
  code_import: '导入代码',
  code_reanalyze: '重新分析代码',
  code_associate: '论文与代码关联',
  code_consistency: '参数一致性检查',
  repro_assess: '复现可行性评估'
}

export function jobTypeLabel(t: string): string {
  return JOB_TYPE_LABELS[t] ?? t
}

const JOB_STATUS_LABELS: Record<string, string> = {
  pending: '等待中',
  running: '执行中',
  waiting_approval: '等待授权',
  paused: '已暂停',
  completed: '已完成',
  failed: '失败',
  cancelled: '已取消'
}

export function jobStatusLabel(s: string): string {
  return JOB_STATUS_LABELS[s] ?? s
}

export function isActiveJob(status: string): boolean {
  return status === 'pending' || status === 'running' || status === 'waiting_approval'
}
