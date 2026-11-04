import { useReaderStore } from '@/features/reader/readerStore'
import { useInsightStore } from '@/features/insight/insightStore'
import type { TableAnalysisItem } from '@/lib/api.text'
import { useTextStore } from './textStore'

const MAX_TABLE_CHARS = 5000

/** 识别好的表格转成 Markdown 表（超长截断），随提问一起发给 AI。 */
export function tableToMarkdown(t: TableAnalysisItem): string {
  const d = t.data
  if (!d || d.columns.length === 0) return ''
  const esc = (x: string): string => String(x ?? '').replace(/\|/g, '\|').replace(/\s+/g, ' ')
  const lines = [`| ${d.columns.map(esc).join(' | ')} |`, `| ${d.columns.map(() => '---').join(' | ')} |`, ...d.rows.map((r) => `| ${r.map(esc).join(' | ')} |`)]
  const md = lines.join('\n')
  return md.length > MAX_TABLE_CHARS ? md.slice(0, MAX_TABLE_CHARS) + '\n…（表格较长，已截断）' : md
}

/** 让 AI 解析一张表：表注 + 识别出的表格内容作为上下文，在右侧对话里生成回答（有原文依据，可以接着追问）。 */
export function aiParseTable(t: TableAnalysisItem): void {
  const reader = useReaderStore.getState()
  const caption = useTextStore.getState().items.find((i) => i.ord === t.caption_ord)?.text ?? t.label
  const md = tableToMarkdown(t)
  const question = `请解析${t.label}：它比较了什么、关键数字说明了什么、结论是否被其他内容支持。${md ? `\n\n${t.label} 的内容：\n${md}` : ''}`
  reader.setContextSelection({ text: caption, page: t.page })
  reader.setRight(true, 'ai')
  useInsightStore.getState().setActiveChain(null)
  void useInsightStore.getState().ask({ question, intent: 'free', selection: { text: caption, page: t.page, passage_ords: [t.caption_ord] } as never })
}
