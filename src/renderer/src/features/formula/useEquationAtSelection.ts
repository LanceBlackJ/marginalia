import { useEffect, useState } from 'react'
import { visionApi } from '@/lib/api.vision'
import type { LocatedEquation } from '@/lib/types.vision'
import type { SelectionInfo } from '@/features/reader/pdf/selection'
import { useReaderStore } from '@/features/reader/readerStore'
import { unionRects } from './geometry'

/** 选区与公式区域的重合度达到这个值才认为“选中了一个公式”（后端 locate 的分数，0–1）。 */
const MIN_SCORE = 0.5

/**
 * 当前选区是否落在某个公式上：是则返回该公式（供选区工具条显示“解释公式”）。
 * 只做本地的位置/文字匹配，不调用模型；匹配不上就不显示按钮，而不是放一个点了没用的按钮。
 */
export function useEquationAtSelection(sel: SelectionInfo | null): LocatedEquation | null {
  const paperId = useReaderStore((s) => s.paperId)
  const [eq, setEq] = useState<LocatedEquation | null>(null)
  const key = sel ? `${sel.page}:${sel.text}` : ''

  useEffect(() => {
    setEq(null)
    if (!sel || !paperId) return
    let cancelled = false
    const timer = window.setTimeout(() => {
      const bbox = unionRects(sel.rects)
      visionApi
        .locate(paperId, { page: sel.page, text: sel.text.slice(0, 400), ...(bbox ? { bbox } : {}) })
        .then((r) => {
          if (!cancelled && r.equation && r.equation.score >= MIN_SCORE) setEq(r.equation)
        })
        .catch(() => undefined)
    }, 250)
    return () => {
      cancelled = true
      window.clearTimeout(timer)
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [paperId, key])

  return eq
}
