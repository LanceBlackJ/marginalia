import { useMemo, useState } from 'react'
import { Skeleton, toast } from '@/components/ui'
import { textApi, type TableAnalysisItem } from '@/lib/api.text'
import { useBlobUrl } from '@/features/figures/useBlobUrl'
import { ZoomImage } from '@/components/ZoomImage'
import { numericColumns, tableToMarkdown, tableToTsv } from './tableModel'
import './tables.css'

const PREVIEW_ROWS = 5

interface Block {
  title: string
  columns: string[]
  rows: string[][]
  confidence: number
}

function DataTable({ block, label }: { block: Block; label: string }) {
  const [all, setAll] = useState(false)
  const num = useMemo(() => numericColumns(block), [block])
  const rows = all ? block.rows : block.rows.slice(0, PREVIEW_ROWS)
  const hasHeader = block.columns.some((c) => c.trim() !== '')
  const copy = async (kind: 'tsv' | 'md'): Promise<void> => {
    try {
      await navigator.clipboard.writeText(kind === 'tsv' ? tableToTsv(block) : tableToMarkdown(block))
      toast.success(kind === 'tsv' ? '已复制，可粘贴进 Excel' : '已复制为 Markdown 表格')
    } catch {
      toast.error('复制失败', '没有剪贴板权限')
    }
  }
  return (
    <div className="pd-fd-block">
      {block.title && <div className="pd-fd-title">{block.title}</div>}
      <div className="pd-fd-wrap" tabIndex={0} aria-label={`${label} ${block.title || '数据表'}`}>
        <table>
          {hasHeader && (
            <thead>
              <tr>
                {block.columns.map((c, ci) => (
                  <th key={ci} scope="col" className={num[ci] ? 'pd-num' : undefined}>
                    {c}
                  </th>
                ))}
              </tr>
            </thead>
          )}
          <tbody>
            {rows.map((r, ri) => (
              <tr key={ri}>
                {block.columns.map((_c, ci) => (
                  <td key={ci} className={num[ci] ? 'pd-num' : undefined}>
                    {r[ci] ?? ''}
                  </td>
                ))}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <div className="pd-fd-actions">
        {block.rows.length > PREVIEW_ROWS && (
          <button type="button" className="pd-fd-act" onClick={() => setAll((v) => !v)} aria-expanded={all}>
            {all ? '收起' : `展开 ${block.rows.length} 行`}
          </button>
        )}
        <button type="button" className="pd-fd-act" onClick={() => void copy('tsv')}>
          复制为 TSV
        </button>
        <button type="button" className="pd-fd-act" onClick={() => void copy('md')}>
          复制为 Markdown
        </button>
        {block.confidence < 0.8 && <span>重建可能有误，请对照原图</span>}
      </div>
    </div>
  )
}

interface Props {
  paperId: string
  t: TableAnalysisItem
  running: boolean
  onRetry: () => void
}

/**
 * 图里的数据网格（如 Fig.5 子图下方的 Avg score 表）：用 PDF 文字层的词坐标重建成真正的表格，放在图注下面，
 * 不再是一堆数字堆在正文里。每块（并排的子图）一张表，默认显示前几行，可展开、可复制，可对照原图。
 */
export function FigureDataBlock({ paperId, t, running, onRetry }: Props) {
  const [showImg, setShowImg] = useState(false)
  const { url } = useBlobUrl(showImg ? () => textApi.tableVector(paperId, t.caption_ord) : null, [paperId, t.caption_ord, showImg])
  const blocks: Block[] = t.status === 'ready' ? (t.data?.blocks ?? []) : []
  const ords = t.body_ords.join(' ')
  if (blocks.length === 0) {
    const active = t.status === 'pending' || t.status === 'running' || (running && t.status === 'none')
    return (
      <div className="pd-tp-table pd-tp-table--wait pd-fd" data-ords={ords}>
        <p className="pd-tp-note">
          {active ? '正在还原图中的数据表…' : t.status === 'failed' ? (
            <>
              图中数据没能还原成表格：{t.error ?? '文字层里找不到整齐的行列'}。
              <button type="button" className="pd-tp-link" onClick={onRetry}>
                重试
              </button>
            </>
          ) : '等待还原图中的数据表…'}
        </p>
      </div>
    )
  }
  return (
    <details className="pd-fd" open data-ords={ords}>
      <summary>
        {t.label} 图中数据 · {blocks.length} 张表
      </summary>
      <p className="pd-tp-note">这些数字是从 PDF 文字层按位置还原出来的，不是原文段落；请对照原图确认。</p>
      {blocks.map((b, i) => (
        <DataTable key={i} block={b} label={t.label} />
      ))}
      <div className="pd-fd-actions">
        <button type="button" className="pd-fd-act" onClick={() => setShowImg((v) => !v)} aria-expanded={showImg}>
          {showImg ? '收起原图' : '对照原图'}
        </button>
      </div>
      {showImg && (url ? <ZoomImage src={url} alt={`${t.label} 数据区原图`} className="pd-tp-figimg" /> : <Skeleton height={120} />)}
    </details>
  )
}
