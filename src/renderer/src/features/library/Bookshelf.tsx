import { useEffect, useMemo, useRef, useState, type DragEvent } from 'react'
import { useNavigate } from 'react-router-dom'
import { CheckCircle2, ChevronDown, ChevronRight, FolderInput, MoreHorizontal, Star, Tag as TagIcon } from 'lucide-react'
import { useContextMenu } from '@/components/ui'
import { api } from '@/lib/api'
import { formatAuthors } from '@/lib/format'
import { paperName } from '@/lib/paperName'
import type { PaperSummary } from '@/lib/types'
import type { FolderItem } from '@/lib/types.library'
import { useBlobUrl } from '@/features/figures/useBlobUrl'
import { DRAG_PAPERS } from './filterModel'
import { FolderQuickMenu } from './FolderQuickMenu'
import { dragHandlers, paperMenuItems, type PaperItemActions } from './PaperItems'
import { buildFolderTree, flattenTree, folderPath } from './filterModel'
import './bookshelf.css'

/** 每本书占的宽度（含间距）：用来算一层书架放几本。 */
const BOOK_W = 148
const UNFILED = '__unfiled__'

function useNear(ref: React.RefObject<HTMLElement | null>): boolean {
  const [near, setNear] = useState(false)
  useEffect(() => {
    const el = ref.current
    if (!el || near) return
    if (typeof IntersectionObserver === 'undefined') {
      setNear(true)
      return
    }
    const io = new IntersectionObserver((e) => e.some((x) => x.isIntersecting) && setNear(true), { rootMargin: '400px' })
    io.observe(el)
    return () => io.disconnect()
  }, [ref, near])
  return near
}

export interface BookshelfActions {
  onOpen: (p: PaperSummary) => void
  onToggleFavorite: (p: PaperSummary) => void
  /** 放进文件夹；null 是移出文件夹 */
  onMove: (p: PaperSummary, folderId: string | null) => void
  /** 新建文件夹并把这篇放进去 */
  onCreateFolderFor: (p: PaperSummary, name: string) => Promise<void>
  /** 打开“标签”对话框，给这篇论文加/删标签 */
  onEditTags?: (p: PaperSummary) => void
  /** 论文的完整操作（打开、思维导图、移动、导出、重新解析、删除…）：书上的“更多”按钮和右键菜单用它 */
  more?: PaperItemActions
}

function Book({ paper, folders, actions }: { paper: PaperSummary; folders: FolderItem[]; actions: BookshelfActions }) {
  const ref = useRef<HTMLDivElement>(null)
  const near = useNear(ref)
  const cover = useBlobUrl(near && paper.has_pdf && paper.status !== 'parsing' ? () => api.papers.cover(paper.id) : null, [paper.id, near, paper.status])
  const progress = Math.round(Math.max(0, Math.min(1, paper.read_progress)) * 100)
  const name = paperName(paper)
  const navigate = useNavigate()
  const cm = useContextMenu()
  const items = actions.more ? paperMenuItems(paper, actions.more, navigate) : []
  return (
    <div className="pd-book" ref={ref} onContextMenu={(e) => cm.open(e, items)} {...(actions.more ? dragHandlers(paper, { ...actions.more, onReorder: undefined }, 'x') : {})}>
      {cm.element}
      <button type="button" className="pd-book-cover" onClick={() => actions.onOpen(paper)} title={name} aria-label={`打开《${name}》`}>
        {cover.url ? (
          <img src={cover.url} alt="" draggable={false} />
        ) : (
          <span className="pd-book-blank" aria-hidden="true">
            <span className="pd-book-blank-title">{name}</span>
            <span className="pd-book-blank-meta">{[paper.venue, paper.year].filter(Boolean).join(' · ')}</span>
          </span>
        )}
        {paper.status === 'parsing' && <span className="pd-book-flag">解析中</span>}
        {paper.status === 'metadata_only' && <span className="pd-book-flag">无全文</span>}
        {paper.status === 'failed' && <span className="pd-book-flag">解析失败</span>}
        {paper.finished_at && (
          <span className="pd-book-done" title={`已读完 · ${paper.finished_at.slice(0, 10)}`}>
            <CheckCircle2 size={11} />
            已读完
          </span>
        )}
        {progress > 0 && <span className="pd-book-progress" style={{ width: `${progress}%` }} aria-hidden="true" />}
      </button>
      <button
        type="button"
        className={`pd-book-fav${paper.favorite ? ' is-on' : ''}`}
        aria-label={paper.favorite ? '取消收藏' : '收藏'}
        aria-pressed={paper.favorite}
        onClick={() => actions.onToggleFavorite(paper)}
      >
        <Star size={13} fill={paper.favorite ? 'currentColor' : 'none'} />
      </button>
      {actions.onEditTags && (
        <button type="button" className="pd-book-tagbtn" aria-label="标签" title={(paper.tags ?? []).length ? `标签：${(paper.tags ?? []).map((t) => t.name).join('、')}` : '添加标签'} onClick={() => actions.onEditTags?.(paper)}>
          <TagIcon size={13} />
        </button>
      )}
      {actions.more && (
        <button type="button" className="pd-book-more" aria-label="更多操作" title="更多操作（也可以在书上右键）：打开、移动、导出、删除…" onClick={(e) => cm.open(e, items)}>
          <MoreHorizontal size={13} />
        </button>
      )}
      <FolderQuickMenu
        currentFolderId={paper.folder_id ?? null}
        folders={folders}
        onMove={(fid) => actions.onMove(paper, fid)}
        onCreate={(n) => actions.onCreateFolderFor(paper, n)}
        trigger={({ toggle }) => (
          <button type="button" className="pd-book-folder" aria-label="放进文件夹" title="放进文件夹" onClick={toggle}>
            <FolderInput size={13} />
          </button>
        )}
      />
    </div>
  )
}

/** 书下面的书名：点它和点封面一样打开这篇论文；鼠标移上去（或用键盘聚焦）时浮出完整的标题、作者和来源。 */
function Label({ paper, onOpen }: { paper: PaperSummary; onOpen: (p: PaperSummary) => void }) {
  const meta = [paper.venue, paper.year].filter(Boolean).join(' · ')
  const tags = paper.tags ?? []
  return (
    <div className="pd-book-label">
      <button type="button" className="pd-book-titlebtn" onClick={() => onOpen(paper)} aria-label={`打开《${paperName(paper)}》`}>
        <span className="pd-book-title">{paperName(paper)}</span>
        <span className="pd-book-sub">{[formatAuthors(paper.authors, 1), paper.year].filter(Boolean).join(' · ')}</span>
      </button>
      {tags.length > 0 && (
        <div className="pd-book-tags">
          {tags.slice(0, 3).map((t) => (
            <span key={t.id} className="pd-book-tag">
              {t.name}
            </span>
          ))}
          {tags.length > 3 && <span className="pd-book-tag pd-book-tag--more">+{tags.length - 3}</span>}
        </div>
      )}
      <div className="pd-book-tip" role="tooltip">
        <div className="pd-book-tip-title">{paperName(paper)}</div>
        {paper.alias && <div className="pd-book-tip-line">原标题：{paper.title}</div>}
        <div className="pd-book-tip-line">{formatAuthors(paper.authors, 4)}</div>
        {meta && <div className="pd-book-tip-line">{meta}</div>}
        {(paper.tags ?? []).length > 0 && <div className="pd-book-tip-line">标签：{(paper.tags ?? []).map((t) => t.name).join('、')}</div>}
      </div>
    </div>
  )
}

interface Section {
  key: string
  title: string | null
  papers: PaperSummary[]
}

/** 按文件夹分成几层书架：文件夹按树的顺序，没放进文件夹的在最后叫“未分类”。 */
export function sectionsFor(papers: PaperSummary[], folders: FolderItem[], grouped: boolean): Section[] {
  if (!grouped) return [{ key: 'all', title: null, papers }]
  const byFolder = new Map<string, PaperSummary[]>()
  for (const p of papers) {
    const k = p.folder_id && folders.some((f) => f.id === p.folder_id) ? p.folder_id : UNFILED
    byFolder.set(k, [...(byFolder.get(k) ?? []), p])
  }
  const order = flattenTree(buildFolderTree(folders), new Set(folders.map((f) => f.id)))
  const out: Section[] = []
  for (const f of order) {
    const ps = byFolder.get(f.id)
    if (ps?.length) out.push({ key: f.id, title: folderPath(folders, f.id), papers: ps })
  }
  const rest = byFolder.get(UNFILED)
  if (rest?.length) out.push({ key: UNFILED, title: out.length > 0 ? '未分类' : null, papers: rest })
  return out
}

/**
 * 阅读模式的书架分层：书架首先回答“下一本读什么”，而不是复刻文献管理器的文件夹表格。
 * 文件夹仍由左侧树负责筛选；进入某个文件夹后，这里只保留阅读顺序，不再重复嵌套文件夹。
 */
export function readingSectionsFor(papers: PaperSummary[], grouped: boolean): Section[] {
  const time = (v: string | null | undefined): number => {
    const n = v ? Date.parse(v) : 0
    return Number.isFinite(n) ? n : 0
  }
  const readingRank = (p: PaperSummary): number => (p.finished_at || p.read_progress >= 0.999 ? 2 : p.read_progress > 0 ? 0 : 1)
  const ordered = [...papers].sort((a, b) => {
    const rank = readingRank(a) - readingRank(b)
    if (rank !== 0) return rank
    const primary = rank === 0 ? time(b.last_opened_at) - time(a.last_opened_at) : rank === 1 ? time(b.imported_at) - time(a.imported_at) : time(b.finished_at) - time(a.finished_at)
    return primary || a.title.localeCompare(b.title, 'zh')
  })
  if (!grouped) return [{ key: 'reading-all', title: null, papers: ordered }]
  const groups: Array<[string, string, number]> = [
    ['reading', '继续阅读', 0],
    ['unread', '待读', 1],
    ['finished', '已读完', 2]
  ]
  return groups
    .map(([key, title, rank]) => ({ key, title, papers: ordered.filter((p) => readingRank(p) === rank) }))
    .filter((s) => s.papers.length > 0)
}

/**
 * 整行都能接收拖放：书和书之间的空隙、第一本的左边、最后一本的右边也算——
 * 只认书本身的话，“放到这一行最前面 / 最后面”落在空白处就没有任何效果。按横向位置找最近的一本，在它前面或后面。
 */
function rowDrop(row: PaperSummary[], onReorder: PaperItemActions['onReorder']) {
  if (!onReorder) return {}
  const clearMarks = (root: ParentNode): void => root.querySelectorAll<HTMLElement>('[data-drop-at]').forEach((el) => delete el.dataset.dropAt)
  /** 鼠标最近的那本书（所在格子）和在它的前面还是后面 */
  const locate = (e: DragEvent<HTMLElement>): { slot: HTMLElement; index: number; after: boolean } | null => {
    const slots = Array.from(e.currentTarget.querySelectorAll<HTMLElement>('.pd-shelf-books .pd-shelf-slot'))
    if (slots.length === 0) return null
    let index = 0
    let bestDist = Infinity
    slots.forEach((s, i) => {
      const r = s.getBoundingClientRect()
      const d = Math.abs(e.clientX - (r.left + r.width / 2))
      if (d < bestDist) {
        index = i
        bestDist = d
      }
    })
    const r = slots[index].getBoundingClientRect()
    return { slot: slots[index], index, after: e.clientX > r.left + r.width / 2 }
  }
  return {
    onDragOver: (e: DragEvent<HTMLElement>) => {
      if (!Array.from(e.dataTransfer.types).includes(DRAG_PAPERS)) return
      e.preventDefault()
      e.dataTransfer.dropEffect = 'move'
      const hit = locate(e)
      clearMarks(document)
      if (hit) hit.slot.dataset.dropAt = hit.after ? 'after' : 'before' // 落点：目标书的左边或右边画一条粗线
    },
    onDragLeave: (e: DragEvent<HTMLElement>) => {
      if (!e.currentTarget.contains(e.relatedTarget as Node | null)) clearMarks(e.currentTarget)
    },
    onDrop: (e: DragEvent<HTMLElement>) => {
      const raw = e.dataTransfer.getData(DRAG_PAPERS)
      clearMarks(document)
      if (!raw || row.length === 0) return
      e.preventDefault()
      const hit = locate(e)
      if (!hit) return
      const target = row[Math.min(hit.index, row.length - 1)]
      try {
        const ids = JSON.parse(raw) as string[]
        if (ids.length > 0 && !ids.includes(target.id)) onReorder(ids, target, hit.after)
      } catch {
        /* 不是我们的拖拽数据 */
      }
    }
  }
}

function Shelf({ papers, folders, actions }: { papers: PaperSummary[]; folders: FolderItem[]; actions: BookshelfActions }) {
  const wrap = useRef<HTMLDivElement>(null)
  const [perRow, setPerRow] = useState(6)
  useEffect(() => {
    const el = wrap.current
    if (!el) return
    const calc = (): void => setPerRow(Math.max(2, Math.floor(el.clientWidth / BOOK_W)))
    calc()
    if (typeof ResizeObserver === 'undefined') return
    const ro = new ResizeObserver(calc)
    ro.observe(el)
    return () => ro.disconnect()
  }, [])
  const rows = useMemo(() => {
    const out: PaperSummary[][] = []
    for (let i = 0; i < papers.length; i += perRow) out.push(papers.slice(i, i + perRow))
    return out
  }, [papers, perRow])
  return (
    <div className="pd-shelf-rows" ref={wrap}>
      {rows.map((row, ri) => (
        <section
          className="pd-shelf-row"
          key={ri}
          role="presentation"
          {...rowDrop(row, actions.more?.onReorder)}
        >
          <div className="pd-shelf-books">
            {row.map((p) => (
              <div className="pd-shelf-slot" key={p.id} role="listitem">
                <Book paper={p} folders={folders} actions={actions} />
              </div>
            ))}
          </div>
          <div className="pd-shelf-plank" aria-hidden="true" />
          <div className="pd-shelf-labels">
            {row.map((p) => (
              <div className="pd-shelf-slot" key={p.id}>
                <Label paper={p} onOpen={actions.onOpen} />
              </div>
            ))}
          </div>
        </section>
      ))}
    </div>
  )
}

/** 书架：封面是论文第一页的缩略图，立在隔板上；每个文件夹是一层，点封面打开这篇论文的详情页。 */
export function Bookshelf({ papers, folders, grouped, actions, readingMode = false }: { papers: PaperSummary[]; folders: FolderItem[]; grouped: boolean; actions: BookshelfActions; readingMode?: boolean }) {
  const sections = useMemo(() => (readingMode ? readingSectionsFor(papers, grouped) : sectionsFor(papers, folders, grouped)), [papers, folders, grouped, readingMode])
  const [closed, setClosed] = useState<ReadonlySet<string>>(new Set())
  const toggle = (k: string): void =>
    setClosed((s) => {
      const n = new Set(s)
      if (n.has(k)) n.delete(k)
      else n.add(k)
      return n
    })
  return (
    <div className="pd-shelf" role="list" aria-label="书架">
      {sections.map((s) => {
        const shut = closed.has(s.key)
        return (
          <div className="pd-shelf-section" key={s.key}>
            {s.title && (
              <button type="button" className="pd-shelf-head" onClick={() => toggle(s.key)} aria-expanded={!shut}>
                {shut ? <ChevronRight size={14} /> : <ChevronDown size={14} />}
                <span className="pd-shelf-tab">{s.title}</span>
                <span className="pd-shelf-count">{s.papers.length} 篇</span>
              </button>
            )}
            {!shut && <Shelf papers={s.papers} folders={folders} actions={actions} />}
          </div>
        )
      })}
    </div>
  )
}
