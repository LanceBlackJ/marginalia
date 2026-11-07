import { useEffect, useState } from 'react'
import { useNavigate, useParams } from 'react-router-dom'
import { AlertCircle, ArrowLeft, FileX } from 'lucide-react'
import { Button, EmptyState, Spinner } from '@/components/ui'
import { api, errorMessage } from '@/lib/api'
import { useInsightStore } from '@/features/insight/insightStore'
import { takePendingJump } from '@/features/materials/crossJump'
import { SupplementAuthBar } from '@/features/materials/SupplementAuthBar'
import { useMaterialsStore } from '@/features/materials/materialsStore'
import { useNotesStore } from '@/features/notes/notesStore'
import { AnnotationPopover, TranslatePopover } from '@/features/reader/FloatingPopovers'
import { PdfViewer } from '@/features/reader/pdf/PdfViewer'
import { loadPdfDocument, type PDFDocumentProxy } from '@/features/reader/pdf/pdfjs'
import { ReaderSidebar } from '@/features/reader/ReaderSidebar'
import { ReaderToolbar } from '@/features/reader/ReaderToolbar'
import { RightPanel } from '@/features/reader/RightPanel'
import { RightRail } from '@/features/reader/RightRail'
import { SearchPanel } from '@/features/reader/SearchPanel'
import { SelectionToolbar } from '@/features/reader/SelectionToolbar'
import { useGlossaryStore } from '@/features/glossary/glossaryStore'
import { ParaInsightCard, ParaSpot } from '@/features/paragraph/ParaInsightCard'
import { useParaStore } from '@/features/paragraph/paraStore'
import { TextReader } from '@/features/text/TextReader'
import { ReadProgress } from '@/features/reader/ReadProgress'
import { ShortcutHelp } from '@/features/reader/ShortcutHelp'
import { SplitView } from '@/features/reader/SplitView'
import { useReaderShortcuts } from '@/features/reader/useReaderShortcuts'
import { useMediaQuery } from '@/lib/hooks'
import { useTextStore } from '@/features/text/textStore'
import { useMarksStore } from '@/features/reader/marksStore'
import { useReaderStore } from '@/features/reader/readerStore'
import { useSearchStore } from '@/features/reader/searchStore'
import { useTranslateStore } from '@/features/reader/translateStore'
import { useReadingPersistence } from '@/features/reader/useReadingPersistence'
import '@/features/reader/reader.css'
import '@/features/insight/insight.css'
import '@/features/materials/materials.css'

export function ReaderPage() {
  const { paperId } = useParams()
  const navigate = useNavigate()
  const loadState = useReaderStore((s) => s.loadState)
  const loadError = useReaderStore((s) => s.loadError)
  const paper = useReaderStore((s) => s.paper)
  const leftOpen = useReaderStore((s) => s.leftOpen)
  const rightOpen = useReaderStore((s) => s.rightOpen)
  const readView = useReaderStore((s) => s.readView)
  // 换论文、离开阅读页：打开着的“这一段怎么读”一并关掉（段落序号只在这篇论文里有意义）
  useEffect(() => () => useParaStore.getState().close(), [paperId])
  // 术语表：导入时已在后台做好；还没有的话这里让后台补做
  useEffect(() => {
    if (!paperId) return
    void useGlossaryStore.getState().load(paperId)
    return () => useGlossaryStore.getState().reset()
  }, [paperId])
  const leftTab = useReaderStore((s) => s.leftTab)
  const noText = useReaderStore((s) => s.noText)
  const focus = useReaderStore((s) => s.focus)
  const [peek, setPeek] = useState(false)
  const narrow = useMediaQuery('(max-width: 1099px)')
  const textStatus = useTextStore((s) => s.status)
  const textCount = useTextStore((s) => s.items.length)
  const [doc, setDoc] = useState<PDFDocumentProxy | null>(null)
  // PDF 只在需要时才加载（看 PDF 模式、或打开缩略图）；加载过就保留，来回切换不重复解析
  const [pdfWanted, setPdfWanted] = useState(false)
  const [pdfError, setPdfError] = useState<string | null>(null)

  // 打开 / 切换论文：加载元数据、批注、书签、笔记、研究卡片
  useEffect(() => {
    if (!paperId) return
    setDoc(null)
    setPdfWanted(false)
    setPdfError(null)
    void useReaderStore.getState().openPaper(paperId)
    void useTextStore.getState().load(paperId)
    void useMarksStore.getState().load(paperId)
    void useNotesStore.getState().load(paperId)
    void useInsightStore.getState().load(paperId)
    return () => {
      useTextStore.getState().reset()
      useTranslateStore.getState().close()
      useSearchStore.getState().clear()
      useInsightStore.getState().reset()
      useNotesStore.getState().reset()
      useMarksStore.getState().reset()
      useReaderStore.getState().closePaper()
    }
  }, [paperId])

  // 补充材料：只有主论文才有“材料”标签；每次打开都重新询问授权
  const paperRole = paper?.role ?? 'paper'
  const loadedPaperId = paper?.id
  useEffect(() => {
    if (!loadedPaperId || paperRole === 'supplement') return
    void useMaterialsStore.getState().load(loadedPaperId)
    return () => useMaterialsStore.getState().reset()
  }, [loadedPaperId, paperRole])

  // 加载 PDF 字节并交给 pdf.js
  const readable = loadState === 'ready' && paper?.status === 'ready' && paper.has_pdf
  const needPdf = readView === 'pdf' || readView === 'split' || (leftOpen && leftTab === 'thumbnails')
  useEffect(() => {
    if (needPdf) setPdfWanted(true)
  }, [needPdf])

  // 没有文字层（扫描版）：文字阅读器没有内容可显示，退回 PDF 并说明原因
  useEffect(() => {
    if (textStatus === 'ready' && textCount === 0 && readable) {
      useReaderStore.getState().setNoText(true)
      if (useReaderStore.getState().readView !== 'pdf') useReaderStore.getState().setReadView('pdf')
    }
  }, [textStatus, textCount, readable])

  // 从 [S1P12] 之类的引用跳转而来：打开后定位到被引用的位置（文字和 PDF 两种界面都能定位）
  useEffect(() => {
    if (!paperId || !readable) return
    const pending = takePendingJump(paperId)
    if (pending) {
      if (pending.bbox) useReaderStore.getState().jumpToEvidence({ page: pending.page, bbox: pending.bbox })
      else useReaderStore.getState().jumpToPage(pending.page)
    }
  }, [paperId, readable])

  useEffect(() => {
    if (!paperId || !readable || !pdfWanted) return
    const ctrl = new AbortController()
    let loaded: PDFDocumentProxy | null = null
    void (async () => {
      try {
        const bytes = await api.papers.file(paperId, ctrl.signal)
        const d = await loadPdfDocument(bytes)
        if (ctrl.signal.aborted) {
          void d.loadingTask.destroy()
          return
        }
        loaded = d
        useReaderStore.getState().setNumPages(d.numPages)
        setDoc(d)
      } catch (err) {
        if (!ctrl.signal.aborted) setPdfError(errorMessage(err))
      }
    })()
    return () => {
      ctrl.abort()
      if (loaded) void loaded.loadingTask.destroy()
    }
  }, [paperId, readable, pdfWanted])

  useReadingPersistence(paperId, readable)
  useReaderShortcuts(readable)

  // Ctrl+F：论文内搜索
  useEffect(() => {
    const onKey = (e: KeyboardEvent): void => {
      if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === 'f') {
        e.preventDefault()
        useReaderStore.getState().setSearchOpen(true)
      }
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [])

  if (loadState === 'error') {
    return (
      <div className="pd-gate">
        <EmptyState
          tone="danger"
          icon={<AlertCircle size={22} />}
          title="无法打开这篇论文"
          description={loadError}
          action={<Button onClick={() => navigate('/library')}>返回文献库</Button>}
        />
      </div>
    )
  }
  if (loadState !== 'ready' || !paper) {
    return (
      <div className="pd-gate">
        <EmptyState icon={<Spinner size={20} />} title="正在打开论文…" />
      </div>
    )
  }
  if (!readable) {
    return (
      <div className="pd-gate">
        <EmptyState
          icon={<FileX size={22} />}
          title={paper.status === 'parsing' ? '论文仍在解析中' : '这篇论文没有可阅读的 PDF'}
          description={paper.status === 'parsing' ? '解析完成后即可阅读，稍后再回来看看。' : (paper.parse_error ?? '请回到文献库，为它附加 PDF。')}
          action={<Button onClick={() => navigate('/library')}>返回文献库</Button>}
        />
      </div>
    )
  }

  // ready 的论文也可能带提示（例如扫描版无文字层），当作提示而不是错误
  const notice = paper.parse_error

  // PDF 栏：PDF 界面和并排视图共用
  const pdfNode = doc ? (
    <>
      {noText && (
        <div className="pd-jobbar pd-jobbar--note" role="note">
          <span>这篇论文没有可提取的文字层，只能显示 PDF。</span>
        </div>
      )}
      <PdfViewer key={paperId} doc={doc} />
    </>
  ) : (
    <div className="pd-viewer-state">
      {pdfError ? (
        <EmptyState tone="danger" icon={<AlertCircle size={22} />} title="PDF 渲染失败" description={pdfError} action={<Button onClick={() => navigate('/library')}>返回文献库</Button>} />
      ) : (
        <EmptyState icon={<Spinner size={20} />} title="正在加载 PDF…" />
      )}
    </div>
  )

  return (
    <div className={`pd-reader${focus ? ' pd-reader--focus' : ''}`}>
      {focus && <div className="pd-reader-hot" onMouseEnter={() => setPeek(true)} aria-hidden />}
      <div className="pd-reader-barwrap" data-peek={peek} onMouseLeave={() => setPeek(false)}>
        <ReaderToolbar />
      </div>
      {paper.role === 'supplement' && (
        <div className="pd-supp-parent" role="note">
          <span>
            补充材料 · 属于《<b>{paper.parent_title ?? '主论文'}</b>》
          </span>
          {paper.parent_id && (
            <Button size="sm" variant="ghost" icon={<ArrowLeft size={13} />} onClick={() => navigate(`/reader/${paper.parent_id}`)}>
              返回主论文
            </Button>
          )}
        </div>
      )}
      <SupplementAuthBar />
      {notice && (
        <div className="pd-jobbar pd-jobbar--note" role="note">
          <span>{notice}</span>
        </div>
      )}
      <div className="pd-reader-body">
        {leftOpen && !focus && <ReaderSidebar doc={doc} />}
        <div className="pd-reader-center">
          {readView === 'pdf' ? (
            pdfNode
          ) : readView === 'split' ? (
            <SplitView
              pdf={pdfNode}
              note={
                narrow ? (
                  <div className="pd-jobbar pd-jobbar--note" role="note">
                    <span>窗口较窄，并排会很挤；建议放宽窗口，或改用单栏阅读。</span>
                    <Button size="sm" variant="ghost" onClick={() => useReaderStore.getState().setReadView('text')}>
                      改用文字单栏
                    </Button>
                  </div>
                ) : null
              }
            />
          ) : (
            <TextReader />
          )}
          <ReadProgress />
          <SearchPanel />
        </div>
        {rightOpen && !focus && <RightPanel />}
        {!focus && <RightRail />}
      </div>
      <ShortcutHelp />
      <SelectionToolbar />
      <ParaSpot />
      {readView === 'pdf' && <ParaInsightCard variant="float" />}
      <TranslatePopover />
      <AnnotationPopover />
    </div>
  )
}
