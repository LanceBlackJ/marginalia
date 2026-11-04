import { formatRelativeTime, taskTypeLabel, truncate } from '@/lib/format'
import type { InsightCard } from '@/lib/types'

/** 模型短名：去掉量化、精度、日期和 Instruct 后缀，卡片信息行里不再被截成“…W…”。完整名放在悬停提示里。 */
export function shortModelName(name: string | null | undefined): string {
  if (!name) return ''
  let n = name.trim()
  const slash = n.lastIndexOf('/')
  if (slash >= 0) n = n.slice(slash + 1)
  const parts = n.split('-')
  const keep = parts.filter((p, i) => {
    if (i === 0) return true
    if (/^\d{4}$/.test(p) || /^\d{8}$/.test(p)) return false // 日期后缀，如 0731、20250219
    if (/^(w\d+a\d+|bf16|fp16|fp8|int8|int4|awq|gptq|instruct|chat)$/i.test(p)) return false
    return true
  })
  return keep.join('-') || n
}

export function verifyLabel(status: string): string {
  switch (status) {
    case 'verified':
      return '已核实'
    case 'partial':
      return '部分核实'
    case 'no_evidence':
      return '无依据'
    case 'unverified':
      return '未核实'
    default:
      return ''
  }
}

type MetaCard = Pick<InsightCard, 'task_type' | 'intent' | 'object_type' | 'selection' | 'model' | 'created_at' | 'saved'>

/** 卡片信息行：类型 · 页码 · 模型 · 时间，合成一行淡色小字。 */
export function cardMetaParts(card: MetaCard): string[] {
  const kind = taskTypeLabel(card.task_type) || taskTypeLabel(card.intent)
  const where = card.object_type === 'equation' ? '公式' : card.selection ? `第 ${card.selection.page} 页` : ''
  return [kind, where, shortModelName(card.model), formatRelativeTime(card.created_at)].filter(Boolean)
}

/** 问题行：选中的文字加问题，合成一行；两者重复时只留一份。 */
export function questionLine(
  question: string | null | undefined,
  selectionText: string | null | undefined
): { text: string; full: string; quote: string; ask: string } {
  const q = (question ?? '').replace(/\s+/g, ' ').trim()
  const s = (selectionText ?? '').replace(/\s+/g, ' ').trim()
  const parts: string[] = []
  if (s) parts.push(`“${truncate(s, 60)}”`)
  if (q && q !== s) parts.push(q)
  return { text: parts.join(' · '), full: [s, q].filter(Boolean).join('\n'), quote: s ? truncate(s, 120) : '', ask: q && q !== s ? q : '' }
}
