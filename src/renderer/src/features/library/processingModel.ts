/** 论文处理进度的显示文案（纯函数）：公式和表格没处理好之前，不说“已完成/已分析”。 */
import type { AnalysisStatus, ProcessingSummary } from '@/lib/types'

export interface ProcessingCell {
  text: string
  tone: 'default' | 'warning' | 'danger'
  /** 悬停提示：需要注意时列出每一条原因；待处理时说明怎么触发 */
  title?: string
}

/** 文库“处理”列：有 processing 摘要就以它为准；没有（旧后端）才退回只看分析状态。 */
export function processingCell(p: ProcessingSummary | undefined, analysis: AnalysisStatus): ProcessingCell | null {
  if (!p) {
    if (analysis === 'ready') return { text: '已分析', tone: 'default' }
    if (analysis === 'running' || analysis === 'pending') return { text: '分析中', tone: 'default' }
    if (analysis === 'failed') return { text: '分析失败', tone: 'danger' }
    return null
  }
  switch (p.overall) {
    case 'done':
      return { text: '已完成', tone: 'default' }
    case 'processing':
      return { text: p.label, tone: 'default', title: '正在后台处理，完成前不会显示“已完成”' }
    case 'waiting':
      return { text: p.label, tone: 'default', title: '还没有开始；打开这篇论文会自动开始处理' }
    default:
      return { text: '需要注意', tone: 'warning', title: p.reasons.join('\n') || undefined }
  }
}

/** 阅读器/概览里的一行提示：还有没处理好的，就说清楚“部分内容可能还是截图”；全部好了返回 null。 */
export function processingNote(p: ProcessingSummary | undefined): string | null {
  if (!p || p.overall === 'done') return null
  const f = p.steps.formulas
  const t = p.steps.tables
  const parts: string[] = []
  if (f.state === 'running' || f.state === 'pending') parts.push(`公式识别${f.state === 'running' ? '中' : '尚未开始'} ${f.done}/${f.total}`)
  if (t.state === 'running' || t.state === 'pending') parts.push(`表格还原${t.state === 'running' ? '中' : '尚未开始'} ${t.done}/${t.total}`)
  if (parts.length) return `${parts.join('，')}，部分内容可能还是截图或行文字`
  if (p.overall === 'attention') return p.reasons.join('；') || null
  return p.overall === 'processing' ? '正在后台处理…' : null
}

/** 还有步骤在跑或等着：需要继续轮询。 */
export function isProcessingActive(p: ProcessingSummary | undefined): boolean {
  return !!p && (p.overall === 'processing' || p.overall === 'waiting')
}
