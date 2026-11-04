import { useNavigate } from 'react-router-dom'
import { truncate } from '@/lib/format'
import type { BBox, Evidence } from '@/lib/types'
import type { MindEquationRef, MindFigureRef } from '@/lib/types.mindmap'
import { openEvidenceInReader } from '@/features/shared/evidenceNav'
import { useReaderStore } from '@/features/reader/readerStore'

type Target = { page: number; bbox?: BBox | null }

/**
 * 跳转到原文位置。阅读器里（右侧面板）直接在已打开的 PDF 中定位并闪烁高亮；
 * 在工作区页面则先登记待办跳转再打开这篇论文的阅读器，阅读器加载完成后定位。
 */
export function useMindJump(paperId: string | null): (t: Target) => void {
  const navigate = useNavigate()
  return (t) => {
    if (!paperId) return
    const reader = useReaderStore.getState()
    if (reader.paperId === paperId && reader.loadState === 'ready') {
      if (t.bbox && t.bbox.length === 4) reader.jumpToEvidence({ page: t.page, bbox: t.bbox })
      else reader.jumpToPage(t.page)
      return
    }
    openEvidenceInReader(navigate, paperId, t)
  }
}

/** 节点依据：每条是一个“p.页码”引用，悬停看原文片段，点击跳转并高亮。 */
export function EvidenceChips({ refs, jump, limit }: { refs: Evidence[]; jump: (t: Target) => void; limit?: number }) {
  const shown = limit ? refs.slice(0, limit) : refs
  return (
    <>
      {shown.map((r) => (
        <button
          key={r.ord}
          type="button"
          className="pd-cite"
          title={`第 ${r.page} 页${r.section_title ? ` · ${r.section_title}` : ''}\n${truncate(r.quote, 160)}`}
          onClick={() => jump({ page: r.page, bbox: r.bbox })}
        >
          p.{r.page}
        </button>
      ))}
      {limit && refs.length > limit && <span className="pd-aux pd-weak">+{refs.length - limit}</span>}
    </>
  )
}

export function EntityChips({ figures, equations, jump }: { figures: MindFigureRef[]; equations: MindEquationRef[]; jump: (t: Target) => void }) {
  if (figures.length === 0 && equations.length === 0) return null
  return (
    <>
      {figures.map((f) => (
        <button
          key={`f${f.id}`}
          type="button"
          className="pd-cite"
          title={f.caption ? truncate(f.caption, 160) : undefined}
          onClick={() => jump({ page: f.page, bbox: f.bbox })}
        >
          {f.label || (f.kind === 'table' ? '表' : '图')} · p.{f.page}
        </button>
      ))}
      {equations.map((e) => (
        <button key={`e${e.id}`} type="button" className="pd-cite" onClick={() => jump({ page: e.page, bbox: e.bbox })}>
          公式{e.number ? ` (${e.number})` : ''} · p.{e.page}
        </button>
      ))}
    </>
  )
}
