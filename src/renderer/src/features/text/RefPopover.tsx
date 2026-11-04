import { useEffect, useLayoutEffect, useRef, useState } from 'react'
import { ChevronLeft, ChevronRight, MoreHorizontal } from 'lucide-react'
import { Button } from '@/components/ui'
import { useReaderStore } from '@/features/reader/readerStore'
import { placePopover, refHeadline, type Placement } from './refModel'
import { useRefStore } from './refStore'

const WIDTH = 380

/** 点击引用标记弹出的轻量浮层：原文、解析出的题录、本文引用处和三个主要动作。 */
export function RefPopover() {
  const pop = useRefStore((s) => s.popover)
  const byIndex = useRefStore((s) => s.byIndex)
  const contexts = useRefStore((s) => s.contexts)
  const step = useRefStore((s) => s.stepPopover)
  const openDrawer = useRefStore((s) => s.openDrawer)
  const openMenu = useRefStore((s) => s.openMenu)
  const loadContexts = useRefStore((s) => s.loadContexts)
  const summarize = useRefStore((s) => s.summarize)
  const ref = useRef<HTMLDivElement>(null)
  const [place, setPlace] = useState<Placement | null>(null)

  const index = pop ? pop.refs[pop.pos] : null
  const item = index !== null ? byIndex.get(index) : undefined

  useEffect(() => {
    if (index !== null) void loadContexts(index)
  }, [index, loadContexts])

  useLayoutEffect(() => {
    if (!pop) return setPlace(null)
    const h = ref.current?.offsetHeight ?? 300
    setPlace(placePopover(pop.rect, WIDTH, { w: window.innerWidth, h: window.innerHeight }, h))
  }, [pop, contexts, index])

  useEffect(() => {
    if (!pop) return
    ref.current?.focus()
    const close = (e: PointerEvent): void => {
      const t = e.target as Element | null
      if (t?.closest?.('[data-selecttranslate-ui]')) return // 点划词翻译的浮条/浮层不算点在外面
      if (!ref.current?.contains(t as Node)) useRefStore.getState().closeAll()
    }
    const key = (e: KeyboardEvent): void => {
      if (e.key === 'Escape' && !document.querySelector('[data-selecttranslate-ui]')) useRefStore.getState().closeAll()
    }
    document.addEventListener('pointerdown', close, true)
    document.addEventListener('keydown', key)
    return () => {
      document.removeEventListener('pointerdown', close, true)
      document.removeEventListener('keydown', key)
    }
  }, [pop])

  if (!pop || index === null || !place) return pop && index !== null ? <div ref={ref} className="pd-refpop" style={{ visibility: 'hidden', left: 0, top: 0 }} /> : null
  if (!item) {
    return (
      <div ref={ref} className="pd-refpop" role="dialog" aria-label="参考文献" tabIndex={-1} style={{ left: place.left, top: place.top, bottom: place.bottom, maxHeight: place.maxHeight }}>
        <p className="pd-refpop-note">没有找到第 {index} 条参考文献。</p>
      </div>
    )
  }
  const ctx = contexts[index]?.[0]
  const group = pop.refs.length > 1
  return (
    <div
      ref={ref}
      className="pd-refpop"
      role="dialog"
      aria-label={`参考文献 ${index}`}
      tabIndex={-1}
      style={{ left: place.left, top: place.top, bottom: place.bottom, maxHeight: place.maxHeight, width: WIDTH }}
    >
      <div className="pd-refpop-head">
        <strong>参考文献 [{index}]</strong>
        {group && (
          <span className="pd-refpop-step">
            <button type="button" aria-label="上一条" disabled={pop.pos === 0} onClick={() => step(-1)}>
              <ChevronLeft size={14} />
            </button>
            {pop.pos + 1}/{pop.refs.length}
            <button type="button" aria-label="下一条" disabled={pop.pos === pop.refs.length - 1} onClick={() => step(1)}>
              <ChevronRight size={14} />
            </button>
          </span>
        )}
      </div>
      <p className="pd-refpop-raw" data-translatable>{item.raw}</p>
      {item.title && !item.messy && (
        <dl className="pd-refpop-meta">
          <dt>标题</dt>
          <dd>{refHeadline(item)}</dd>
          {item.authors.length > 0 && (
            <>
              <dt>作者</dt>
              <dd>{item.authors.join('; ')}</dd>
            </>
          )}
          {(item.year || item.venue) && (
            <>
              <dt>出处</dt>
              <dd>{[item.venue, item.year].filter(Boolean).join(' · ')}</dd>
            </>
          )}
        </dl>
      )}
      {(item.messy || item.merged) && <p className="pd-refpop-warn">这条文字被双栏排版弄乱了，解析出的题录可能不准，请以原文为准。</p>}
      {ctx && (
        <div className="pd-refpop-ctx">
          <span className="pd-refpop-label">本文引用处</span>
          <button type="button" className="pd-tp-link" onClick={() => void useReaderStore.getState().jumpToOrd(ctx.ord)}>
            第 {ctx.page} 页
          </button>
          <p data-translatable>{ctx.sentence}</p>
        </div>
      )}
      <div className="pd-refpop-actions">
        <Button size="sm" variant="primary" onClick={() => openDrawer(index)} title="联网查询 OpenAlex / Crossref，只发送这条参考文献的标题、作者或 DOI">
          查看摘要
        </Button>
        <Button
          size="sm"
          onClick={() => {
            openDrawer(index)
            void summarize(index)
          }}
        >
          AI 概括
        </Button>
        <Button
          size="sm"
          icon={<MoreHorizontal size={14} />}
          aria-label="更多"
          onClick={(e) => {
            const r = e.currentTarget.getBoundingClientRect()
            openMenu(pop.refs, r.left, r.bottom + 4)
          }}
        >
          更多
        </Button>
      </div>
    </div>
  )
}
