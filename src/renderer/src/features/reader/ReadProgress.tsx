import { useEffect, useMemo, useRef, useState } from 'react'
import { useTextStore } from '@/features/text/textStore'

/** 估算阅读速度（字符 / 分钟）：英文论文按约 1100，中文按约 450。 */
export function estimateMinutes(chars: number, ratio: number, cjkShare: number): number {
  const speed = cjkShare > 0.3 ? 450 : 1100
  return Math.max(0, Math.round((chars * (1 - ratio)) / speed))
}

/**
 * 阅读进度：阅读区顶部一条 2px 细线，加上“约剩 N 分钟”。
 * 监听阅读区的滚动（文字栏优先，其次 PDF 栏），不改动阅读区本身。
 */
export function ReadProgress() {
  const items = useTextStore((s) => s.items)
  const [ratio, setRatio] = useState(0)
  const host = useRef<HTMLDivElement>(null)

  const { chars, cjk } = useMemo(() => {
    let c = 0
    let cj = 0
    for (const it of items) {
      if (it.kind === 'reference' || it.kind === 'figure_text') continue
      c += it.text.length
      cj += (it.text.match(/[一-鿿]/g) ?? []).length
    }
    return { chars: c, cjk: c > 0 ? cj / c : 0 }
  }, [items])

  useEffect(() => {
    const root = host.current?.parentElement
    if (!root) return
    const onScroll = (e: Event): void => {
      const el = e.target
      if (!(el instanceof HTMLElement)) return
      if (!el.classList.contains('pd-textreader') && !el.classList.contains('pd-viewer')) return
      // 并排视图里两栏都在滚动：以文字栏为准
      if (el.classList.contains('pd-viewer') && root.querySelector('.pd-textreader')) return
      const max = el.scrollHeight - el.clientHeight
      setRatio(max > 0 ? Math.min(1, Math.max(0, el.scrollTop / max)) : 0)
    }
    root.addEventListener('scroll', onScroll, true)
    return () => root.removeEventListener('scroll', onScroll, true)
  }, [])

  const minutes = estimateMinutes(chars, ratio, cjk)
  return (
    <div ref={host} className="pd-readprogress" aria-hidden={chars === 0}>
      <div className="pd-readprogress-bar" style={{ width: `${(ratio * 100).toFixed(1)}%` }} />
      {chars > 0 && (
        <span className="pd-readprogress-eta" title="按平均阅读速度估算，仅供参考">
          {Math.round(ratio * 100)}% · {minutes > 0 ? `约剩 ${minutes} 分钟` : '快读完了'}
        </span>
      )}
    </div>
  )
}
