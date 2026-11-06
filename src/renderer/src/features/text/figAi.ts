import { useReaderStore } from '@/features/reader/readerStore'
import { useInsightStore } from '@/features/insight/insightStore'
import type { FigureRegion, TableAnalysisItem } from '@/lib/api.text'
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

export const DEFAULT_FIGURE_QUESTION = '请解析这张图：它画的是什么、关键的数据或趋势是什么、想说明什么结论。'

/**
 * 让 AI 解析一张图：和表一样，在右侧对话里出一张卡片（不再单独弹窗），能接着追问。
 * 用视觉模型直接看这张图的截图（不是只读图注）；图上读不出的会明说，估读的数字单独标出。
 */
export function aiParseFigure(fig: FigureRegion, question: string = DEFAULT_FIGURE_QUESTION): void {
  const caption = (fig.caption_ord != null ? useTextStore.getState().items.find((i) => i.ord === fig.caption_ord)?.text : null) ?? fig.label ?? '图'
  useReaderStore.getState().setRight(true, 'ai')
  useInsightStore.getState().setActiveChain(null)
  void useInsightStore.getState().ask({
    question,
    intent: 'free',
    selection: { text: caption, page: fig.page, passage_ords: fig.caption_ord != null ? [fig.caption_ord] : [] },
    figureId: fig.id
  })
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
