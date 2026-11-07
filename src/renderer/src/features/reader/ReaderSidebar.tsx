import { useEffect, useRef, useState } from 'react'
import { BookmarkPlus, PanelLeftClose, PanelLeftOpen, Trash2 } from 'lucide-react'
import { Button, EmptyState, IconButton, Segmented } from '@/components/ui'
import { useTextStore } from '@/features/text/textStore'
import { useMarksStore } from './marksStore'
import { activeOutlineIndex } from './outlinePos'
import { useReaderStore, type LeftTab } from './readerStore'
import type { PDFDocumentProxy } from './pdf/pdfjs'

function OutlineTab() {
  const outline = useReaderStore((s) => s.outline)
  const source = useReaderStore((s) => s.outlineSource)
  const currentPage = useReaderStore((s) => s.currentPage)
  const anchorOrd = useReaderStore((s) => s.textAnchor)
  const sectionTitle = useReaderStore((s) => s.activeSectionTitle)
  const items = useTextStore((s) => s.items)
  const jumpToSection = useReaderStore((s) => s.jumpToSection)

  if (outline.length === 0) {
    return (
      <EmptyState
        title="没有检测到目录"
        description={source === 'none' ? '这篇论文没有可识别的章节结构，可以用缩略图或页码跳转。' : '正在读取章节…'}
      />
    )
  }
  const currentIdx = activeOutlineIndex(outline, items, anchorOrd, currentPage, sectionTitle)
  const minLevel = Math.min(...outline.map((o) => o.level))
  return (
    <div>
      {outline.map((it, i) => (
        <button
          key={it.id}
          type="button"
          className="pd-outline-item"
          data-current={i === currentIdx}
          style={{ paddingLeft: 8 + (it.level - minLevel) * 12 }}
          title={it.title}
          onClick={() => jumpToSection(it.page, it.title)}
        >
          <span className="pd-outline-page">{it.page}</span>
          {it.title}
        </button>
      ))}
    </div>
  )
}

const THUMB_W = 150

function Thumb({ doc, page, current, onGo }: { doc: PDFDocumentProxy; page: number; current: boolean; onGo: () => void }) {
  const hostRef = useRef<HTMLDivElement>(null)
  const [visible, setVisible] = useState(false)
  const [ratio, setRatio] = useState(1.414)
  const rendered = useRef(false)

  useEffect(() => {
    const el = hostRef.current
    if (!el) return
    const io = new IntersectionObserver((entries) => entries.forEach((e) => e.isIntersecting && setVisible(true)), { rootMargin: '200px' })
    io.observe(el)
    return () => io.disconnect()
  }, [])

  useEffect(() => {
    if (!visible || rendered.current) return
    let cancelled = false
    let task: { cancel: () => void } | null = null
    void (async () => {
      try {
        const p = await doc.getPage(page)
        const base = p.getViewport({ scale: 1 })
        if (cancelled) return
        setRatio(base.height / base.width)
        const scale = (THUMB_W / base.width) * (window.devicePixelRatio || 1)
        const vp = p.getViewport({ scale })
        const canvas = document.createElement('canvas')
        canvas.width = Math.floor(vp.width)
        canvas.height = Math.floor(vp.height)
        const ctx = canvas.getContext('2d')
        if (!ctx) return
        const t = p.render({ canvas, canvasContext: ctx, viewport: vp })
        task = t
        await t.promise
        if (cancelled) return
        hostRef.current?.replaceChildren(canvas)
        rendered.current = true
      } catch {
        /* 取消或渲染失败：忽略 */
      }
    })()
    return () => {
      cancelled = true
      task?.cancel()
    }
  }, [visible, doc, page])

  return (
    <button type="button" className="pd-thumb" data-current={current} onClick={onGo} aria-label={`第 ${page} 页`}>
      <div className="pd-thumb-frame" ref={hostRef} style={{ width: THUMB_W, height: Math.round(THUMB_W * ratio) }} />
      <div className="pd-thumb-label">{page}</div>
    </button>
  )
}

function ThumbnailsTab({ doc }: { doc: PDFDocumentProxy }) {
  const currentPage = useReaderStore((s) => s.currentPage)
  const jumpToPage = useReaderStore((s) => s.jumpToPage)
  const wrapRef = useRef<HTMLDivElement>(null)

  // 当前页的缩略图滚动到可见区域
  useEffect(() => {
    const el = wrapRef.current?.querySelector<HTMLElement>('[data-current="true"]')
    el?.scrollIntoView({ block: 'nearest' })
  }, [currentPage])

  return (
    <div ref={wrapRef}>
      {Array.from({ length: doc.numPages }, (_, i) => i + 1).map((p) => (
        <Thumb key={p} doc={doc} page={p} current={p === currentPage} onGo={() => jumpToPage(p)} />
      ))}
    </div>
  )
}

function BookmarksTab() {
  const bookmarks = useMarksStore((s) => s.bookmarks)
  const remove = useMarksStore((s) => s.removeBookmark)
  const jumpToPage = useReaderStore((s) => s.jumpToPage)

  const add = (): void => {
    const st = useReaderStore.getState()
    const section = st.currentSectionTitle()
    void useMarksStore.getState().addBookmark(st.currentPage, section ?? `第 ${st.currentPage} 页`)
  }
  return (
    <div>
      <Button size="sm" icon={<BookmarkPlus size={14} />} onClick={add} style={{ width: '100%', marginBottom: 8 }}>
        为当前页添加书签
      </Button>
      {bookmarks.length === 0 ? (
        <EmptyState title="还没有书签" description="给重要的页面添加书签，之后可以一键回来。" />
      ) : (
        bookmarks.map((b) => (
          <div key={b.id} className="pd-bookmark">
            <button type="button" className="pd-bookmark-go pd-ellipsis" onClick={() => jumpToPage(b.page)} title={b.label}>
              <span className="pd-muted">第 {b.page} 页</span>
              <span style={{ marginLeft: 6 }}>{b.label}</span>
            </button>
            <IconButton icon={<Trash2 size={13} />} label="删除书签" size="sm" onClick={() => void remove(b.id)} />
          </div>
        ))
      )}
    </div>
  )
}

/** 收成窄条：只留一排章节序号（顶层章节），当前章节有黑色指示，点击跳转；顶部按钮展开完整目录。 */
function NarrowStrip() {
  const outline = useReaderStore((s) => s.outline)
  const currentPage = useReaderStore((s) => s.currentPage)
  const jumpToSection = useReaderStore((s) => s.jumpToSection)
  const setLeftNarrow = useReaderStore((s) => s.setLeftNarrow)
  const minLevel = outline.length > 0 ? Math.min(...outline.map((o) => o.level)) : 0
  const top = outline.filter((o) => o.level === minLevel)
  const anchorOrd = useReaderStore((s) => s.textAnchor)
  const sectionTitle = useReaderStore((s) => s.activeSectionTitle)
  const items = useTextStore((s) => s.items)
  // 完整目录里的当前章节，再归到它所在的顶层章节
  const full = activeOutlineIndex(outline, items, anchorOrd, currentPage, sectionTitle)
  let currentIdx = -1
  top.forEach((it, i) => {
    if (outline.indexOf(it) <= full) currentIdx = i
  })
  return (
    <aside className="pd-reader-left pd-reader-left--narrow" aria-label="论文导航">
      <IconButton icon={<PanelLeftOpen size={16} />} label="展开目录" size="sm" onClick={() => setLeftNarrow(false)} />
      <div className="pd-narrow-ticks">
        {top.map((it, i) => (
          <button
            key={it.id}
            type="button"
            className="pd-narrow-tick"
            data-current={i === currentIdx}
            title={`${i + 1}. ${it.title} · 第 ${it.page} 页`}
            aria-label={`${it.title}，第 ${it.page} 页`}
            onClick={() => jumpToSection(it.page, it.title)}
          >
            {i + 1}
          </button>
        ))}
      </div>
    </aside>
  )
}

export function ReaderSidebar({ doc }: { doc: PDFDocumentProxy | null }) {
  const tab = useReaderStore((s) => s.leftTab)
  const setLeft = useReaderStore((s) => s.setLeft)
  const narrow = useReaderStore((s) => s.leftNarrow)
  const setLeftNarrow = useReaderStore((s) => s.setLeftNarrow)
  if (narrow) return <NarrowStrip />
  return (
    <aside className="pd-reader-left" aria-label="论文导航">
      <div className="pd-reader-left-head">
        <Segmented<LeftTab>
          variant="text"
          aria-label="导航类型"
          value={tab}
          onChange={(t) => setLeft(true, t)}
          options={[
            { value: 'outline', label: '目录' },
            { value: 'thumbnails', label: '缩略图' },
            { value: 'bookmarks', label: '书签' }
          ]}
        />
        <span className="pd-spacer" />
        <IconButton icon={<PanelLeftClose size={15} />} label="收成窄条" size="sm" onClick={() => setLeftNarrow(true)} />
      </div>
      <div className="pd-reader-left-body">
        {tab === 'outline' && <OutlineTab />}
        {tab === 'thumbnails' && doc && <ThumbnailsTab doc={doc} />}
        {tab === 'bookmarks' && <BookmarksTab />}
      </div>
    </aside>
  )
}
