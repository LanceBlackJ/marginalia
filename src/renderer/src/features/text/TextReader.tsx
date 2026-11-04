import { memo, useCallback, useEffect, useMemo, useRef, useState, type ReactElement } from 'react'
import { AlertCircle, ChevronDown, ChevronRight, Compass, Languages, StickyNote } from 'lucide-react'
import { toast } from '@/components/ui'
import { Button, Dialog, EmptyState, ProgressBar, Skeleton } from '@/components/ui'
import { Lightbox } from '@/components/ui/Lightbox'
import { NotePeek } from '@/features/notes/NotePeek'
import { selectionCollapseIsFromPanel } from '@/features/reader/selectionKeep'
import { useNotePeek } from '@/features/notes/notePeekStore'
import { useNotesStore } from '@/features/notes/notesStore'
import { NOTE_MARK_PREFIX, noteMarksByPage, noteOrdMap } from '@/features/notes/noteMarks'
import { useBlobUrl } from '@/features/figures/useBlobUrl'
import { Tex } from '@/features/formula/Tex'
import { explainEquation } from '@/features/reader/selectionActions'
import { useMarksStore } from '@/features/reader/marksStore'
import { useGlossaryStore } from '@/features/glossary/glossaryStore'
import { useReaderStore } from '@/features/reader/readerStore'
import { useSearchStore } from '@/features/reader/searchStore'
import { visionApi } from '@/lib/api.vision'
import type { Note } from '@/lib/types'
import { regionKindLabel, textApi, type FigureRegion, type RegionKind, type TableAnalysisItem, type TableTranslation, type TextItem } from '@/lib/api.text'
import { splitInlineMath } from './inlineMath'
import { misplacedCount, misplacedSet, numericColumns, tableToMarkdown, tableToTsv, unverifiedCount, unverifiedSet } from './tableModel'
import { useTextStore, type EquationInfo } from './textStore'
import { usePrefs } from '@/stores/prefsStore'
import { muteSync, syncMuted } from '@/features/reader/splitSync'
import { buildRegions, regionForOrd } from '@/features/reader/regions'
import { applyBold, applyMath, applyTerms, termRegExp, blockText, boldRanges, mathRanges, buildDoc, findPassageByBBox, pageAtLine, segmentsFor, type AnnMark, type Block, type Seg, type TextView } from './textModel'
import { buildVocab, cleanDisplay, parseCaption, parseCaptionZh, splitCitations, splitFirstSentence, splitFirstSentenceZh, splitTag, stripBroken, type Vocab } from './textClean'
import { FrontBlock } from './FrontBlock'
import { TocRail, activeSection, type TocItem, type TocTable } from './TocRail'
import { FigureDataBlock } from './FigureDataBlock'
import { FigLabelMenu } from './FigLabelMenu'
import { ParaInsightCard } from '@/features/paragraph/ParaInsightCard'
import { useParaStore } from '@/features/paragraph/paraStore'
import { FigAiDialog } from './FigAiDialog'
import { ZoomImage } from '@/components/ZoomImage'
import { LinkAnchor, Linkified } from './Linkified'
import { splitLinks } from './links'
import { labelKey, splitFigRefs } from './figRefs'
import { useFigViewer, type FigTarget } from './figViewerStore'
import { useTextPrefs } from './textPrefs'
import { readTextSelection } from './textSelection'
import { RefMarker, RefEntryHandlers } from './RefMarker'
import { RefLayer } from './RefLayer'
import { mergeParaRefs, splitByMarkers, splitLooseBrackets, splitSupDigits, type ParaRefs } from './refModel'
import { useRefStore } from './refStore'
import './text.css'

const READ_LINE = 90 // 阅读线：块的顶部越过这条线（距容器顶部的像素）就算“读到这一页”
const CHUNK = 24 // 每 24 个块为一组，用 content-visibility 只渲染可见部分（不按页分块）
const TOC_MIN_WIDTH = 980 // 阅读区至少这么宽才显示“本文结构”
const EMPTY_MARKS: AnnMark[] = []

/** 进入视口附近后才为 true（一次性）：图片等重内容只在快要看到时才加载。 */
function useNearViewport(ref: React.RefObject<HTMLElement | null>): boolean {
  const [near, setNear] = useState(false)
  useEffect(() => {
    const el = ref.current
    if (!el || near) return
    const io = new IntersectionObserver((e) => e.some((x) => x.isIntersecting) && setNear(true), { rootMargin: '900px' })
    io.observe(el)
    return () => io.disconnect()
  }, [ref, near])
  return near
}

/** 行内数学：只有高置信度的写法（希腊字母、带下划线的下标变量、O(…) 复杂度）渲染成 KaTeX；其余原样。 */
function withMath(text: string): ReactElement[] | string {
  const parts = splitInlineMath(text)
  if (parts.length === 1 && 'text' in parts[0]) return text
  return parts.map((m, i) => ('tex' in m ? <ClickTex key={i} tex={m.tex} /> : <span key={i}>{m.text}</span>))
}

/** 行内公式：点一下在页面中间放大显示（正在选中文字时不触发）。 */
function ClickTex({ tex }: { tex: string }) {
  const open = useFigViewer((s) => s.openFormula)
  return (
    <span
      className="pd-tp-imath-click"
      title="点击放大查看公式"
      onClick={() => {
        if (window.getSelection()?.toString()) return
        open(tex)
      }}
    >
      <Tex latex={tex} className="pd-tp-imath" />
    </span>
  )
}

/** 论文里存在的图、表：键 → 打开它所需的信息。按 Map 的身份缓存，不会每段文字都重算。 */
const targetCache = new WeakMap<object, Set<string>>()
function targetKeys(figs: Map<number, FigureRegion>, tables: Map<number, TableAnalysisItem>): Set<string> {
  let hit = targetCache.get(figs)
  if (hit && targetCache.get(tables) === hit) return hit
  const keys = new Set<string>()
  for (const f of figs.values()) {
    const k = f.kind === 'figure' ? labelKey(f.label) : null
    if (k) keys.add(k)
  }
  for (const t of tables.values()) {
    const k = t.kind === 'figure_data' ? null : labelKey(t.label)
    if (k) keys.add(k)
  }
  hit = keys
  targetCache.set(figs, hit)
  targetCache.set(tables, hit)
  return hit
}

/** 一段普通文字：里面的“Fig. 3”“Table 2”如果论文里真有这张图或表，就变成可点击的，点了打开它。 */
function FigText({ text, render }: { text: string; render: (t: string) => ReactElement[] | string }) {
  const figs = useTextStore((s) => s.figuresByCaption)
  const tables = useTextStore((s) => s.tablesByCaption)
  const keys = targetKeys(figs, tables)
  if (keys.size === 0) return <>{render(text)}</>
  const pieces = splitFigRefs(text, (k) => keys.has(k))
  if (pieces.length === 1 && !('key' in pieces[0])) return <>{render(text)}</>
  return (
    <>
      {pieces.map((p, i) =>
        'key' in p ? (
          <FigLabelMenu key={i} className="pd-tp-figref" target={{ kind: p.key.includes('table') ? 'table' : 'fig', key: p.key }}>
            {p.text}
          </FigLabelMenu>
        ) : (
          <span key={i}>{render(p.text)}</span>
        )
      )}
    </>
  )
}

/** 点了正文里的图表引用后弹出的查看器：图用放大镜头，识别好的表直接显示成表格，还没识别的表显示截图。 */
function FigViewer({ paperId }: { paperId: string }) {
  const target = useFigViewer((s) => s.target)
  const close = useFigViewer((s) => s.close)
  const formula = useFigViewer((s) => s.formula)
  const aiTarget = useFigViewer((s) => s.ai)
  const figs = useTextStore((s) => s.figuresByCaption)
  const tables = useTextStore((s) => s.tablesByCaption)
  const fig = target?.kind === 'fig' ? ([...figs.values()].find((f) => f.kind === 'figure' && labelKey(f.label) === target.key) ?? null) : null
  const table = target?.kind === 'table' ? ([...tables.values()].find((t) => t.kind !== 'figure_data' && labelKey(t.label) === target.key) ?? null) : null
  const aiFig = aiTarget?.kind === 'fig' ? ([...figs.values()].find((f) => f.kind === 'figure' && labelKey(f.label) === aiTarget.key) ?? null) : null
  const asImage = fig !== null || (table !== null && table.status !== 'ready')
  const img = useBlobUrl(
    !target ? null : fig ? () => textApi.regionVector(paperId, fig.page, fig.bbox, 220) : table && table.status !== 'ready' ? () => textApi.tableVector(paperId, table.caption_ord, 200) : null,
    [paperId, target?.key]
  )
  return (
    <>
      {formula && (
        <Dialog open onClose={close} title="公式" width={760}>
          <div style={{ overflowX: 'auto', fontSize: '1.5em', padding: '16px 0', textAlign: 'center' }}>
            <Tex latex={formula} display />
          </div>
        </Dialog>
      )}
      {aiFig && <FigAiDialog paperId={paperId} fig={aiFig} onClose={close} />}
      <Lightbox open={Boolean(target) && asImage} onClose={close} src={img.url} alt={fig?.label ?? table?.label ?? ''} />
      {table && table.status === 'ready' && (
        <Dialog open onClose={close} title={table.label} width={980}>
          <TableBlock paperId={paperId} t={table} running={false} runError={null} onRetry={() => undefined} />
        </Dialog>
      )}
    </>
  )
}

/** 普通文字片段：整理排版时把粘在词尾的引用数字渲染成上标，并把简单的行内数学渲染出来。 */
function plainPieces(text: string, vocab: Vocab | null, key: number, refs: ParaRefs | null = null): ReactElement {
  if (!vocab) {
    return (
      <span key={key}>
        <Linkified text={text} />
      </span>
    )
  }
  // 先按后端确认过的“按文字定位”的引用标记（[12]、refs 3、作者-年份）切开，其余再做上标与行内数学
  const exact = refs && refs.text.length > 0 ? splitByMarkers(text, refs.text) : [{ text, refs: undefined as number[] | undefined }]
  const loose = refs?.loose
  const parts = loose ? exact.flatMap((p) => (p.refs ? [p] : splitLooseBrackets(p.text, loose))) : exact
  // 链接要在引用角标和行内数学之前切出来：网址、DOI 里的数字不能被当成角标拆开
  const citePieces = (txt: string): ReactElement[] =>
    splitCitations(txt, vocab).map((p, i) => {
      if (!p.sup) return <span key={i}><FigText text={p.text} render={withMath} /></span>
      const sup = refs && refs.sup.size > 0 ? splitSupDigits(p.text, refs.sup) : []
      return (
        <sup key={i} className="pd-tp-cite">
          {sup.some((s) => s.refs)
            ? sup.map((s, j) =>
                s.refs ? (
                  <RefMarker key={j} refs={s.refs}>
                    {s.text}
                  </RefMarker>
                ) : (
                  <span key={j}>{s.text}</span>
                )
              )
            : p.text}
        </sup>
      )
    })
  return (
    <span key={key}>
      {parts.map((part, pi) =>
        part.refs ? (
          <RefMarker key={pi} refs={part.refs}>
            {part.text}
          </RefMarker>
        ) : (
          <span key={pi}>
            {splitLinks(part.text).map((lp, li) =>
              'href' in lp ? (
                <LinkAnchor key={li} href={lp.href}>
                  {lp.text}
                </LinkAnchor>
              ) : (
                <span key={li}>{citePieces(lp.text)}</span>
              )
            )}
          </span>
        )
      )}
    </span>
  )
}

function renderSegs(segs: Seg[], vocab: Vocab | null, refs: ParaRefs | null = null): ReactElement[] {
  return segs.map((s, i) => {
    const el = renderSeg(s, i, vocab, refs)
    return s.bold ? (
      <strong key={i} className="pd-tp-b">
        {el}
      </strong>
    ) : (
      el
    )
  })
}

function renderSeg(s: Seg, i: number, vocab: Vocab | null, refs: ParaRefs | null): ReactElement {
  if (s.tex) return <ClickTex key={i} tex={s.tex} />
  if (s.term) {
    const term = s.term
    const g = useGlossaryStore.getState().terms.find((t) => t.term === term)
    return (
      <span
        key={i}
        className="pd-tp-term"
        data-term={term}
        title={g ? `${g.zh ? `${g.zh} · ` : ''}${g.plain}（点开看详细解释）` : '术语表里的词'}
        onClick={() => {
          useGlossaryStore.getState().setFocus(term)
          useReaderStore.getState().setRight(true, 'glossary')
        }}
      >
        {s.text}
      </span>
    )
  }
  if (s.annId === 'sel:keep') {
    return (
      <span key={i} className="pd-tp-selkeep">
        {s.text}
      </span>
    )
  }
  if (s.annId?.startsWith(NOTE_MARK_PREFIX)) {
    return (
      <span key={i} className="pd-tp-notemark" data-note={s.annId.slice(NOTE_MARK_PREFIX.length)} title="点开看这条笔记">
        {s.text}
      </span>
    )
  }
  if (s.annId) {
    return (
      <span key={i} className="pd-tp-ann" data-ann={s.annId} style={{ background: `var(--hl-${s.color ?? 'yellow'})` }}>
        {s.text}
      </span>
    )
  }
  if (s.mark) {
    return (
      <mark key={i} className="pd-tp-hit">
        {s.text}
      </mark>
    )
  }
  return plainPieces(s.text, vocab, i, refs)
}

const CAPTION_LABEL_ZH = (label: string): string => label.replace(/^(?:Extended Data |Supplementary |Supp\. )?(?:Figure|Fig\.)\s*/i, '图 ').replace(/^Table\s*/i, '表 ')

interface FormulaProps {
  item: TextItem
  eq: EquationInfo | undefined
  paperId: string
  running: boolean
  runError: string | null
}

/**
 * 公式块：识别好的 LaTeX 用 KaTeX 渲染（居中，编号靠右）；识别中显示“正在识别公式…”，
 * 失败写明原因并可重试，只有失败或用户要对照时才显示截图。
 */
function FormulaBlock({ item, eq, paperId, running, runError }: FormulaProps) {
  const ref = useRef<HTMLDivElement>(null)
  const near = useNearViewport(ref)
  const refreshEquations = useTextStore((s) => s.refreshEquations)
  const startEquations = useTextStore((s) => s.startEquations)
  const [showImg, setShowImg] = useState(false)
  const [retrying, setRetrying] = useState(false)
  const hasTex = !!eq?.latex
  const { tex, tag } = splitTag(eq?.latex ?? '')
  const shownNumber = eq?.number ?? tag
  const suspicious = hasTex && (eq?.confidence === 'low' || (eq?.warnings.length ?? 0) > 0)
  const failed = !!eq && !hasTex && eq.status === 'failed'
  const wantImg = !!eq && (showImg || !hasTex)
  const { url } = useBlobUrl(wantImg && near && eq ? () => textApi.equationVector(paperId, eq.id) : null, [paperId, eq?.id, wantImg, near])

  const explain = (): void => {
    if (!eq) return
    explainEquation({ text: item.text, page: item.page, rects: [], clientRect: { left: 0, top: 0, right: 0, bottom: 0 }, ord: item.ord, fromText: true }, eq.id)
  }
  const retry = async (): Promise<void> => {
    if (!eq) return
    setRetrying(true)
    try {
      await visionApi.transcribe(paperId, eq.id, true)
    } catch {
      /* 失败原因由随后的状态刷新显示 */
    } finally {
      await refreshEquations()
      setRetrying(false)
    }
  }

  if (!eq) {
    return (
      <div className="pd-tp-formula" ref={ref}>
        <code className="pd-tp-mono">{stripBroken(item.text)}</code>
      </div>
    )
  }
  return (
    <div className="pd-tp-formula" ref={ref}>
      {hasTex ? (
        <div className="pd-tp-eqrow">
          <div className="pd-tp-eqmath">
            <Tex latex={tex} display />
          </div>
          {shownNumber && <span className="pd-tp-eqno">({shownNumber})</span>}
        </div>
      ) : (
        <div className="pd-tp-eqwait">
          {failed ? (
            <p className="pd-tp-note">
              公式识别失败：{eq.error ?? '模型没有返回可用的结果'}。
              <button type="button" className="pd-tp-link" disabled={retrying} onClick={() => void retry()}>
                {retrying ? '重试中…' : '重试'}
              </button>
            </p>
          ) : running ? (
            <p className="pd-tp-note">正在识别公式…</p>
          ) : runError ? (
            <p className="pd-tp-note">
              公式还没有识别：{runError}
              <button type="button" className="pd-tp-link" onClick={() => void startEquations(true)}>
                重试识别
              </button>
            </p>
          ) : (
            <p className="pd-tp-note">等待识别公式…</p>
          )}
          {url && <ZoomImage src={url} alt={`公式${eq.number ? ` (${eq.number})` : ''}`} className="pd-tp-eqimg pd-tp-eqimg--small" />}
        </div>
      )}
      {showImg && hasTex && url && <ZoomImage src={url} alt={`公式${eq.number ? ` (${eq.number})` : ''}原图`} className="pd-tp-eqimg" />}
      <div className="pd-tp-formula-tools">
        {suspicious && (
          <button type="button" className="pd-tp-link" onClick={() => setShowImg((v) => !v)}>
            {showImg ? '收起原图' : '识别可能有误，点此对照原图'}
          </button>
        )}
        <button type="button" className="pd-tp-link" onClick={explain}>
          解释公式
        </button>
      </div>
    </div>
  )
}

/**
 * 图：默认就内联显示在图注上方（整张图，子图已按图注合并），快要滚到时才加载；点击放大，Esc 或点空白处关闭。
 * 截图是本地从 PDF 渲染的，不调用模型。
 */
function FigureInline({ fig, paperId }: { fig: FigureRegion; paperId: string }) {
  const ref = useRef<HTMLDivElement>(null)
  const near = useNearViewport(ref)
  const { url, error, type } = useBlobUrl(near ? () => textApi.regionVector(paperId, fig.page, fig.bbox, 130) : null, [paperId, fig.id, near])
  const vector = type.includes('svg') // 矢量图放大不会糊，不用再去取更清晰的版本
  const [kind, setKind] = useState<RegionKind | null>(null)
  useEffect(() => {
    if (!url) return
    let alive = true
    textApi.regionKind(paperId, fig.page, fig.bbox).then((k) => alive && setKind(k)).catch(() => undefined)
    return () => {
      alive = false
    }
  }, [url, paperId, fig.page, fig.bbox])
  const alt = fig.label ?? '图'
  return (
    <div className="pd-tp-fig" ref={ref}>
      {url ? (
        <ZoomImage
          src={url}
          alt={alt}
          className="pd-tp-figimg"
          loading="lazy"
          large={vector ? undefined : { key: `${paperId}:${fig.id}`, load: () => textApi.regionImage(paperId, fig.page, fig.bbox, 220) }}
        />
      ) : error ? (
        <p className="pd-tp-note">{alt} 的图片渲染失败。</p>
      ) : (
        <div className="pd-tp-figph" aria-hidden="true" />
      )}
      {kind && <div className={`pd-tp-figkind pd-tp-figkind-${kind.kind}`}>{regionKindLabel(kind)}</div>}
    </div>
  )
}

/** 表格单元格：译文模式显示中文（没有译文的格子显示原文）；对照模式原文在上、中文在下。 */
function CellText({ v, zh, view }: { v: string; zh: string | null; view: TextView }) {
  if (!zh || view === 'text') return <>{v}</>
  if (view === 'target') return <span title={`原文：${v}`}>{zh}</span>
  return (
    <>
      {v}
      <span className="pd-tp-celltr">{zh}</span>
    </>
  )
}

interface TableBlockProps {
  paperId: string
  t: TableAnalysisItem
  /** 整批表格识别任务正在进行 */
  running: boolean
  /** 任务整体失败的原因（没有 API Key、没配置视觉模型…） */
  runError: string | null
  onRetry: () => void
  /** 文字视图的当前模式：译文/对照时表格里的文字也显示中文 */
  view?: TextView
  /** 这张表已翻译的单元格（没翻译的格子是 null，显示原文） */
  tr?: TableTranslation
}

/**
 * 表格：识别好的在表题处渲染成真正的表格（表头加粗黑线、数值列右对齐、可横向滚动、可复制为 TSV / Markdown）；
 * 与 PDF 文字层核对不上的单元格用虚线下划线标出；识别中显示“正在识别表格…”，失败写明原因并可重试；
 * “对照原图”折叠项显示表格区域的截图。识别结果由视觉模型转录，不能当作原文。
 */
function TableBlock({ paperId, t, running, runError, onRetry, view = 'text', tr }: TableBlockProps) {
  const [showImg, setShowImg] = useState(false)
  const { url } = useBlobUrl(showImg ? () => textApi.tableVector(paperId, t.caption_ord) : null, [paperId, t.caption_ord, showImg])
  const data = t.status === 'ready' ? t.data : null
  const bodyOrds = t.body_ords.join(' ')
  const copy = async (kind: 'tsv' | 'md'): Promise<void> => {
    if (!data) return
    try {
      await navigator.clipboard.writeText(kind === 'tsv' ? tableToTsv(data) : tableToMarkdown(data))
      toast.success(kind === 'tsv' ? '已复制，可粘贴进 Excel' : '已复制为 Markdown 表格')
    } catch {
      toast.error('复制失败', '没有剪贴板权限')
    }
  }
  const num = useMemo(() => (data ? numericColumns(data) : []), [data])
  const bad = useMemo(() => (data ? unverifiedSet(data) : new Set<string>()), [data])
  const mis = useMemo(() => (data ? misplacedSet(data) : new Set<string>()), [data])
  const nBad = data ? unverifiedCount(data) : 0
  const nMis = data ? misplacedCount(data) : 0

  if (!data) {
    const active = t.status === 'pending' || t.status === 'running' || (running && t.status === 'none')
    return (
      <div className="pd-tp-table pd-tp-table--wait" data-ords={bodyOrds}>
        {active ? (
          <p className="pd-tp-note">正在识别表格，下面先显示从 PDF 提取出的行文字</p>
        ) : t.status === 'failed' ? (
          <p className="pd-tp-note">
            表格识别失败：{t.error ?? '模型没有返回可用的结果'}。
            <button type="button" className="pd-tp-link" onClick={onRetry}>
              重试
            </button>
          </p>
        ) : runError ? (
          <p className="pd-tp-note">
            表格还没有识别：{runError}
            <button type="button" className="pd-tp-link" onClick={onRetry}>
              重试识别
            </button>
          </p>
        ) : (
          <p className="pd-tp-note">等待识别表格…</p>
        )}
      </div>
    )
  }
  return (
    <div className="pd-tp-table" data-ords={bodyOrds}>
      <div className="pd-fd-wrap" tabIndex={0} aria-label={`${t.label} 表格`}>
        <table>
          <thead>
            <tr>
              {data.columns.map((c, ci) => (
                <th key={ci} scope="col" className={num[ci] ? 'pd-num' : undefined}>
                  <CellText v={c} zh={tr?.columns[ci] ?? null} view={view} />
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {data.rows.map((r, ri) => (
              <tr key={ri}>
                {data.columns.map((_c, ci) => {
                  const v = r[ci] ?? ''
                  const flagged = bad.has(`${ri},${ci}`)
                  const misplaced = mis.has(`${ri},${ci}`)
                  return (
                    <td key={ci} className={num[ci] ? 'pd-num' : undefined}>
                      {misplaced ? (
                        <span className="pd-tp-mis" title="位置存疑：这个值在 PDF 文字层里出现过，但不在这一行/列的位置，可能填错了格子，请对照原图">
                          {v}
                        </span>
                      ) : flagged ? (
                        <span className="pd-tp-unv" title="这个单元格没能与 PDF 文字层核对，可能识别有误，请对照原图">
                          {v}
                        </span>
                      ) : (
                        <CellText v={v} zh={tr?.rows[ri]?.[ci] ?? null} view={view} />
                      )}
                    </td>
                  )
                })}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      {data.notes && <p className="pd-tp-note">注：{data.notes}</p>}
      <div className="pd-fd-actions">
        <span>
          {t.source === 'text'
            ? '按文字层建表'
            : t.source === 'layout'
              ? `按文字层坐标还原${data.layout && data.layout.confidence < 0.9 ? `，可信度 ${Math.round(data.layout.confidence * 100)}%，可能有误，请对照原图` : ''}`
              : '视觉模型转录'}
          {nBad > 0 ? ` · ${nBad} 个单元格未能与文字层核对，已用虚线标出` : nMis === 0 && data.verified_ratio !== null ? ' · 已与文字层核对' : ''}
        </span>
        <button type="button" className="pd-fd-act" onClick={() => void copy('tsv')}>
          复制为 TSV
        </button>
        <button type="button" className="pd-fd-act" onClick={() => void copy('md')}>
          复制为 Markdown
        </button>
        <button type="button" className="pd-fd-act" onClick={() => setShowImg((v) => !v)} aria-expanded={showImg}>
          {showImg ? '收起原图' : '对照原图'}
        </button>
      </div>
      {nMis > 0 && (
        <p className="pd-tp-note pd-tp-note--mis" role="status">
          {nMis} 个单元格位置存疑，已用点状下划线加问号标出，请对照原图。
        </p>
      )}
      {(() => {
        // “N 个单元格位置存疑”界面上已经单独写了一句，后端告警里的同一条不再重复
        const rest = data.warnings.filter((w) => !/个单元格位置存疑/.test(w))
        return rest.length > 0 ? <p className="pd-tp-note">{rest.join(' ')}</p> : null
      })()}
      {showImg && (url ? <ZoomImage src={url} alt={`${t.label} 原图`} className="pd-tp-figimg" /> : <Skeleton height={120} />)}
    </div>
  )
}

interface PassageProps {
  parts: TextItem[]
  view: TextView
  tr: string | undefined
  marks: AnnMark[]
  query: string
  hit: 'none' | 'hit' | 'active'
  flash: number | null
  eq: EquationInfo | undefined
  fig: FigureRegion | undefined
  paperId: string
  /** 整理排版用的词表；显示原始文字时为 null */
  vocab: Vocab | null
  boldFirst: boolean
  /** 术语表里第一次出现在这一段里的词：正文里标上虚线下划线 */
  glossTerms?: string[]
  /** 这个块是某一页的第一个块：边距页码（页码开关打开时才显示） */
  pageNo: number | null
  eqRunning: boolean
  eqError: string | null
  /** 这个段落是某个表格的表题：识别结果（有的话）渲染在表题下面 */
  table: TableAnalysisItem | undefined
  tblRunning: boolean
  tblError: string | null
  onRetryTables: () => void
  /** 这个表格已翻译的单元格 */
  tableTr?: TableTranslation
  /** 这个段落上的笔记：段落下面贴便签角标（原文、译文都有），原文里引用的文字另外标出来 */
  notes?: Note[]
  /** 正在“重新翻译这一段” */
  retranslating: boolean
  onRetranslate: (ords: number[]) => void
}

const PassageView = memo(function PassageView({ parts, view, tr, marks, query, hit, flash, eq, fig, paperId, vocab, boldFirst, glossTerms, pageNo, eqRunning, eqError, table, tblRunning, tblError, onRetryTables, tableTr, notes, retranslating, onRetranslate }: PassageProps) {
  const item = parts[0]
  const paraRefsMap = useRefStore((s) => s.paraRefs)
  const refs = useMemo(() => mergeParaRefs(parts.map((p) => p.ord), paraRefsMap), [parts, paraRefsMap])
  // 译文里的方括号编号常被改写（破折号、全角括号…），后端按原文记的标记文字对不上：译文再按“编号确实存在”认一遍
  const validRefs = useRefStore((s) => s.valid)
  const trRefs = useMemo<ParaRefs | null>(() => (validRefs.size > 0 ? { sup: refs?.sup ?? new Set<number>(), text: refs?.text ?? [], loose: validRefs } : refs), [refs, validRefs])
  const isCaption = item.kind === 'caption' || !!table
  // 这一段怎么读：选中它（或点左边的小按钮）后，在它下面显示；正文段落才有
  const paraOrd = useParaStore((s) => s.ord)
  const paraOn = paraOrd !== null && parts.some((p) => p.ord === paraOrd)
  const canRole = item.kind === 'paragraph' && item.text.trim().length >= 60
  const translatable = item.kind === 'paragraph' || item.kind === 'caption'
  const showOrig = !translatable || view !== 'target' || tr === undefined
  const showTr = translatable && view !== 'text' && tr !== undefined
  const plain = marks.length === 0 && !query
  const raw = blockText(parts)
  const text = vocab ? cleanDisplay(raw) : raw
  const cap = vocab && isCaption && plain ? parseCaption(text) : null
  // 图注开头的“图 3 / 表 2”可点：查看，或让 AI 解析（只有论文里真有对应的图/表时）
  const capKey = labelKey(fig?.kind === 'figure' ? fig.label : table && table.kind !== 'figure_data' ? table.label : null)
  const capTarget: FigTarget | null = cap?.label && capKey ? { kind: fig?.kind === 'figure' ? 'fig' : 'table', key: capKey } : null
  // 原文自带加粗时按原文来；没有加粗的段落才可以选“首句加粗”
  const bold = useMemo(() => boldRanges(text, parts.flatMap((p) => p.bold ?? [])), [text, parts])
  const maths = useMemo(() => mathRanges(text, parts.flatMap((p) => p.math ?? [])), [text, parts])
  const first = vocab && boldFirst && item.kind === 'paragraph' && plain && bold.length === 0 && maths.length === 0 ? splitFirstSentence(text) : null
  const cls = [
    'pd-tp',
    isCaption ? 'pd-tp--caption' : '',
    item.kind === 'table' ? 'pd-tp--table' : '',
    item.kind === 'figure_text' ? 'pd-tp--figtext' : '',
    view === 'target' && translatable && tr === undefined ? 'pd-tp--untranslated' : '',
    view === 'both' && showTr ? 'pd-tp--pair' : ''
  ]
    .filter(Boolean)
    .join(' ')
  const trText = tr === undefined ? undefined : vocab ? stripBroken(tr) : tr
  // 译文和原文一样渲染：行内公式、原文加粗里保留下来的词、引用角标的悬浮卡片、链接、搜索命中；图注拆成“编号 + 加粗标题句”，段落可以首句加粗
  const trCap = trText !== undefined && vocab && isCaption ? parseCaptionZh(trText) : null
  const trFirst = trText !== undefined && vocab && boldFirst && item.kind === 'paragraph' && !query && bold.length === 0 && maths.length === 0 ? splitFirstSentenceZh(trText) : null
  const trSegs = useMemo(
    () => (trText === undefined ? [] : applyMath(applyBold(segmentsFor(trText, [], query), boldRanges(trText, parts.flatMap((p) => p.bold ?? []))), mathRanges(trText, parts.flatMap((p) => p.math ?? [])))),
    [trText, query, parts]
  )
  const trClass = view === 'both' ? 'pd-tp-p pd-tp-trans pd-tp-tr' : 'pd-tp-p pd-tp-trans'
  return (
    <div
      className={cls}
      data-ord={item.ord}
      data-ords={parts.length > 1 ? parts.map((p) => p.ord).join(' ') : undefined}
      data-page={item.page}
      data-hit={hit}
      data-flash={flash === null ? undefined : flash % 2}
      data-paraon={paraOn || undefined}
    >
      {canRole && (
        <button
          type="button"
          className="pd-tp-parabtn"
          title="这一段怎么读：作用、该带走什么、读的时候留意什么"
          aria-label="这一段怎么读"
          onClick={() => void useParaStore.getState().open(paperId, item.ord)}
        >
          <Compass size={13} />
        </button>
      )}
      {pageNo !== null && (
        <span className="pd-tp-pg" aria-hidden="true">
          {pageNo}
        </span>
      )}
      {item.kind === 'formula' ? (
        <FormulaBlock item={item} eq={eq} paperId={paperId} running={eqRunning} runError={eqError} />
      ) : (
        <>
          {isCaption && fig && <FigureInline fig={fig} paperId={paperId} />}
          {showOrig &&
            (cap && cap.label ? (
              <div className="pd-tp-cap">
                {capTarget ? (
                  <FigLabelMenu className="pd-tp-cap-label pd-tp-cap-label--btn" target={capTarget}>
                    {CAPTION_LABEL_ZH(cap.label)}
                  </FigLabelMenu>
                ) : (
                  <span className="pd-tp-cap-label">{CAPTION_LABEL_ZH(cap.label)}</span>
                )}
                <p className="pd-tp-p">
                  {cap.title && <strong className="pd-tp-cap-title">{renderSegs([{ text: cap.title }], vocab, refs)} </strong>}
                  {renderSegs([{ text: cap.rest }], vocab, refs)}
                </p>
              </div>
            ) : first ? (
              <p className="pd-tp-p">
                <strong className="pd-tp-first">{renderSegs([{ text: first.first }], vocab, refs)}</strong>
                {renderSegs([{ text: first.rest }], vocab, refs)}
              </p>
            ) : (
              <p className="pd-tp-p">{renderSegs(applyTerms(applyMath(applyBold(segmentsFor(text, marks, query), bold), maths), glossTerms ?? []), vocab, refs)}</p>
            ))}
          {showTr &&
            trText !== undefined &&
            (trCap && trCap.label ? (
              <div className="pd-tp-cap pd-tp-trans">
                <span className="pd-tp-cap-label">{trCap.label}</span>
                <p className={trClass}>
                  {trCap.title && <strong className="pd-tp-cap-title">{renderSegs([{ text: trCap.title }], vocab, trRefs)} </strong>}
                  {renderSegs([{ text: trCap.rest }], vocab, trRefs)}
                </p>
              </div>
            ) : trFirst ? (
              <p className={trClass}>
                <strong className="pd-tp-first">{renderSegs([{ text: trFirst.first }], vocab, trRefs)}</strong>
                {renderSegs([{ text: trFirst.rest }], vocab, trRefs)}
              </p>
            ) : (
              <p className={trClass}>{renderSegs(trSegs, vocab, trRefs)}</p>
            ))}
          {showTr && (
            <button type="button" className="pd-tp-link pd-tp-retr" disabled={retranslating} onClick={() => onRetranslate(parts.map((p) => p.ord))}>
              {retranslating ? '重新翻译中…' : '重新翻译这一段'}
            </button>
          )}
        </>
      )}
      {paraOn && <ParaInsightCard variant="inline" />}
      {notes && notes.length > 0 && (
        <div className="pd-tp-notes">
          {notes.map((n) => (
            <button key={n.id} type="button" className="pd-tp-notetab" data-note={n.id} title="点开看这条笔记">
              <StickyNote size={12} />
              <span>{n.text ? n.text.replace(/\s+/g, ' ').slice(0, 24) : '引用'}</span>
            </button>
          ))}
        </div>
      )}
      {table &&
        (table.kind === 'figure_data' ? (
          <FigureDataBlock paperId={paperId} t={table} running={tblRunning} onRetry={onRetryTables} />
        ) : (
          <TableBlock paperId={paperId} t={table} running={tblRunning} runError={tblError} onRetry={onRetryTables} view={view} tr={tableTr} />
        ))}
    </div>
  )
})

export function TextReader() {
  const paperId = useReaderStore((s) => s.paperId) ?? ''
  const paperTitle = useReaderStore((s) => s.paper?.title) ?? ''
  const readViewState = useReaderStore((s) => s.readView)
  const splitTextView = useReaderStore((s) => s.splitTextView)
  const view: TextView = readViewState === 'split' ? splitTextView : (readViewState as TextView)
  const textSize = useReaderStore((s) => s.textSize)
  const wide = useReaderStore((s) => s.textWide)
  const outline = useReaderStore((s) => s.outline)
  const jump = useReaderStore((s) => s.jump)
  const flash = useReaderStore((s) => s.flash)
  const searchOpen = useReaderStore((s) => s.searchOpen)
  const annotations = useMarksStore((s) => s.annotations)
  const query = useSearchStore((s) => s.query)
  const hits = useSearchStore((s) => s.hits)
  const activeHit = useSearchStore((s) => s.active)
  const prefs = useTextPrefs()

  const { status, error, items, trans, tableTr, headingTr, equationsByOrd, figuresByCaption, trTotal, trDone, trComplete, jobId, jobMsg, progress, eq, eqJobId, eqProgress, eqError } = useTextStore()
  const { tablesByCaption, tableHidden, tbl, tblJobId, tblProgress, tblError, retranslating } = useTextStore()
  // 术语表里的词只在第一次出现的那一段里标出来（论文自己定义的词就在定义它的那一段）
  const glossTerms = useGlossaryStore((s) => s.terms)
  const termsByOrd = useMemo(() => {
    const out = new Map<number, string[]>()
    const paras = items.filter((i) => i.kind === 'paragraph')
    for (const t of glossTerms) {
      const own = t.in_paper && t.ord !== null && paras.some((p) => p.ord === t.ord) ? t.ord : null
      const re = termRegExp(t.term)
      const ord = own ?? paras.find((p) => re.test(p.text))?.ord ?? null
      if (ord !== null) out.set(ord, [...(out.get(ord) ?? []), t.term])
    }
    return out
  }, [glossTerms, items])

  const eqList = useTextStore((s) => s.eqList)
  // 公式/图/表自己的区域框：并排视图里点它们，PDF 栏用这些框定位，而不是段落框
  const regions = useMemo(() => buildRegions(eqList, figuresByCaption, tablesByCaption), [eqList, figuresByCaption, tablesByCaption])
  const startTranslation = useTextStore((s) => s.startTranslation)
  // 正文已经翻完、只差标题（以前翻译的论文）：进入译文/对照时自动补译标题，很短的一次调用；每篇论文只自动试一次。
  // （这几个 hook 必须在下面任何提前 return 之前）
  const headingTried = useRef<string | null>(null)
  const headingsMissing = Object.keys(headingTr).length === 0
  const jobRunning = jobId !== null
  useEffect(() => {
    if (view === 'text' || !paperId || headingTried.current === paperId) return
    if (trDone > 0 && !jobRunning && headingsMissing) {
      headingTried.current = paperId
      void startTranslation()
    }
  }, [view, paperId, trDone, jobRunning, headingsMissing, startTranslation])
  const cancelTranslation = useTextStore((s) => s.cancelTranslation)
  const startEquations = useTextStore((s) => s.startEquations)
  const startTables = useTextStore((s) => s.startTables)
  const retranslate = useTextStore((s) => s.retranslate)
  const retryTables = useCallback(() => void startTables(true), [startTables])
  const retranslateParts = useCallback(
    async (ords: number[]): Promise<void> => {
      for (const o of ords) await retranslate(o)
    },
    [retranslate]
  )

  // 参考文献与正文引用标记：论文打开后在后台读取；读不到不影响阅读（正文里就没有可点击的引用标记）
  const refOrdToIndex = useRefStore((s) => s.ordToIndex)
  const loadRefs = useRefStore((s) => s.load)
  useEffect(() => {
    if (paperId) void loadRefs(paperId)
  }, [paperId, loadRefs])

  const [showNoise, setShowNoise] = useState(false)
  const [refsOpen, setRefsOpen] = useState(false)
  const [activeSec, setActiveSec] = useState<string | null>(null)
  const [width, setWidth] = useState(0)
  const containerRef = useRef<HTMLDivElement>(null)
  const handledJump = useRef(0)
  const guardUntil = useRef(0)
  const rafId = useRef(0)

  const raw = prefs.raw
  // 已经识别成表格的那些表：它们的行文字段落不再单独显示（显示原始提取文字时保留，方便对照）
  const shown = useMemo(() => (raw || tableHidden.size === 0 ? items : items.filter((i) => !tableHidden.has(i.ord))), [items, tableHidden, raw])
  const doc = useMemo(() => buildDoc(shown, outline, { showNoise, raw }), [shown, outline, showNoise, raw])
  const vocab = useMemo(() => (raw ? null : buildVocab(items)), [items, raw])

  const toc = useMemo<TocItem[]>(
    () =>
      doc.blocks
        .filter((b): b is Extract<Block, { type: 'heading' }> => b.type === 'heading')
        .map((b) => ({ key: b.key, title: b.title, level: b.level, page: b.page })),
    [doc.blocks]
  )
  // 目录栏里的“本文表格”清单：带 “Table N:” 图注的表，和图里的数据网格（挂在图注上）
  const tocTables = useMemo<TocTable[]>(
    () =>
      [...tablesByCaption.values()]
        .sort((a, b) => a.caption_ord - b.caption_ord)
        .map((t) => ({ key: String(t.caption_ord), ord: t.caption_ord, label: t.kind === 'figure_data' ? `${t.label} 图中数据` : t.label, page: t.page })),
    [tablesByCaption]
  )

  const notes = useNotesStore((s) => s.notes)
  const marksByPage = useMemo(() => {
    const m = new Map<number, AnnMark[]>()
    for (const a of annotations) if (a.text) m.set(a.page, [...(m.get(a.page) ?? []), { id: a.id, text: a.text, color: a.color }])
    // 去右边面板提问时浏览器会取消选区：保留的那段话在正文里继续标出来，知道提问针对的是哪里
    // 笔记引用的原文也标在正文里（点开就是那张便签）
    for (const [page, ms] of noteMarksByPage(notes)) m.set(page, [...(m.get(page) ?? []), ...ms])
    return m
  }, [annotations, notes])
  const notesByOrd = useMemo(() => noteOrdMap(notes, items), [notes, items])

  const hitState = useMemo(() => {
    const m = new Map<number, 'hit' | 'active'>()
    hits.forEach((h, i) => {
      if (m.get(h.ord) !== 'active') m.set(h.ord, i === activeHit ? 'active' : 'hit')
    })
    return m
  }, [hits, activeHit])

  const flashOrd = useMemo(() => (flash ? (flash.ord ?? findPassageByBBox(items, flash.page, flash.bbox)) : null), [flash, items])

  // 阅读区宽度：够宽才显示右侧“本文结构”
  useEffect(() => {
    const el = containerRef.current
    if (!el) return
    const ro = new ResizeObserver((e) => setWidth(e[0].contentRect.width))
    ro.observe(el)
    return () => ro.disconnect()
  }, [status])

  // ---------- 跳转（目录 / 书签 / 页码 / 证据 / 搜索） ----------
  const scrollToJump = useCallback((): boolean => {
    const el = containerRef.current
    const j = useReaderStore.getState().jump
    if (!el || !j) return true
    if (j.from === 'text') return true // 这次跳转就是文字栏自己发起的（并排视图）：不再滚动自己
    if (j.from === 'pdf') muteSync()
    const ord = j.ord ?? (j.bbox || j.flash ? findPassageByBBox(items, j.page, j.bbox) : null)
    let target: HTMLElement | null = ord !== null && ord !== undefined ? el.querySelector<HTMLElement>(`[data-ord="${ord}"], [data-ords~="${ord}"]`) : null
    if (!target && j.section) target = Array.from(el.querySelectorAll<HTMLElement>('[data-sec]')).find((h) => h.dataset.sec === j.section) ?? null
    if (!target && ord !== null && ord !== undefined && doc.references.some((r) => r.ord === ord)) {
      setRefsOpen(true) // 引用的是参考文献：先展开再定位
      return false
    }
    if (!target) {
      // 这一页没有可显示的文字：顺延到下一个有文字的块
      const blocks = Array.from(el.querySelectorAll<HTMLElement>('[data-page]'))
      target = blocks.find((p) => Number(p.dataset.page) >= j.page) ?? blocks[blocks.length - 1] ?? null
    }
    if (!target) return true
    guardUntil.current = performance.now() + 900
    const go = (): void => {
      const top = target!.getBoundingClientRect().top - el.getBoundingClientRect().top + el.scrollTop - (ord !== null && ord !== undefined ? READ_LINE + 20 : 12)
      el.scrollTo({ top: Math.max(0, top) })
    }
    go()
    // content-visibility 的分组在滚动后才渲染出真实高度：下一帧再校正一次位置
    requestAnimationFrame(() => {
      go()
      window.setTimeout(go, 120)
    })
    // 重开论文恢复阅读位置：长文档里上方分组的真实高度要更久才稳定，稍后再校正几次；用户已经自己滚动了就不再动
    if (ord !== null && ord !== undefined && !j.flash && !j.bbox) {
      let last = -1
      for (const ms of [350, 800, 1600]) {
        window.setTimeout(() => {
          if (last >= 0 && Math.abs(el.scrollTop - last) > 60) return
          go()
          last = el.scrollTop
        }, ms)
      }
    }
    const landed = Number(target.closest<HTMLElement>('[data-page]')?.dataset.page)
    useReaderStore.getState().setCurrentPage(Number.isFinite(landed) && landed > 0 ? landed : j.page)
    return true
  }, [items, doc.references])

  useEffect(() => {
    if (status !== 'ready' || !jump || jump.id === handledJump.current) return
    if (scrollToJump()) handledJump.current = jump.id
  }, [jump, status, scrollToJump, refsOpen])

  // ---------- 当前页与当前章节跟踪 + 选区工具条位置 ----------
  const onScroll = useCallback(() => {
    if (rafId.current) return
    rafId.current = requestAnimationFrame(() => {
      rafId.current = 0
      const el = containerRef.current
      if (!el) return
      const top = el.getBoundingClientRect().top
      if (performance.now() >= guardUntil.current) {
        const tops = Array.from(el.querySelectorAll<HTMLElement>('[data-page]')).map((p) => ({ page: Number(p.dataset.page), top: p.getBoundingClientRect().top - top }))
        if (tops.length) {
          const st0 = useReaderStore.getState()
          const pg = pageAtLine(tops, READ_LINE)
          const changed = pg !== st0.currentPage
          st0.setCurrentPage(pg)
          // 并排视图的同步滚动：文字栏翻到新的一页，PDF 栏跟到同一页
          if (changed && st0.readView === 'split' && usePrefs.getState().syncScroll && !syncMuted()) st0.jumpToPage(pg, 'text')
        }
      }
      const secs = Array.from(el.querySelectorAll<HTMLElement>('[data-sec]')).map((h) => ({ title: h.dataset.sec ?? '', top: h.getBoundingClientRect().top - top }))
      const cur = activeSection(secs, READ_LINE + 40)
      setActiveSec((prev) => (prev === cur ? prev : cur))
      // 阅读位置记忆：记下阅读线上方最近的段落（重开论文回到这里）
      if (performance.now() >= guardUntil.current) {
        const line = top + READ_LINE
        for (const p of el.querySelectorAll<HTMLElement>('[data-ord]')) {
          if (p.getBoundingClientRect().bottom > line) {
            const ord = Number(p.dataset.ord)
            if (Number.isFinite(ord)) useReaderStore.getState().setTextAnchor(ord)
            break
          }
        }
      }
      const st = useReaderStore.getState()
      if (st.selection) st.setSelection(readTextSelection(el))
    })
  }, [])

  // ---------- 选区 ----------
  useEffect(() => {
    const el = containerRef.current
    if (!el) return
    let timer = 0
    const refresh = (): void => {
      window.clearTimeout(timer)
      timer = window.setTimeout(() => useReaderStore.getState().setSelection(readTextSelection(el)), 10)
    }
    const onSelChange = (): void => {
      const sel = window.getSelection()
      if (!sel || sel.isCollapsed) {
        window.clearTimeout(timer)
        timer = window.setTimeout(() => {
          const s = window.getSelection()
          if ((!s || s.isCollapsed) && !selectionCollapseIsFromPanel()) useReaderStore.getState().setSelection(null)
        }, 120)
      }
    }
    el.addEventListener('mouseup', refresh)
    el.addEventListener('keyup', refresh)
    document.addEventListener('selectionchange', onSelChange)
    return () => {
      window.clearTimeout(timer)
      el.removeEventListener('mouseup', refresh)
      el.removeEventListener('keyup', refresh)
      document.removeEventListener('selectionchange', onSelChange)
    }
  }, [status])

  const onClick = (e: React.MouseEvent): void => {
    const sel = window.getSelection()
    if (sel && !sel.isCollapsed) return
    // 并排视图：点一个段落，PDF 栏滚动到它所在的页并框出它
    if (useReaderStore.getState().readView === 'split') {
      const p = (e.target as HTMLElement).closest<HTMLElement>('[data-ord]')
      const ord = p ? Number(p.dataset.ord) : NaN
      const it = Number.isFinite(ord) ? items.find((i) => i.ord === ord) : undefined
      if (it) {
        muteSync()
        // 公式/图/表：用它们自己的区域框（整块公式、整张图/表），不是某个段落的框
        const region = regionForOrd(regions, ord)
        if (region) useReaderStore.getState().jumpToEvidence({ page: region.page, bbox: region.bbox, ord, from: 'text' })
        // 老版本后端的文字接口没有位置框：只能让 PDF 栏翻到这一页，不画框
        else if (it.bbox) useReaderStore.getState().jumpToEvidence({ page: it.page, bbox: it.bbox, ord, from: 'text' })
        else useReaderStore.getState().jumpToPage(it.page, 'text')
      }
    }
    const noteEl = (e.target as HTMLElement).closest<HTMLElement>('[data-note]')
    if (noteEl?.dataset.note) {
      useNotePeek.getState().open(noteEl.dataset.note, e.clientX, e.clientY)
      return
    }
    const marks = useMarksStore.getState()
    const ann = (e.target as HTMLElement).closest<HTMLElement>('[data-ann]')
    marks.setActiveAnnotation(ann?.dataset.ann ? { id: ann.dataset.ann, x: e.clientX, y: e.clientY } : null)
  }

  if (status === 'loading' || status === 'idle') {
    return (
      <div className="pd-textreader pd-textreader--state">
        <div style={{ width: 'min(720px, 80%)' }}>
          <Skeleton height={18} />
          <Skeleton height={18} />
          <Skeleton height={18} />
        </div>
      </div>
    )
  }
  if (status === 'error') {
    return (
      <div className="pd-textreader pd-textreader--state">
        <EmptyState tone="danger" icon={<AlertCircle size={22} />} title="无法读取论文文字" description={error ?? undefined} />
      </div>
    )
  }

  const translating = jobId !== null
  const needTr = (view === 'both' || view === 'target') && !trComplete
  const searchQuery = searchOpen ? query : ''
  const eqRunning = eqJobId !== null
  const eqPending = eq.total > 0 && !eq.complete
  const tblRunning = tblJobId !== null
  const tblPending = tbl.total > 0 && !tbl.complete
  const showBanner = translating || needTr || eqRunning || eqPending || !!eqError || tblRunning || tblPending || !!tblError
  const showToc = prefs.toc && width >= TOC_MIN_WIDTH && toc.length >= 2
  const abstractText = doc.abstract

  const renderPassage = (parts: TextItem[], pageNo: number | null): ReactElement => {
    const it = parts[0]
    const ords = parts.map((p) => p.ord)
    const trs = parts.map((p) => trans.get(p.ord)).filter((t): t is string => t !== undefined)
    const hs = ords.map((o) => hitState.get(o))
    return (
      <PassageView
        key={it.ord}
        parts={parts}
        view={view}
        tr={trs.length ? trs.join(' ') : undefined}
        marks={marksByPage.get(it.page) ?? EMPTY_MARKS}
        query={searchQuery}
        hit={hs.includes('active') ? 'active' : hs.includes('hit') ? 'hit' : 'none'}
        flash={flash && flashOrd !== null && ords.includes(flashOrd) ? flash.key : null}
        eq={equationsByOrd.get(it.ord)}
        fig={figuresByCaption.get(it.ord)}
        paperId={paperId}
        vocab={vocab}
        boldFirst={prefs.boldFirst}
        glossTerms={termsByOrd.get(it.ord)}
        pageNo={prefs.pages ? pageNo : null}
        eqRunning={eqRunning}
        eqError={eqError}
        table={raw ? undefined : tablesByCaption.get(it.ord)}
        tblRunning={tblRunning}
        tblError={tblError}
        onRetryTables={retryTables}
        tableTr={tablesByCaption.get(it.ord) ? tableTr[String(it.ord)] : undefined}
        notes={parts.flatMap((p) => notesByOrd.get(p.ord) ?? [])}
        retranslating={ords.some((o) => retranslating.has(o))}
        onRetranslate={retranslateParts}
      />
    )
  }

  const renderBlock = (b: Block): ReactElement => {
    if (b.type === 'heading') {
      const H = b.level <= 1 ? 'h2' : b.level === 2 ? 'h3' : 'h4'
      return (
        <H key={b.key} className={`pd-tp-h pd-tp-h${Math.min(b.level, 3)}`} data-sec={b.title} data-page={b.page}>
          {prefs.pages && (
            <span className="pd-tp-pg" aria-hidden="true">
              {b.page}
            </span>
          )}
          {view === 'target' && headingTr[b.title] ? (
            <span title={`原文：${b.title}`}>{headingTr[b.title]}</span>
          ) : (
            <>
              {b.title}
              {view === 'both' && headingTr[b.title] && <span className="pd-tp-h-zh">{headingTr[b.title]}</span>}
            </>
          )}
        </H>
      )
    }
    return renderPassage(b.parts, b.pageStart ? b.item.page : null)
  }

  const chunks: Block[][] = []
  for (let i = 0; i < doc.blocks.length; i += CHUNK) chunks.push(doc.blocks.slice(i, i + CHUNK))

  const eqText = eqRunning
    ? `公式识别 ${eq.done}/${eq.total || '…'}${eq.failed ? ` · 失败 ${eq.failed}` : ''}`
    : eqError
      ? `公式识别失败：${eqError}`
      : eqPending
        ? `公式 ${eq.done}/${eq.total} 已识别`
        : ''

  const tblText = tblRunning
    ? `表格识别 ${tbl.ready}/${tbl.total || '…'}${tbl.failed ? ` · 失败 ${tbl.failed}` : ''}`
    : tblError
      ? `表格识别失败：${tblError}`
      : tblPending
        ? `表格 ${tbl.ready}/${tbl.total} 已识别${tbl.failed ? ` · 失败 ${tbl.failed}` : ''}`
        : ''

  return (
    <div className="pd-textreader" ref={containerRef} tabIndex={0} onScroll={onScroll} onClick={onClick} aria-label="论文文字阅读区域" style={{ ['--tp-size' as string]: `${textSize}px` }}>
      <FigViewer paperId={paperId} />
      {showBanner && (
        <div className="pd-tp-banner" role="status">
          {eqText && (
            <span className="pd-tp-bstat">
              {eqText}
              {!eqRunning && (eqError || eqPending) && (
                <button type="button" className="pd-tp-link" onClick={() => void startEquations(true)}>
                  {eqError ? '重试' : '开始识别'}
                </button>
              )}
            </span>
          )}
          {tblText && (
            <span className="pd-tp-bstat">
              {tblText}
              {!tblRunning && (tblError || tblPending) && (
                <button type="button" className="pd-tp-link" onClick={() => void startTables(true)}>
                  {tblError || tbl.failed ? '重试' : '开始识别'}
                </button>
              )}
            </span>
          )}
          {(translating || needTr) && (
            <span className="pd-tp-bstat">
              <Languages size={14} />
              {translating ? (
                <>
                  正在翻译 · 已译 {trDone}/{trTotal || '…'} 段{jobMsg && !/已翻译/.test(jobMsg) ? ` · ${jobMsg}` : ''}
                </>
              ) : trDone > 0 ? (
                `只翻译了 ${trDone}/${trTotal} 段，没有译文的段落显示原文`
              ) : (
                '这篇论文还没有翻译'
              )}
            </span>
          )}
          <span className="pd-spacer" />
          {translating ? (
            <Button size="sm" onClick={() => void cancelTranslation()}>
              取消翻译
            </Button>
          ) : needTr ? (
            <Button size="sm" variant="primary" onClick={() => void startTranslation()}>
              {trDone > 0 ? '继续翻译' : '翻译全文'}
            </Button>
          ) : null}
        </div>
      )}
      {(translating || eqRunning || tblRunning) && (
        <ProgressBar
          value={
            translating
              ? progress !== undefined ? Math.max(0.03, progress) : undefined
              : eqRunning
                ? eqProgress !== undefined ? Math.max(0.03, eqProgress) : undefined
                : tblProgress !== undefined ? Math.max(0.03, tblProgress) : undefined
          }
        />
      )}
      <div className="pd-textlayout">
        <article className="pd-textdoc" data-wide={wide} data-pages={prefs.pages}>
          <div className="pd-tp-opts" role="group" aria-label="显示选项">
            <label>
              <input type="checkbox" checked={prefs.raw} onChange={(e) => prefs.setRaw(e.target.checked)} />
              显示原始提取文字
            </label>
            <label>
              <input type="checkbox" checked={prefs.boldFirst} disabled={prefs.raw} onChange={(e) => prefs.setBoldFirst(e.target.checked)} />
              首句加粗
            </label>
            <label>
              <input type="checkbox" checked={prefs.pages} onChange={(e) => prefs.setPages(e.target.checked)} />
              显示页码
            </label>
            {width >= TOC_MIN_WIDTH && (
              <label>
                <input type="checkbox" checked={prefs.toc} onChange={(e) => prefs.setToc(e.target.checked)} />
                本文结构
              </label>
            )}
          </div>
          {doc.front && <FrontBlock title={paperTitle} front={doc.front} titleZh={headingTr[paperTitle]} view={view} />}
          {abstractText.length > 0 && (
            <section className="pd-abstract" data-page={abstractText[0].page}>
              <div className="pd-abstract-label">摘要</div>
              {abstractText.map((it) => renderPassage([it], null))}
            </section>
          )}
          {chunks.map((c, i) => (
            <div key={i} className="pd-tchunk">
              {c.map(renderBlock)}
            </div>
          ))}
          {doc.references.length > 0 && (
            <section className="pd-tpage--refs" data-page={doc.references[0].page}>
              <button type="button" className="pd-tp-refhead" onClick={() => setRefsOpen((v) => !v)} aria-expanded={refsOpen}>
                {refsOpen ? <ChevronDown size={14} /> : <ChevronRight size={14} />}
                参考文献 {doc.references.length}
              </button>
              {refsOpen &&
                doc.references.map((r) => (
                  <div
                    key={r.ord}
                    className="pd-tp pd-tp--ref"
                    data-ord={r.ord}
                    data-flash={flashOrd === r.ord && flash ? flash.key % 2 : undefined}
                    {...RefEntryHandlers(refOrdToIndex.get(r.ord))}
                  >
                    <p className="pd-tp-p">
                      <Linkified text={r.text} />
                    </p>
                  </div>
                ))}
            </section>
          )}
          <label className="pd-tp-noise">
            <input type="checkbox" checked={showNoise} onChange={(e) => setShowNoise(e.target.checked)} />
            显示图内文字{!showNoise && doc.hidden > 0 ? ` · 已隐藏 ${doc.hidden} 段` : ''}
          </label>
        </article>
        {showToc && <TocRail items={toc} active={activeSec} tables={tocTables} />}
      </div>
      <RefLayer />
      <NotePeek />
    </div>
  )
}
