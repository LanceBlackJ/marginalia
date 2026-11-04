import { toast } from '@/components/ui'
import { useInsightStore } from '@/features/insight/insightStore'
import { useNotesStore } from '@/features/notes/notesStore'
import { useRefStore } from '@/features/text/refStore'
import type { AskIntent, HighlightColor } from '@/lib/types'
import type { SelectionInfo } from './pdf/selection'
import { useMarksStore } from './marksStore'
import { useReaderStore } from './readerStore'
import { useParaStore } from '@/features/paragraph/paraStore'
import { useSearchStore } from './searchStore'
import { useTranslateStore } from './translateStore'

export type QuickIntent = Extract<AskIntent, 'explain' | 'summarize' | 'term' | 'motivation'>

function clearNativeSelection(): void {
  window.getSelection()?.removeAllRanges()
  useReaderStore.getState().setSelection(null)
}

export function translateSelection(sel: SelectionInfo): void {
  clearNativeSelection()
  void useTranslateStore.getState().start(sel)
}

/** 解释 / 总结 / 术语解释 / 分析设计动机：在右侧 AI 面板里生成研究卡片。 */
export function askQuick(sel: SelectionInfo, intent: QuickIntent): void {
  const reader = useReaderStore.getState()
  const selection = { text: sel.text, page: sel.page }
  reader.setContextSelection(selection)
  reader.setRight(true, 'ai')
  clearNativeSelection()
  useInsightStore.getState().setActiveChain(null)
  void useInsightStore.getState().ask({ question: '', intent, selection })
}

/** 解释公式：先让视觉模型把公式截图转写成 LaTeX，再讲解符号与含义（走 /ai/explain-equation）。 */
export function explainEquation(sel: SelectionInfo, equationId: number): void {
  const reader = useReaderStore.getState()
  const selection = { text: sel.text, page: sel.page }
  reader.setContextSelection(selection)
  reader.setRight(true, 'ai')
  clearNativeSelection()
  useInsightStore.getState().setActiveChain(null)
  void useInsightStore.getState().ask({ question: '', intent: 'explain', selection, equationId })
}

/** 提问：把选中内容作为上下文，光标落到提问框。 */
export function askFreeform(sel: SelectionInfo): void {
  const reader = useReaderStore.getState()
  reader.setContextSelection({ text: sel.text, page: sel.page })
  reader.setRight(true, 'ai')
  clearNativeSelection()
  // 面板可能刚打开，等它渲染后再聚焦
  window.setTimeout(() => useReaderStore.getState().focusComposer(), 60)
}

export function noteSelection(sel: SelectionInfo): void {
  const reader = useReaderStore.getState()
  useNotesStore.getState().startDraft({ quote: sel.text, page: sel.page })
  reader.setRight(true, 'notes')
  clearNativeSelection()
}

export function highlightSelection(sel: SelectionInfo, color: HighlightColor): void {
  void useMarksStore.getState().addHighlight(sel, color)
  clearNativeSelection()
}

/** 复制选中的文字（原样，不做显示层整理）。 */
export function copySelection(sel: SelectionInfo): void {
  const text = sel.text
  clearNativeSelection()
  navigator.clipboard
    .writeText(text)
    .then(() => toast.success('已复制'))
    .catch(() => toast.error('复制失败', '浏览器没有给剪贴板权限'))
}

/** 在整篇论文里搜索选中的文字：打开搜索面板并直接执行。 */
export function searchSelection(sel: SelectionInfo): void {
  const paperId = useReaderStore.getState().paperId
  const q = sel.text.trim().slice(0, 120)
  clearNativeSelection()
  if (!paperId || !q) return
  useReaderStore.getState().setSearchOpen(true)
  useSearchStore.getState().setQuery(q)
  void useSearchStore.getState().run(paperId, q)
}

/** 选中的是引用编号：打开对应参考文献的详情抽屉。 */
export function openCitation(sel: SelectionInfo, index: number): void {
  clearNativeSelection()
  useRefStore.getState().openDrawer(index)
}

/** 这一段怎么读：找到选中文字所在的段落，在文字视图贴在段落下面、PDF 里高亮并浮出卡片。 */
export function roleSelection(sel: SelectionInfo): void {
  const paperId = useReaderStore.getState().paperId
  const info = { text: sel.text, page: sel.page, ord: sel.ord }
  clearNativeSelection()
  if (paperId) void useParaStore.getState().openFromSelection(paperId, info)
}
