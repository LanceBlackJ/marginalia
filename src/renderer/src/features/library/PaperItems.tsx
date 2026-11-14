import { VenueHover } from '@/features/discovery/VenueHover'
import { processingCell } from './processingModel'
import { paperName } from '@/lib/paperName'
import type { DragEvent, KeyboardEvent } from 'react'
import { useNavigate } from 'react-router-dom'
import { BookOpen, CheckCircle2, Code2, Copy, Download, FolderInput, FolderClosed, MoreHorizontal, Network, Quote, RefreshCw, Star, Tag as TagIcon, Trash2 } from 'lucide-react'
import { IconButton, Menu, ProgressBar, Tag, toast, useContextMenu, type MenuItem } from '@/components/ui'
import { formatAuthors, formatPercent, formatRelativeTime } from '@/lib/format'
import { foldTags } from '@/lib/tags'
import type { PaperSummary, PaperTag } from '@/lib/types'
import type { MatchSnippet } from '@/lib/types.library'
import { DRAG_PAPERS, dragPaperIds, splitSnippet } from './filterModel'
import { canReparse, needsReparse, REPARSE_HINT, REPARSE_HINT_DETAIL } from './reparseModel'

export interface PaperItemActions {
  /** 点论文本身：打开它的详情页 */
  onOpen: (p: PaperSummary) => void
  /** 直接进阅读页（行菜单里的“打开阅读”）；不提供就和 onOpen 一样 */
  onRead?: (p: PaperSummary) => void
  onToggleFavorite: (p: PaperSummary) => void
  onDelete: (p: PaperSummary) => void
  onEditTags: (p: PaperSummary) => void
  onExport: (p: PaperSummary) => void
  /** 生成这篇论文的引用（行菜单） */
  onCite?: (p: PaperSummary) => void
  /** 移动到文件夹…（行菜单） */
  onMove?: (p: PaperSummary) => void
  /** 重新解析（行菜单；有新版解析时行上也有小提示） */
  onReparse?: (p: PaperSummary) => void
  /** 勾选：用于批量移动；不提供则不显示勾选框 */
  selected?: ReadonlySet<string>
  onToggleSelect?: (p: PaperSummary) => void
  /** 拖动调整顺序：把 dragIds 放到 target 的前面（after=false）或后面（after=true） */
  onReorder?: (dragIds: string[], target: PaperSummary, after: boolean) => void
  /** 标记 / 取消“已读完” */
  onToggleFinished?: (p: PaperSummary) => void
  /** 点标签：按这个标签筛选 */
  onPickTag?: (tag: PaperTag) => void
}

/** 复制适合粘贴到笔记、邮件或文献管理器的基础题录信息；不把内部状态和路径混进去。 */
export function paperInfoText(paper: PaperSummary): string {
  const lines = [`标题：${paper.title}`]
  if (paper.alias?.trim()) lines.push(`备注名：${paper.alias.trim()}`)
  if (paper.authors.length > 0) lines.push(`作者：${paper.authors.join('；')}`)
  if (paper.year !== null) lines.push(`年份：${paper.year}`)
  if (paper.venue || paper.source_folder) lines.push(`来源：${paper.venue ?? paper.source_folder}`)
  if (paper.doi) lines.push(`DOI：${paper.doi}`)
  return lines.join('\n')
}

export async function copyPaperInfo(paper: PaperSummary): Promise<void> {
  try {
    await navigator.clipboard.writeText(paperInfoText(paper))
    toast.success('已复制论文信息')
  } catch {
    toast.error('复制失败', '请检查剪贴板权限后重试')
  }
}

/** 拖拽论文（已勾选的会一起拖走）到左侧文件夹。 */
/** axis：行 / 卡片上下排列看鼠标的纵向位置（y），书架上书是横着排的看横向位置（x） */
export function dragHandlers(paper: PaperSummary, actions: PaperItemActions, axis: 'x' | 'y' = 'y') {
  if (!actions.onMove) return {}
  const reorder = actions.onReorder
  const clear = (el: HTMLElement): void => {
    delete el.dataset.dropAt
  }
  return {
    draggable: true,
    // 拖到别的论文上：按鼠标在上半还是下半，插到它前面或后面（拖到左边文件夹上仍是“移动到文件夹”，不受影响）
    onDragOver: reorder
      ? (e: DragEvent<HTMLElement>) => {
          if (!Array.from(e.dataTransfer.types).includes(DRAG_PAPERS)) return
          e.preventDefault()
          e.dataTransfer.dropEffect = 'move'
          const r = e.currentTarget.getBoundingClientRect()
          e.currentTarget.dataset.dropAt = (axis === 'x' ? e.clientX > r.left + r.width / 2 : e.clientY > r.top + r.height / 2) ? 'after' : 'before'
        }
      : undefined,
    onDragLeave: reorder
      ? (e: DragEvent<HTMLElement>) => {
          if (!e.currentTarget.contains(e.relatedTarget as Node | null)) clear(e.currentTarget) // 划过里面的图片、按钮不算离开
        }
      : undefined,
    onDrop: reorder
      ? (e: DragEvent<HTMLElement>) => {
          const raw = e.dataTransfer.getData(DRAG_PAPERS)
          const after = e.currentTarget.dataset.dropAt === 'after'
          clear(e.currentTarget)
          if (!raw) return
          e.preventDefault()
          e.stopPropagation() // 已经按这本书处理了：别让外面一整行的接收再处理一遍
          try {
            const ids = JSON.parse(raw) as string[]
            if (ids.length > 0 && !ids.includes(paper.id)) reorder(ids, paper, after)
          } catch {
            /* 不是我们的拖拽数据 */
          }
        }
      : undefined,
    onDragEnd: (e: DragEvent<HTMLElement>) => {
      clear(e.currentTarget)
      delete e.currentTarget.dataset.dragging
      document.querySelectorAll<HTMLElement>('[data-drop-at]').forEach((el) => delete el.dataset.dropAt)
    },
    onDragStart: (e: DragEvent) => {
      const el = e.currentTarget as HTMLElement
      window.setTimeout(() => (el.dataset.dragging = '1'), 0) // 被拖的那个变淡（晚一拍，免得拖动时跟着的影像也是淡的）
      const ids = dragPaperIds(paper.id, actions.selected ?? new Set())
      e.dataTransfer.setData(DRAG_PAPERS, JSON.stringify(ids))
      e.dataTransfer.setData('text/plain', ids.length > 1 ? `${ids.length} 篇论文` : paper.title)
      e.dataTransfer.effectAllowed = 'move'
    }
  }
}

function SelectBox({ paper, actions }: { paper: PaperSummary; actions: PaperItemActions }) {
  if (!actions.onToggleSelect) return null
  const on = actions.selected?.has(paper.id) ?? false
  return (
    <input
      type="checkbox"
      className="pd-paper-check"
      checked={on}
      aria-label={`选择：${paperName(paper)}`}
      onClick={(e) => e.stopPropagation()}
      onChange={() => actions.onToggleSelect?.(paper)}
    />
  )
}

function Highlighted({ s }: { s: MatchSnippet }) {
  const [a, b, c] = splitSnippet(s.text, s.hit)
  return (
    <>
      {a}
      <mark className="pd-hit">{b}</mark>
      {c}
    </>
  )
}

/** 命中片段：搜笔记、批注、正文或 AI 卡片时，在论文下面显示命中的那一小段。 */
export function PaperMatches({ paper }: { paper: PaperSummary }) {
  const m = paper.matches
  if (!m) return null
  const groups: Array<[string, MatchSnippet[] | undefined]> = [
    ['笔记命中', m.notes],
    ['批注命中', m.annotations],
    ['正文命中', m.fulltext],
    ['AI 卡片命中', m.cards]
  ]
  return (
    <div className="pd-matches">
      {groups.map(([title, list]) =>
        (list ?? []).map((s, i) => (
          <div key={`${title}-${i}`} className="pd-match">
            <span className="pd-match-kind">{title}{s.page ? ` · 第 ${s.page} 页` : ''}</span>
            <span className="pd-match-text">
              <Highlighted s={s} />
            </span>
          </div>
        ))
      )}
    </div>
  )
}

/** “有新版解析，可重新解析”：安静的文字提示，点一下就重新解析（之后可撤销）。 */
function ReparseHint({ paper, actions }: { paper: PaperSummary; actions: PaperItemActions }) {
  if (!actions.onReparse || !needsReparse(paper)) return null
  return (
    <button
      type="button"
      className="pd-reparse-hint"
      title={REPARSE_HINT_DETAIL}
      onClick={(e) => {
        e.stopPropagation()
        actions.onReparse?.(paper)
      }}
    >
      <RefreshCw size={11} />
      {REPARSE_HINT}
    </button>
  )
}

/** 已读完的标记：黑底小角标 */
function FinishedBadge({ paper }: { paper: PaperSummary }) {
  if (!paper.finished_at) return null
  return (
    <span className="pd-finished-badge" title={`已读完 · ${paper.finished_at.slice(0, 10)}`}>
      <CheckCircle2 size={11} />
      已读完
    </span>
  )
}

function FolderBadge({ paper }: { paper: PaperSummary }) {
  if (!paper.folder_path) return null
  return (
    <span className="pd-folder-badge" title={`文件夹：${paper.folder_path}`}>
      <FolderClosed size={11} />
      {paper.folder_path}
    </span>
  )
}

/** 标签：黑白细边小标记；多于 3 个时折叠成 “+N”（完整列表在 title 里）。 */
export function PaperTags({ paper, onPick }: { paper: PaperSummary; onPick?: (tag: PaperTag) => void }) {
  const tags = paper.tags ?? []
  if (tags.length === 0) return null
  const { visible, rest } = foldTags(tags)
  return (
    <div className="pd-taglist pd-taglist--row">
      {visible.map((t) => (
        onPick ? (
          <button
            key={t.id}
            type="button"
            className="pd-ptag pd-ptag--btn"
            title={`只看标签“${t.name}”的论文`}
            onClick={(e) => {
              e.stopPropagation()
              onPick(t)
            }}
            onKeyDown={(e) => e.stopPropagation()}
          >
            {t.name}
          </button>
        ) : (
          <span key={t.id} className="pd-ptag">
            {t.name}
          </span>
        )
      ))}
      {rest.length > 0 && (
        <span className="pd-ptag pd-ptag--more" title={rest.map((t) => t.name).join('、')}>
          +{rest.length}
        </span>
      )}
    </div>
  )
}

/** 阅读进度 / 状态：进行中=黑色小圆点，失败、无全文=暗色文字，已读=2px 细线 + 百分比。 */
function ProgressCell({ paper }: { paper: PaperSummary }) {
  if (paper.status === 'parsing') {
    return (
      <span className="pd-status-text">
        <span className="pd-dot" />
        解析中
      </span>
    )
  }
  if (paper.status === 'failed') {
    return (
      <span className="pd-status-text pd-status-text--danger" title={paper.parse_error ?? undefined}>
        解析失败
      </span>
    )
  }
  if (paper.status === 'metadata_only') {
    return (
      <span className="pd-status-text pd-status-text--warning" title="没有开放获取全文，仅保存了元数据">
        无全文
      </span>
    )
  }
  if (paper.read_progress > 0) {
    return (
      <span className="pd-paper-progress" title={`阅读进度 ${formatPercent(paper.read_progress)}`}>
        <ProgressBar value={paper.read_progress} />
        <span>{formatPercent(paper.read_progress)}</span>
      </span>
    )
  }
  return <span className="pd-status-text pd-weak-text">未读</span>
}

function AnalysisCell({ paper }: { paper: PaperSummary }) {
  // 以导入后自动处理的整体进度为准：公式和表格没处理好之前不说“已完成/已分析”
  const cell = processingCell(paper.processing, paper.analysis_status)
  if (!cell) return <span className="pd-weak-text">—</span>
  return (
    <span title={cell.title}>
      <Tag tone={cell.tone === 'default' ? undefined : cell.tone}>{cell.text}</Tag>
    </span>
  )
}

function onKey(e: KeyboardEvent, fn: () => void): void {
  if (e.key === 'Enter' || e.key === ' ') {
    e.preventDefault()
    fn()
  }
}

export function paperMenuItems(paper: PaperSummary, actions: PaperItemActions, navigate: (to: string) => void): MenuItem[] {
  return [
    {
      key: 'open',
      label: '打开阅读',
      icon: <BookOpen size={15} />,
      disabled: paper.status !== 'ready',
      onSelect: () => (actions.onRead ?? actions.onOpen)(paper)
    },
    { key: 'copy-info', label: '复制论文信息', icon: <Copy size={15} />, onSelect: () => void copyPaperInfo(paper) },
    { key: 'mindmap', label: '思维导图工作区', icon: <Network size={15} />, disabled: paper.status !== 'ready', onSelect: () => navigate(`/mindmap/${paper.id}`) },
    { key: 'code', label: '代码工作区', icon: <Code2 size={15} />, disabled: paper.status === 'parsing', onSelect: () => navigate(`/code/${paper.id}`) },
    ...(actions.onMove ? [{ key: 'move', label: '移动到文件夹…', icon: <FolderInput size={15} />, onSelect: () => actions.onMove?.(paper) }] : []),
    ...(actions.onToggleFinished
      ? [{ key: 'finished', label: paper.finished_at ? '取消“已读完”标记' : '标记为已读完', icon: <CheckCircle2 size={15} />, onSelect: () => actions.onToggleFinished?.(paper) }]
      : []),
    { key: 'tags', label: '标签…', icon: <TagIcon size={15} />, onSelect: () => actions.onEditTags(paper) },
    ...(actions.onCite ? [{ key: 'cite', label: '生成引用…', icon: <Quote size={15} />, onSelect: () => actions.onCite?.(paper) }] : []),
    { key: 'export', label: '导出引文与笔记…', icon: <Download size={15} />, onSelect: () => actions.onExport(paper) },
    ...(actions.onReparse && canReparse(paper)
      ? [{ key: 'reparse', label: needsReparse(paper) ? '重新解析 · 有新版…' : '重新解析…', icon: <RefreshCw size={15} />, onSelect: () => actions.onReparse?.(paper) }]
      : []),
    { key: 'sep', separator: true },
    { key: 'delete', label: '从文献库删除…', icon: <Trash2 size={15} />, danger: true, onSelect: () => actions.onDelete(paper) }
  ]
}

function RowMenu({ paper, actions }: { paper: PaperSummary; actions: PaperItemActions }) {
  const navigate = useNavigate()
  return (
    <Menu
      align="end"
      trigger={({ toggle, open }) => (
        <IconButton
          icon={<MoreHorizontal size={16} />}
          label="更多操作"
          selected={open}
          onClick={(e) => {
            e.stopPropagation()
            toggle()
          }}
        />
      )}
      items={paperMenuItems(paper, actions, navigate)}
    />
  )
}

function FavoriteButton({ paper, actions }: { paper: PaperSummary; actions: PaperItemActions }) {
  return (
    <IconButton
      icon={<Star size={16} fill={paper.favorite ? 'currentColor' : 'none'} />}
      label={paper.favorite ? '取消收藏' : '收藏'}
      selected={paper.favorite}
      onClick={(e) => {
        e.stopPropagation()
        actions.onToggleFavorite(paper)
      }}
    />
  )
}

/** 表头：列随容器宽度显现（窄时依次收起来源、年份、最近打开、分析状态）。 */
export function PaperTableHeader() {
  return (
    <div className="pd-paper-row pd-paper-row--head" role="row">
      <span>标题 · 作者</span>
      <span className="pd-col-year">年份</span>
      <span className="pd-col-source">来源</span>
      <span>阅读进度</span>
      <span className="pd-col-analysis">分析</span>
      <span className="pd-col-time pd-col-time--head">最近打开</span>
      <span />
    </div>
  )
}

export function PaperRow({ paper, actions }: { paper: PaperSummary; actions: PaperItemActions }) {
  const source = paper.venue ?? paper.source_folder
  const navigate = useNavigate()
  const cm = useContextMenu()
  return (
    <div
      className={`pd-paper-row${actions.selected?.has(paper.id) ? ' is-selected' : ''}`}
      role="row"
      tabIndex={0}
      onClick={() => actions.onOpen(paper)}
      onKeyDown={(e) => onKey(e, () => actions.onOpen(paper))}
      onContextMenu={(e) => cm.open(e, paperMenuItems(paper, actions, navigate))}
      {...dragHandlers(paper, actions)}
    >
      {cm.element}
      <div className="pd-paper-main">
        <div className="pd-paper-titlerow">
          <SelectBox paper={paper} actions={actions} />
          <div className="pd-paper-title" title={paper.title}>
            {paperName(paper)}
          </div>
        </div>
        <div className="pd-paper-meta pd-ellipsis">
          {formatAuthors(paper.authors)}
          {paper.year && <span className="pd-meta-year"> · {paper.year}</span>}
          {source && <span className="pd-meta-source"> · {source}</span>}
          {!!paper.supplement_count && <span> · 补充材料 {paper.supplement_count}</span>}
        </div>
        <div className="pd-paper-tagrow">
          <FinishedBadge paper={paper} />
          <FolderBadge paper={paper} />
          <PaperTags paper={paper} onPick={actions.onPickTag} />
          <ReparseHint paper={paper} actions={actions} />
        </div>
        <PaperMatches paper={paper} />
      </div>
      <div className="pd-col pd-col-year">{paper.year ?? '—'}</div>
      <div className="pd-col pd-col-source pd-ellipsis" title={source ?? undefined}>
        {paper.venue ? (
          <span onClick={(e) => e.stopPropagation()} onKeyDown={(e) => e.stopPropagation()}>
            <VenueHover name={paper.venue}>{paper.venue}</VenueHover>
          </span>
        ) : (
          (source ?? '—')
        )}
      </div>
      <div className="pd-col">
        <ProgressCell paper={paper} />
      </div>
      <div className="pd-col pd-col-analysis">
        <AnalysisCell paper={paper} />
      </div>
      <div className="pd-col pd-col-time">{paper.last_opened_at ? formatRelativeTime(paper.last_opened_at) : '未打开'}</div>
      <div className="pd-paper-actions">
        <FavoriteButton paper={paper} actions={actions} />
        <RowMenu paper={paper} actions={actions} />
      </div>
    </div>
  )
}

export function PaperCard({ paper, actions }: { paper: PaperSummary; actions: PaperItemActions }) {
  const navigate = useNavigate()
  const cm = useContextMenu()
  const meta = [
    formatAuthors(paper.authors),
    paper.venue,
    paper.year ? String(paper.year) : null,
    paper.supplement_count ? `补充材料 ${paper.supplement_count}` : null
  ]
    .filter(Boolean)
    .join(' · ')
  return (
    <div
      className={`pd-paper-card${actions.selected?.has(paper.id) ? ' is-selected' : ''}`}
      role="button"
      tabIndex={0}
      onClick={() => actions.onOpen(paper)}
      onKeyDown={(e) => onKey(e, () => actions.onOpen(paper))}
      onContextMenu={(e) => cm.open(e, paperMenuItems(paper, actions, navigate))}
      {...dragHandlers(paper, actions)}
    >
      {cm.element}
      <div className="pd-paper-titlerow">
        <SelectBox paper={paper} actions={actions} />
        <div className="pd-paper-card-title" title={paper.title}>
          {paperName(paper)}
        </div>
      </div>
      <div className="pd-paper-meta pd-ellipsis">{meta}</div>
      <div className="pd-paper-tagrow">
        <FinishedBadge paper={paper} />
          <FolderBadge paper={paper} />
        <PaperTags paper={paper} onPick={actions.onPickTag} />
        <ReparseHint paper={paper} actions={actions} />
      </div>
      <PaperMatches paper={paper} />
      <div className="pd-paper-card-foot">
        <ProgressCell paper={paper} />
        <span className="pd-spacer" />
        <FavoriteButton paper={paper} actions={actions} />
        <RowMenu paper={paper} actions={actions} />
      </div>
    </div>
  )
}

/** 最近阅读：等宽条目，标题最多两行，作者年份，细进度线。 */
export function RecentItem({ paper, onOpen }: { paper: PaperSummary; onOpen: (p: PaperSummary) => void }) {
  const meta = [formatAuthors(paper.authors, 2), paper.year ? String(paper.year) : null].filter(Boolean).join(' · ')
  return (
    <button type="button" className="pd-recent-item" onClick={() => onOpen(paper)} title={paper.title}>
      <span className="pd-recent-title">{paperName(paper)}</span>
      <span className="pd-recent-meta">{meta}</span>
      <span className="pd-recent-foot">
        <ProgressBar value={paper.read_progress} />
        <span>
          {formatPercent(paper.read_progress)} · {formatRelativeTime(paper.last_opened_at)}
        </span>
      </span>
    </button>
  )
}
