import { memo, useEffect, useMemo, useRef, useState, type CSSProperties } from 'react'
import { createPortal } from 'react-dom'
import { Sigma } from 'lucide-react'
import { Tex } from '@/features/formula/Tex'
import { useNotesStore } from '@/features/notes/notesStore'
import { useTextStore } from '@/features/text/textStore'
import { usePrefs } from '@/stores/prefsStore'
import type { BBox } from '@/lib/types'
import { useMarksStore } from '../marksStore'
import { useReaderStore } from '../readerStore'
import { explainEquation } from '../selectionActions'
import { buildRegions, regionsOnPage, type Region } from '../regions'

const KIND_LABEL: Record<Region['kind'], string> = { equation: '公式', figure: '图', table: '表' }

function px(b: BBox, scale: number): CSSProperties {
  return { left: b[0] * scale, top: b[1] * scale, width: (b[2] - b[0]) * scale, height: (b[3] - b[1]) * scale }
}

/** 点击角标后在角标旁边展开的小面板：公式显示 KaTeX 与解释，图/表给出识别入口与现有结果。 */
function RegionPopover({ region, anchor, onClose }: { region: Region; anchor: DOMRect; onClose: () => void }) {
  const equationsByOrd = useTextStore((s) => s.equationsByOrd)
  const tablesByCaption = useTextStore((s) => s.tablesByCaption)
  const startEquations = useTextStore((s) => s.startEquations)
  const startTables = useTextStore((s) => s.startTables)
  const eqJobId = useTextStore((s) => s.eqJobId)
  const tblJobId = useTextStore((s) => s.tblJobId)
  const focusFigure = useReaderStore((s) => s.focusFigure)
  const ref = useRef<HTMLDivElement>(null)

  useEffect(() => {
    const onDown = (e: MouseEvent): void => {
      if (ref.current && !ref.current.contains(e.target as Node) && !(e.target as HTMLElement).closest('[data-smart-badge]')) onClose()
    }
    const onKey = (e: KeyboardEvent): void => {
      if (e.key === 'Escape') onClose()
    }
    document.addEventListener('mousedown', onDown)
    document.addEventListener('keydown', onKey)
    return () => {
      document.removeEventListener('mousedown', onDown)
      document.removeEventListener('keydown', onKey)
    }
  }, [onClose])

  const left = Math.max(8, Math.min(anchor.left - 300, window.innerWidth - 348))
  const top = Math.max(60, Math.min(anchor.bottom + 6, window.innerHeight - 260))
  const info = region.kind === 'equation' ? Array.from(equationsByOrd.values()).find((e) => e.id === region.id) : undefined
  const table = region.kind === 'table' && region.ord !== null ? tablesByCaption.get(region.ord) : undefined

  let body
  if (region.kind === 'equation') {
    body = info?.latex ? (
      <>
        <div className="pd-smart-tex">
          <Tex latex={info.latex.replace(/\\tag\*?\{[^}]*\}/g, '')} display />
        </div>
        {(info.confidence === 'low' || info.warnings.length > 0) && <p className="pd-smart-warn">识别可能有误，请对照原图确认{info.warnings[0] ? `：${info.warnings[0]}` : ''}</p>}
        <div className="pd-smart-actions">
          <button
            type="button"
            className="pd-tp-link"
            onClick={() => {
              explainEquation({ text: info.latex ?? '', page: region.page, clientRect: { left: 0, top: 0, right: 0, bottom: 0 }, rects: [], fromText: true } as never, region.id)
              onClose()
            }}
          >
            解释这个公式
          </button>
        </div>
      </>
    ) : info?.status === 'failed' ? (
      <>
        <p className="pd-smart-warn">识别失败{info.error ? `：${info.error}` : ''}</p>
        <button type="button" className="pd-tp-link" onClick={() => void startEquations(true)}>
          重试识别
        </button>
      </>
    ) : (
      <>
        <p className="pd-smart-note">这个公式还没识别。识别会把公式截图发给你配置的视觉模型，并记入隐私日志。</p>
        <button type="button" className="pd-tp-link" disabled={eqJobId !== null} onClick={() => void startEquations(true)}>
          {eqJobId !== null ? '正在识别全文公式…' : '点此识别'}
        </button>
      </>
    )
  } else if (region.kind === 'figure') {
    body = (
      <>
        <p className="pd-smart-note">图的识别结果和追问在右侧“图表”标签里。没有识别过的图会在那里提示“识别这张图”，识别会把截图发给视觉模型。</p>
        <button
          type="button"
          className="pd-tp-link"
          onClick={() => {
            focusFigure(region.id)
            onClose()
          }}
        >
          在“图表”标签中查看 / 识别
        </button>
      </>
    )
  } else if (table?.status === 'ready' && table.data) {
    const rows = table.data.rows.slice(0, 4)
    body = (
      <>
        <div className="pd-smart-mini">
          <table>
            <thead>
              <tr>{table.data.columns.slice(0, 5).map((c, i) => <th key={i}>{c}</th>)}</tr>
            </thead>
            <tbody>
              {rows.map((r, i) => (
                <tr key={i}>{r.slice(0, 5).map((c, j) => <td key={j}>{c}</td>)}</tr>
              ))}
            </tbody>
          </table>
        </div>
        <p className="pd-smart-note">只显示前 {rows.length} 行{table.data.rows.length > rows.length ? `，共 ${table.data.rows.length} 行` : ''}；完整表格和核对情况在文字界面里。</p>
      </>
    )
  } else {
    body = (
      <>
        <p className="pd-smart-note">{table?.status === 'failed' ? `识别失败：${table.error ?? ''}` : '这张表还没识别。识别会把表格截图发给视觉模型，并记入隐私日志。'}</p>
        <button type="button" className="pd-tp-link" disabled={tblJobId !== null} onClick={() => void startTables(true)}>
          {tblJobId !== null ? '正在识别表格…' : table?.status === 'failed' ? '重试识别' : '点此识别'}
        </button>
      </>
    )
  }

  return createPortal(
    <div ref={ref} className="pd-smart-pop" style={{ left, top }} role="dialog" aria-label={`${KIND_LABEL[region.kind]}${region.label ? ` ${region.label}` : ''}`}>
      <div className="pd-smart-pop-head">
        {KIND_LABEL[region.kind]}
        {region.label ? ` ${region.label}` : ''} · 第 {region.page} 页
      </div>
      {body}
    </div>,
    document.body
  )
}

/**
 * PDF 页面上的“智能层”：公式/图/表区域的角标（点击展开识别结果）、页边的笔记与批注标记、书签标记。
 * 角标只是入口：识别仍走已有接口（有隐私日志与用量记录），这里不会自动调用模型。
 */
export const PageSmartLayer = memo(function PageSmartLayer({ page, scale }: { page: number; scale: number }) {
  const enabled = usePrefs((s) => s.smartLayer)
  const eqList = useTextStore((s) => s.eqList)
  const figuresByCaption = useTextStore((s) => s.figuresByCaption)
  const tablesByCaption = useTextStore((s) => s.tablesByCaption)
  const items = useTextStore((s) => s.items)
  const notes = useNotesStore((s) => s.notes)
  const bookmarks = useMarksStore((s) => s.bookmarks)
  const annotations = useMarksStore((s) => s.annotations)
  const setRight = useReaderStore((s) => s.setRight)
  const setLeft = useReaderStore((s) => s.setLeft)
  const [open, setOpen] = useState<{ region: Region; rect: DOMRect } | null>(null)

  const regions = useMemo(() => regionsOnPage(buildRegions(eqList, figuresByCaption, tablesByCaption), page), [eqList, figuresByCaption, tablesByCaption, page])
  const ordBox = useMemo(() => {
    const m = new Map<number, BBox>()
    for (const it of items) if (it.page === page && it.bbox) m.set(it.ord, it.bbox)
    return m
  }, [items, page])

  if (!enabled) return null
  const pageNotes = notes.filter((n) => n.page === page)
  const pageBookmarks = bookmarks.filter((b) => b.page === page)
  const notedAnnotations = annotations.filter((a) => a.page === page && a.note)

  return (
    <div className="pd-page-smart">
      {regions.map((r) => (
        <button
          key={`${r.kind}-${r.id}`}
          type="button"
          data-smart-badge
          className="pd-smart-badge"
          data-kind={r.kind}
          style={{ left: r.bbox[2] * scale - 20, top: r.bbox[1] * scale - 2 }}
          title={`${KIND_LABEL[r.kind]}${r.label ? ` ${r.label}` : ''}：点击查看识别结果`}
          aria-label={`${KIND_LABEL[r.kind]}${r.label ? ` ${r.label}` : ''}`}
          onClick={(e) => {
            e.stopPropagation()
            setOpen(open?.region.kind === r.kind && open.region.id === r.id ? null : { region: r, rect: e.currentTarget.getBoundingClientRect() })
          }}
        >
          {r.kind === 'equation' ? <Sigma size={11} /> : KIND_LABEL[r.kind]}
        </button>
      ))}
      {regions.map((r) => open && open.region.kind === r.kind && open.region.id === r.id && <div key={`o-${r.kind}-${r.id}`} className="pd-smart-outline" style={px(r.bbox, scale)} />)}

      {/* 页边：笔记（有原文段落的放在那一段旁边，没有的叠放在页边上部）、带批注文字的高亮、书签 */}
      {pageNotes.map((n, i) => {
        const b = n.passage_ord !== null ? ordBox.get(n.passage_ord) : undefined
        const top = b ? b[1] * scale : 10 + i * 16
        return (
          <button key={`n-${n.id}`} type="button" className="pd-margin-mark" data-kind="note" style={{ top }} title={`笔记：${n.text.slice(0, 80) || n.quote?.slice(0, 80) || ''}`} aria-label="笔记标记" onClick={() => setRight(true, 'notes')} />
        )
      })}
      {notedAnnotations.map((a) => (
        <button key={`a-${a.id}`} type="button" className="pd-margin-mark" data-kind="annotation" style={{ top: a.rects[0][1] * scale }} title={`批注：${a.note}`} aria-label="批注标记" onClick={() => useMarksStore.getState().setActiveAnnotation({ id: a.id, x: 120, y: 160 })} />
      ))}
      {pageBookmarks.map((b, i) => (
        <button key={`b-${b.id}`} type="button" className="pd-bookmark-flag" style={{ right: 10 + i * 14 }} title={`书签：${b.label}`} aria-label="书签标记" onClick={() => setLeft(true, 'bookmarks')} />
      ))}
      {open && <RegionPopover region={open.region} anchor={open.rect} onClose={() => setOpen(null)} />}
    </div>
  )
})
