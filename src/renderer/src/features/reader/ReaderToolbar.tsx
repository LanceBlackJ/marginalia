import { useEffect, useRef, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { useBack } from '@/lib/useBack'
import {
  ArrowLeft,
  BookmarkPlus,
  ChevronDown,
  Code2,
  ExternalLink,
  FileText,
  Info,
  RefreshCw,
  Keyboard,
  Languages,
  Maximize2,
  Minus,
  MoreHorizontal,
  Network,
  NotebookPen,
  PanelLeft,
  PanelRight,
  Plus,
  Quote,
  Search,
  Star,
  Type
} from 'lucide-react'
import { Button, Dialog, IconButton, Menu, Segmented, type MenuItem } from '@/components/ui'
import { api } from '@/lib/api'
import { codeApi } from '@/lib/api.code'
import { toast } from '@/components/ui'
import { errorMessage } from '@/lib/api'
import { openExternal } from '@/lib/native'
import { formatAuthors } from '@/lib/format'
import { useNotesStore } from '@/features/notes/notesStore'
import { CiteDialog } from '@/features/library/CiteDialog'
import { ReparseDialog } from '@/features/library/ReparseDialog'
import { reparsePapers, undoReparse } from '@/features/library/reparseActions'
import { usePrefs } from '@/stores/prefsStore'
import { canReparse, needsReparse } from '@/features/library/reparseModel'
import { buildPlain, type ReadView } from '@/features/text/textModel'
import { useTextStore } from '@/features/text/textStore'
import { AppearancePanel } from './AppearancePanel'
import { useMarksStore } from './marksStore'
import { useReaderStore } from './readerStore'
import type { ViewMode } from './pdf/layout'

const ZOOM_PRESETS = [0.5, 0.75, 1, 1.25, 1.5, 2]
const MODE_LABELS: Record<ViewMode, string> = { continuous: '连续滚动', single: '单页', double: '双页' }

function PageControl() {
  const currentPage = useReaderStore((s) => s.currentPage)
  const numPages = useReaderStore((s) => s.numPages)
  const jumpToPage = useReaderStore((s) => s.jumpToPage)
  const [text, setText] = useState(String(currentPage))

  useEffect(() => setText(String(currentPage)), [currentPage])

  const commit = (): void => {
    const n = Number.parseInt(text, 10)
    if (Number.isFinite(n)) jumpToPage(n)
    else setText(String(currentPage))
  }
  return (
    <div className="pd-row" style={{ gap: 6 }}>
      <input
        className="pd-input pd-page-input"
        inputMode="numeric"
        aria-label="页码"
        value={text}
        onChange={(e) => setText(e.target.value.replace(/[^\d]/g, ''))}
        onKeyDown={(e) => {
          if (e.key === 'Enter') {
            commit()
            e.currentTarget.blur()
          }
          if (e.key === 'Escape') e.currentTarget.blur()
        }}
        onBlur={commit}
        onFocus={(e) => e.currentTarget.select()}
      />
      <span className="pd-muted pd-aux" style={{ whiteSpace: 'nowrap' }}>
        / {numPages || '—'}
      </span>
    </div>
  )
}

function ZoomControl() {
  const scale = useReaderStore((s) => s.scale)
  const zoomMode = useReaderStore((s) => s.zoomMode)
  const setZoom = useReaderStore((s) => s.setZoom)
  const zoomBy = useReaderStore((s) => s.zoomBy)
  const label = `${Math.round(scale * 100)}%`
  const items: MenuItem[] = [
    ...ZOOM_PRESETS.map((z) => ({
      key: String(z),
      label: `${Math.round(z * 100)}%`,
      hint: zoomMode === 'custom' && Math.abs(scale - z) < 0.005 ? '当前' : undefined,
      onSelect: () => setZoom('custom', z)
    })),
    { key: 'sep', separator: true },
    { key: 'fit-width', label: '适合宽度', hint: zoomMode === 'fit-width' ? '当前' : undefined, onSelect: () => setZoom('fit-width') },
    { key: 'fit-page', label: '适合页面', hint: zoomMode === 'fit-page' ? '当前' : undefined, onSelect: () => setZoom('fit-page') }
  ]
  return (
    <div className="pd-row" style={{ gap: 2 }}>
      <IconButton icon={<Minus size={16} />} label="缩小" onClick={() => zoomBy(-1)} />
      <Menu
        trigger={({ toggle, open }) => (
          <Button variant="ghost" size="sm" className="pd-zoom-btn" onClick={toggle} aria-haspopup="menu" aria-expanded={open} aria-label="缩放比例">
            <span>{zoomMode === 'fit-width' ? `宽度 ${label}` : zoomMode === 'fit-page' ? `页面 ${label}` : label}</span>
            <ChevronDown size={13} />
          </Button>
        )}
        items={items}
      />
      <IconButton icon={<Plus size={16} />} label="放大" onClick={() => zoomBy(1)} />
    </div>
  )
}

function ModeControl() {
  const mode = useReaderStore((s) => s.mode)
  const setMode = useReaderStore((s) => s.setMode)
  return (
    <Menu
      trigger={({ toggle, open }) => (
        <Button variant="ghost" size="sm" onClick={toggle} aria-haspopup="menu" aria-expanded={open} aria-label="阅读模式" icon={<FileText size={14} />}>
          {MODE_LABELS[mode]}
        </Button>
      )}
      items={(Object.keys(MODE_LABELS) as ViewMode[]).map((m) => ({
        key: m,
        label: MODE_LABELS[m],
        hint: m === mode ? '当前' : undefined,
        onSelect: () => setMode(m)
      }))}
    />
  )
}

/** 主阅读界面切换：文字（默认）｜对照｜译文｜PDF。 */
function ReadViewControl() {
  const readView = useReaderStore((s) => s.readView)
  const setReadView = useReaderStore((s) => s.setReadView)
  const noText = useReaderStore((s) => s.noText)
  const reason = '这篇论文没有可提取的文字层，可能是扫描版'
  return (
    <Segmented<ReadView>
      aria-label="阅读界面"
      value={readView}
      onChange={setReadView}
      options={[
        { value: 'text', label: '文字', disabled: noText, disabledReason: reason },
        { value: 'both', label: '对照', disabled: noText, disabledReason: reason },
        { value: 'target', label: '译文', disabled: noText, disabledReason: reason },
        { value: 'pdf', label: 'PDF' },
        { value: 'split', label: '并排', disabled: noText, disabledReason: reason }
      ]}
    />
  )
}

function InfoDialog({ open, onClose }: { open: boolean; onClose: () => void }) {
  const paper = useReaderStore((s) => s.paper)
  if (!paper) return null
  const rows: Array<[string, string | null]> = [
    ['作者', paper.authors.length ? formatAuthors(paper.authors, 12) : null],
    ['期刊 / 会议', paper.venue],
    ['年份', paper.year ? String(paper.year) : null],
    ['DOI', paper.doi],
    ['arXiv', paper.arxiv_id],
    ['页数', paper.page_count ? String(paper.page_count) : null],
    ['语言', paper.language],
    ['元数据来源', paper.metadata_source],
    ['来源文件', paper.original_path],
    ['来源链接', paper.source_url]
  ]
  return (
    <Dialog open={open} onClose={onClose} title="论文信息" width={560} footer={<Button onClick={onClose}>关闭</Button>}>
      <p style={{ fontWeight: 600, marginBottom: 12 }}>{paper.title}</p>
      <dl className="pd-kv" style={{ gridTemplateColumns: '96px 1fr' }}>
        {rows
          .filter(([, v]) => v)
          .map(([k, v]) => (
            <div key={k} style={{ display: 'contents' }}>
              <dt>{k}</dt>
              <dd>{v}</dd>
            </div>
          ))}
      </dl>
      {paper.abstract && (
        <>
          <p className="pd-field-label" style={{ margin: '16px 0 6px' }}>
            摘要
          </p>
          <p className="pd-muted" style={{ lineHeight: 1.65 }}>
            {paper.abstract}
          </p>
        </>
      )}
    </Dialog>
  )
}

export function ReaderToolbar() {
  const navigate = useNavigate()
  const paper = useReaderStore((s) => s.paper)
  const back = useBack(paper ? `/paper/${paper.id}` : '/library')
  const patchPaper = useReaderStore((s) => s.patchPaper)
  const leftOpen = useReaderStore((s) => s.leftOpen)
  const rightOpen = useReaderStore((s) => s.rightOpen)
  const searchOpen = useReaderStore((s) => s.searchOpen)
  const setLeft = useReaderStore((s) => s.setLeft)
  const setRight = useReaderStore((s) => s.setRight)
  const setSearchOpen = useReaderStore((s) => s.setSearchOpen)
  const readView = useReaderStore((s) => s.readView)
  const noText = useReaderStore((s) => s.noText)
  const setReadView = useReaderStore((s) => s.setReadView)
  const appearanceOpen = useReaderStore((s) => s.appearanceOpen)
  const setAppearanceOpen = useReaderStore((s) => s.setAppearanceOpen)
  const setFocus = useReaderStore((s) => s.setFocus)
  const setHelpOpen = useReaderStore((s) => s.setHelpOpen)
  const startDraft = useNotesStore((s) => s.startDraft)
  const [infoOpen, setInfoOpen] = useState(false)
  const [citeOpen, setCiteOpen] = useState(false)
  const [reparseOpen, setReparseOpen] = useState(false)
  const [canUndo, setCanUndo] = useState(false)
  const paperIdForUndo = paper?.id
  const [codeLinks, setCodeLinks] = useState(0)
  // 论文原文里写明的代码仓库链接：有的话在菜单里提示，用户就知道可以直接去获取
  useEffect(() => {
    let alive = true
    setCodeLinks(0)
    if (paperIdForUndo)
      codeApi
        .sources(paperIdForUndo)
        .then((r) => alive && setCodeLinks(r.items.filter((i) => i.supported).length))
        .catch(() => undefined)
    return () => {
      alive = false
    }
  }, [paperIdForUndo])
  const refreshUndo = (id: string): void => {
    api.papers
      .reparseStatus(id)
      .then((s) => setCanUndo(!!s.backup))
      .catch(() => setCanUndo(false))
  }
  useEffect(() => {
    if (paperIdForUndo) refreshUndo(paperIdForUndo)
    else setCanUndo(false)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [paperIdForUndo])

  // 有新版解析的论文：打开时自动重新解析（后台进行、有备份、可撤销；设置里能关）。每次打开同一篇只自动试一次
  const autoReparse = usePrefs((s) => s.autoReparse)
  const autoTried = useRef<string | null>(null)
  const outdated = !!paper && needsReparse(paper)
  useEffect(() => {
    if (!paper || !outdated || !autoReparse || autoTried.current === paper.id) return
    autoTried.current = paper.id
    void reparsePapers([paper.id], {
      onDone: (id) => {
        const st = useReaderStore.getState()
        if (st.paperId !== id) return
        st.closePaper()
        void st.openPaper(id)
        refreshUndo(id)
      }
    })
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [paper?.id, outdated, autoReparse])

  if (!paper) return null

  const toggleFavorite = async (): Promise<void> => {
    const next = !paper.favorite
    patchPaper({ favorite: next })
    try {
      await api.papers.patch(paper.id, { favorite: next })
    } catch (err) {
      patchPaper({ favorite: !next })
      toast.error('收藏失败', errorMessage(err))
    }
  }

  /** 全文翻译：切到对照界面并开始翻译，译文边翻边出现。 */
  const translateAll = (): void => {
    setReadView('both')
    void useTextStore.getState().startTranslation()
  }

  /** 当前界面的文字（PDF 界面按“文字”导出）。 */
  const exportText = (): string => {
    const t = useTextStore.getState()
    const view = readView === 'pdf' || readView === 'split' ? 'text' : readView
    return buildPlain(t.items, t.trans, view, { showNoise: false })
  }
  const copyText = async (): Promise<void> => {
    try {
      await navigator.clipboard.writeText(exportText())
      toast.success('已复制', '当前界面的论文文字已复制到剪贴板')
    } catch {
      toast.error('复制失败', '没有剪贴板权限')
    }
  }
  const saveText = (): void => {
    const blob = new Blob([exportText()], { type: 'text/plain;charset=utf-8' })
    const url = URL.createObjectURL(blob)
    const a = document.createElement('a')
    a.href = url
    a.download = `${(paper.title || '论文').slice(0, 60).replace(/[\/:*?"<>|]/g, ' ')}${readView === 'both' ? '（对照）' : readView === 'target' ? '（译文）' : ''}.txt`
    a.click()
    URL.revokeObjectURL(url)
  }

  const newNote = (): void => {
    setRight(true, 'notes')
    if (!useNotesStore.getState().draft) startDraft({ page: useReaderStore.getState().currentPage })
  }

  const addBookmark = (): void => {
    const st = useReaderStore.getState()
    const section = st.currentSectionTitle()
    void useMarksStore.getState().addBookmark(st.currentPage, section ? `${section}` : `第 ${st.currentPage} 页`)
  }

  /** 重新解析（或撤销）完成后重新载入这篇论文：文字、目录、图表都换成新的。 */
  const reloadPaper = (id: string): void => {
    const st = useReaderStore.getState()
    if (st.paperId !== id) return
    st.closePaper()
    void st.openPaper(id)
    refreshUndo(id)
  }

  const moreItems: MenuItem[] = [
    { key: 'toc', label: leftOpen ? '收起目录 / 缩略图 / 书签' : '展开目录 / 缩略图 / 书签', icon: <PanelLeft size={15} />, onSelect: () => setLeft(!leftOpen) },
    { key: 'focus', label: '专注模式', hint: 'F', icon: <Maximize2 size={15} />, onSelect: () => setFocus(true) },
    { key: 'sep1', separator: true },
    { key: 'mindmap', label: '思维导图工作区', icon: <Network size={15} />, onSelect: () => navigate(`/mindmap/${paper.id}`) },
    { key: 'code', label: '代码工作区', hint: codeLinks > 0 ? '论文里有代码链接' : undefined, icon: <Code2 size={15} />, onSelect: () => navigate(`/code/${paper.id}`) },
    { key: 'sep2', separator: true },
    ...(noText
      ? []
      : [
          { key: 'copy', label: '复制论文文字', icon: <FileText size={15} />, onSelect: () => void copyText() },
          { key: 'save', label: '保存为 .txt', icon: <FileText size={15} />, onSelect: saveText }
        ]),
    { key: 'bookmark', label: '为当前页添加书签', icon: <BookmarkPlus size={15} />, onSelect: addBookmark },
    { key: 'cite', label: '生成引用', icon: <Quote size={15} />, onSelect: () => setCiteOpen(true) },
    { key: 'info', label: '论文信息', icon: <Info size={15} />, onSelect: () => setInfoOpen(true) },
    ...(canReparse(paper)
      ? [{ key: 'reparse', label: '重新解析这篇论文…', hint: needsReparse(paper) ? '有新版解析' : undefined, icon: <RefreshCw size={15} />, onSelect: () => setReparseOpen(true) }]
      : []),
    ...(canUndo ? [{ key: 'undo-reparse', label: '撤销上次重新解析', icon: <RefreshCw size={15} />, onSelect: () => void undoReparse(paper.id, reloadPaper) }] : []),
    ...(paper.doi ? [{ key: 'doi', label: '在浏览器中打开 DOI', icon: <ExternalLink size={15} />, onSelect: () => void openExternal(`https://doi.org/${paper.doi}`) }] : []),
    { key: 'sep3', separator: true },
    { key: 'help', label: '快捷键', hint: '?', icon: <Keyboard size={15} />, onSelect: () => setHelpOpen(true) }
  ]

  return (
    <header className="pd-reader-bar">
      <div className="pd-reader-bar-left">
        <IconButton icon={<ArrowLeft size={18} />} label="返回" onClick={back} />
        <div className="pd-reader-title pd-ellipsis" title={paper.title}>
          {paper.title}
        </div>
        <IconButton
          icon={<Star size={16} fill={paper.favorite ? 'currentColor' : 'none'} />}
          label={paper.favorite ? '取消收藏' : '收藏'}
          onClick={() => void toggleFavorite()}
        />
      </div>

      <div className="pd-reader-bar-center">
        <ReadViewControl />
        {(readView === 'pdf' || readView === 'split') && (
          <>
            <span className="pd-bar-sep" />
            <PageControl />
            <ZoomControl />
            {readView === 'pdf' && <ModeControl />}
          </>
        )}
      </div>

      <div className="pd-reader-bar-right">
        {/* 工具栏只有一个主按钮：全文翻译；其余都是图标，按“阅读 / 记录 / 外观 / 面板”分组 */}
        <Button size="sm" variant="primary" icon={<Languages size={14} />} onClick={translateAll} disabled={noText} title="把全文翻译成中文，并与原文对照">
          全文翻译
        </Button>
        <span className="pd-bar-sep" />
        <IconButton icon={<Search size={17} />} label="在论文中搜索 · Ctrl+F" selected={searchOpen} onClick={() => setSearchOpen(!searchOpen)} />
        <IconButton icon={<NotebookPen size={17} />} label="写一条笔记" onClick={newNote} />
        <span className="pd-bar-sep" />
        <span data-appearance-trigger="">
          <IconButton icon={<Type size={17} />} label="阅读外观 · A" selected={appearanceOpen} onClick={() => setAppearanceOpen(!appearanceOpen)} />
        </span>
        <IconButton icon={<PanelRight size={17} />} label="AI 面板" selected={rightOpen} onClick={() => setRight(!rightOpen)} />
        <Menu align="end" trigger={({ toggle, open }) => <IconButton icon={<MoreHorizontal size={17} />} label="更多" selected={open} onClick={toggle} />} items={moreItems} />
      </div>
      <AppearancePanel open={appearanceOpen} onClose={() => setAppearanceOpen(false)} />
      <InfoDialog open={infoOpen} onClose={() => setInfoOpen(false)} />
      <CiteDialog paperId={citeOpen ? paper.id : null} title={paper.title} onClose={() => setCiteOpen(false)} />
      <ReparseDialog
        open={reparseOpen}
        count={1}
        onClose={() => setReparseOpen(false)}
        onConfirm={() => {
          setReparseOpen(false)
          void reparsePapers([paper.id], { onDone: reloadPaper })
        }}
      />
    </header>
  )
}
