import { useEffect } from 'react'
import { ArrowDown, ArrowUp, MessageSquarePlus, NotebookPen, RefreshCw, X } from 'lucide-react'
import { Skeleton } from '@/components/ui'
import { useReaderStore } from '@/features/reader/readerStore'
import { useNotesStore } from '@/features/notes/notesStore'
import { useParaStore } from './paraStore'
import type { ParaInsight } from '@/lib/types'
import './paragraph.css'

/** 打开“这一段怎么读”期间，给页面打上标记：文字视图里其余段落变暗（PDF 里的聚光灯由 PdfViewer 自己处理）。 */
export function ParaSpot() {
  const open = useParaStore((s) => s.ord !== null)
  useEffect(() => {
    if (open) document.documentElement.dataset.paraSpot = 'true'
    else delete document.documentElement.dataset.paraSpot
    return () => {
      delete document.documentElement.dataset.paraSpot
    }
  }, [open])
  return null
}

/** 一段的解读正文：作用、带走什么（附原话）、承上启下、读的时候留意、对照这几段。阅读器里的卡片和精读页共用。 */
export function ParaInsightBody({ insight, onJump }: { insight: ParaInsight; onJump: (ord: number) => void }) {
  return (
    <>
      {insight.does && (
        <p>
          <b>作用</b>
          {insight.does}
        </p>
      )}
      {insight.takeaway && (
        <p>
          <b>带走什么</b>
          {insight.takeaway}
        </p>
      )}
      {insight.key_sentence && (
        <blockquote className="pd-para-key" title="这一段里的原话">
          {insight.key_sentence}
        </blockquote>
      )}
      {(insight.from || insight.to) && (
        <div className="pd-para-flow">
          <b>承上启下</b>
          <span>
            {insight.from && (
              <span>
                <ArrowUp size={12} /> {insight.from}
              </span>
            )}
            {insight.to && (
              <span>
                <ArrowDown size={12} /> {insight.to}
              </span>
            )}
          </span>
        </div>
      )}
      {insight.watch.length > 0 && (
        <div>
          <b>读的时候留意</b>
          <ul className="pd-para-watch">
            {insight.watch.map((w) => (
              <li key={w}>{w}</li>
            ))}
          </ul>
        </div>
      )}
      {insight.see.length > 0 && (
        <div className="pd-para-see">
          <b>对照这几段</b>
          {insight.see.map((s) => (
            <button key={s.ord} type="button" className="pd-para-seebtn" onClick={() => onJump(s.ord)} title="跳到这一段">
              <span className="pd-cite">P{s.ord}</span> {s.why}
            </button>
          ))}
        </div>
      )}
    </>
  )
}

/**
 * 选中一段后的“这一段怎么读”卡片：它在整篇论证里起什么作用、读完该带走什么（附原文里最关键的一句）、承上启下、读的时候留意什么、该对照哪几段。
 * 全部是 AI 的判断，不是论文原文；关键句是这一段里的原话，对照段落能点过去。
 */
export function ParaInsightCard({ variant }: { variant: 'inline' | 'float' }) {
  const { ord, passage, status, insight, error, close } = useParaStore()
  const paperId = useReaderStore((s) => s.paperId)
  const jumpToOrd = useReaderStore((s) => s.jumpToOrd)

  useEffect(() => {
    const onKey = (e: KeyboardEvent): void => {
      if (e.key === 'Escape') close()
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [close])

  if (ord === null || !paperId) return null

  const ask = (): void => {
    if (!passage) return
    const reader = useReaderStore.getState()
    reader.setContextSelection({ text: passage.text.slice(0, 700), page: passage.page })
    reader.setRight(true, 'ai')
    window.setTimeout(() => useReaderStore.getState().focusComposer(), 60)
  }
  const note = (): void => {
    if (!passage || !insight) return
    useNotesStore.getState().startDraft({
      quote: (insight.key_sentence ?? passage.text).slice(0, 300),
      page: passage.page,
      passage_ord: passage.ord,
      text: insight.takeaway
    })
    useReaderStore.getState().setRight(true, 'notes')
  }

  return (
    <aside className={`pd-para pd-para--${variant}`} aria-label="这一段怎么读" data-ord-card={ord}>
      <header className="pd-para-head">
        {insight && <span className="pd-para-role">{insight.role}</span>}
        <span className="pd-para-title">这一段怎么读</span>
        <span className="pd-para-ai" title="这是 AI 对这一段的判断，不是论文原文">
          AI 的判断
        </span>
        <span className="pd-spacer" />
        <button type="button" className="pd-para-x" aria-label="关闭" onClick={close}>
          <X size={14} />
        </button>
      </header>

      {status === 'loading' && !insight && (
        <div className="pd-para-loading">
          <p className="pd-aux pd-muted">正在读这一段和它的上下文……</p>
          <Skeleton height={14} />
          <Skeleton height={14} />
        </div>
      )}
      {status === 'error' && (
        <p className="pd-field-error">
          {error}{' '}
          <button type="button" className="pd-para-link" onClick={() => void useParaStore.getState().open(paperId, ord)}>
            重试
          </button>
        </p>
      )}

      {insight && (
        <div className="pd-para-body" data-busy={status === 'loading'}>
          <ParaInsightBody insight={insight} onJump={(o) => void jumpToOrd(o)} />
          <footer className="pd-para-foot">
            <button type="button" onClick={ask} disabled={!passage}>
              <MessageSquarePlus size={13} /> 追问
            </button>
            <button type="button" onClick={note} disabled={!passage}>
              <NotebookPen size={13} /> 记为笔记
            </button>
            <button type="button" onClick={() => void useParaStore.getState().open(paperId, ord, true)} disabled={status === 'loading'}>
              <RefreshCw size={13} /> 重新分析
            </button>
          </footer>
        </div>
      )}
    </aside>
  )
}
