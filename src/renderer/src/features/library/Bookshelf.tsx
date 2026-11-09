import { useEffect, useMemo, useRef, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { ChevronDown, ChevronRight, FolderInput, MoreHorizontal, Star, Tag as TagIcon } from 'lucide-react'
import { useContextMenu } from '@/components/ui'
import { api } from '@/lib/api'
import { formatAuthors } from '@/lib/format'
import { paperName } from '@/lib/paperName'
import type { PaperSummary } from '@/lib/types'
import type { FolderItem } from '@/lib/types.library'
import { useBlobUrl } from '@/features/figures/useBlobUrl'
import { FolderQuickMenu } from './FolderQuickMenu'
import { paperMenuItems, type PaperItemActions } from './PaperItems'
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
    <div className="pd-book" ref={ref} onContextMenu={(e) => cm.open(e, items)}>
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
        <section className="pd-shelf-row" key={ri} role="presentation">
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
export function Bookshelf({ papers, folders, grouped, actions }: { papers: PaperSummary[]; folders: FolderItem[]; grouped: boolean; actions: BookshelfActions }) {
  const sections = useMemo(() => sectionsFor(papers, folders, grouped), [papers, folders, grouped])
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
