import { useEffect, useState } from 'react'
import { Copy, Download } from 'lucide-react'
import { Button, Dialog, Segmented, Skeleton, toast } from '@/components/ui'
import { errorMessage } from '@/lib/api'
import { CITATION_FORMATS, exportApi2, type CitationExport, type CitationFormat } from '@/lib/api.export'
import { downloadBlob, downloadText } from '@/features/shared/download'

export interface ExportScope {
  /** 对话框标题里的范围说明，如“《某论文》”“标签：注意力”“全部文献”。 */
  label: string
  paperIds?: string[]
  tagId?: string
}

type Tab = 'citations' | 'bundle'

/**
 * 导出对话框：引文（BibTeX / RIS / CSL JSON / GB-T 7714 / APA）与阅读成果合集（Markdown / Word）。
 * 内容都由后端从库里生成：引文只含已有的元数据，缺的字段不编造；合集里 AI 生成的内容有明确标识。
 */
export function ExportDialog({ scope, onClose }: { scope: ExportScope | null; onClose: () => void }) {
  const [tab, setTab] = useState<Tab>('citations')
  const [format, setFormat] = useState<CitationFormat>('bibtex')
  const [data, setData] = useState<CitationExport | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [loading, setLoading] = useState(false)
  const [parts, setParts] = useState({ notes: true, annotations: true, cards: true, items: true })
  const [busy, setBusy] = useState(false)

  useEffect(() => {
    if (!scope || tab !== 'citations') return
    let alive = true
    setLoading(true)
    setError(null)
    exportApi2
      .citations({ format, paper_ids: scope.paperIds, tag_id: scope.tagId })
      .then((r) => alive && setData(r))
      .catch((err) => {
        if (alive) {
          setData(null)
          setError(errorMessage(err))
        }
      })
      .finally(() => alive && setLoading(false))
    return () => {
      alive = false
    }
  }, [scope, format, tab])

  const copy = async (): Promise<void> => {
    if (!data) return
    try {
      await navigator.clipboard.writeText(data.content)
      toast.success('已复制', `${data.count} 篇论文的${data.label}`)
    } catch {
      toast.error('复制失败', '没有剪贴板权限，可以改用“保存为文件”。')
    }
  }

  const saveBundle = async (kind: 'md' | 'docx'): Promise<void> => {
    if (!scope) return
    setBusy(true)
    try {
      const body = { paper_ids: scope.paperIds, ...parts }
      if (kind === 'md') {
        const r = await exportApi2.bundleMarkdown(body)
        downloadText(r.filename, r.content)
        toast.success('已导出 Markdown', `${r.count} 条内容`)
      } else {
        downloadBlob('Marginalia_阅读成果.docx', await exportApi2.bundleDocx(body))
        toast.success('已导出 Word', 'Marginalia_阅读成果.docx')
      }
    } catch (err) {
      toast.error('导出失败', errorMessage(err))
    } finally {
      setBusy(false)
    }
  }

  const anyPart = parts.notes || parts.annotations || parts.cards || parts.items

  return (
    <Dialog
      open={!!scope}
      onClose={onClose}
      title={`导出 · ${scope?.label ?? ''}`}
      width={620}
      footer={
        tab === 'citations' ? (
          <>
            <Button variant="ghost" onClick={onClose}>
              关闭
            </Button>
            <Button icon={<Copy size={14} />} disabled={!data} onClick={() => void copy()}>
              复制
            </Button>
            <Button variant="primary" icon={<Download size={14} />} disabled={!data} onClick={() => data && downloadText(data.filename, data.content, `${data.mime};charset=utf-8`)}>
              保存为文件
            </Button>
          </>
        ) : (
          <>
            <Button variant="ghost" onClick={onClose}>
              关闭
            </Button>
            <Button icon={<Download size={14} />} loading={busy} disabled={!anyPart} onClick={() => void saveBundle('md')}>
              Markdown
            </Button>
            <Button variant="primary" icon={<Download size={14} />} loading={busy} disabled={!anyPart} onClick={() => void saveBundle('docx')}>
              Word .docx
            </Button>
          </>
        )
      }
    >
      <Segmented<Tab>
        variant="text"
        aria-label="导出内容"
        value={tab}
        onChange={setTab}
        options={[
          { value: 'citations', label: '引文' },
          { value: 'bundle', label: '笔记与知识' }
        ]}
      />
      {tab === 'citations' ? (
        <div style={{ marginTop: 'var(--sp-lg)' }}>
          <Segmented<CitationFormat>
            aria-label="引文格式"
            value={format}
            onChange={setFormat}
            options={CITATION_FORMATS.map((f) => ({ value: f.value, label: f.label }))}
          />
          <p className="pd-aux pd-muted" style={{ margin: 'var(--sp-sm) 0' }}>
            {CITATION_FORMATS.find((f) => f.value === format)?.hint}。字段来自文库里的元数据，缺失的字段不会编造，库里没有卷期页码。
          </p>
          {loading ? (
            <Skeleton height={120} />
          ) : error ? (
            <p className="pd-status-text pd-status-text--danger">{error}</p>
          ) : (
            data && (
              <>
                <pre className="pd-export-preview" aria-label="导出预览">
                  {data.content}
                </pre>
                <p className="pd-aux pd-muted">共 {data.count} 篇</p>
              </>
            )
          )}
        </div>
      ) : (
        <div style={{ marginTop: 'var(--sp-lg)', display: 'flex', flexDirection: 'column', gap: 'var(--sp-sm)' }}>
          <p className="pd-aux pd-muted">把这些论文的内容汇总成一份文档，每条都带页码和原文片段。AI 生成的内容会明确标注，和你自己写的、论文原文区分开。</p>
          {(
            [
              ['notes', '我的笔记'],
              ['annotations', '我的批注'],
              ['cards', 'AI 研究卡片'],
              ['items', '已确认的知识条目']
            ] as const
          ).map(([k, label]) => (
            <label key={k} className="pd-export-check">
              <input type="checkbox" checked={parts[k]} onChange={(e) => setParts({ ...parts, [k]: e.target.checked })} />
              {label}
            </label>
          ))}
          {!anyPart && <p className="pd-status-text pd-status-text--danger">至少选择一类内容。</p>}
        </div>
      )}
    </Dialog>
  )
}
