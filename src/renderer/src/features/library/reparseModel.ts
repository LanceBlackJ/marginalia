import type { PaperSummary, ReparseStats } from '@/lib/types'

/** 文库行上的小提示 */
export const REPARSE_HINT = '有新版解析，可重新解析'
export const REPARSE_HINT_DETAIL =
  '解析器已经改进，这篇论文是用旧版本解析的。重新解析会更新它的文字与图表；' +
  '你的笔记、批注、书签、卡片和思维导图会保留，并重新对应到新的段落。完成后可以撤销，保留 7 天。'

type Outdatable = Pick<PaperSummary, 'has_pdf' | 'status'> & Partial<Pick<PaperSummary, 'parser_outdated' | 'role'>>

/** 这篇论文要不要提示“有新版解析”：有 PDF、不是补充材料（补充材料随主论文一起重新解析）、不在解析中，且服务端标了过期。 */
export function needsReparse(p: Outdatable): boolean {
  return !!p.parser_outdated && p.has_pdf && p.role !== 'supplement' && p.status !== 'parsing'
}

/** 能重新解析：有 PDF 的正文论文（不要求已过期：用户可以随时重新解析）。 */
export function canReparse(p: Outdatable): boolean {
  return p.has_pdf && p.role !== 'supplement' && p.status !== 'parsing'
}

/** 列表里有多少篇论文可以提示重新解析。 */
export function outdatedCount(papers: Outdatable[]): number {
  return papers.filter(needsReparse).length
}

/** 重新解析完成后给用户看的一句话摘要：段落数变化、笔记重新对应的情况（失效的会明确说明，不是悄悄丢掉）。 */
export function describeReparse(stats: ReparseStats | undefined | null): string {
  if (!stats) return '已用新版解析器更新。'
  const parts: string[] = []
  const p = stats.passages
  if (p) parts.push(`段落 ${p.old} → ${p.new}`)
  const n = stats.notes
  if (n && n.total > 0) {
    parts.push(`${n.mapped}/${n.total} 条笔记已对应到新位置`)
    if (n.stale > 0) parts.push(`${n.stale} 条的位置已失效，原文引用和页码已保留`)
  }
  return parts.length ? parts.join('；') + '。' : '已用新版解析器更新。'
}

/** 后端因“新解析的段落数少得可疑”而中止时，错误文字里带“差别太大”：界面据此提供“仍然替换”。 */
export function isSuspiciousResult(error: string | null | undefined): boolean {
  return !!error && error.includes('差别太大')
}

/** 笔记的位置是否已失效（重新解析后引用位置对不上，原文引用与页码仍在）。 */
export function noteIsStale(note: { stale?: boolean | null; passage_ord: number | null; quote?: string | null }): boolean {
  return !!note.stale && note.passage_ord === null
}
