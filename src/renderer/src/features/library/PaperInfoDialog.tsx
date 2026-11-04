import { useState } from 'react'
import { ExternalLink, Paperclip } from 'lucide-react'
import { Button, Dialog, Tag, toast } from '@/components/ui'
import { api, errorMessage } from '@/lib/api'
import { pickPdfFiles, openExternal } from '@/lib/native'
import { formatAuthors } from '@/lib/format'
import { useJobsStore } from '@/stores/jobsStore'
import type { PaperDetail, PaperSummary } from '@/lib/types'
import { useEffect } from 'react'

/** 无全文（metadata_only）或解析失败的论文详情：展示来源与“附加 PDF”。 */
export function PaperInfoDialog({ paper, onClose, onDelete }: { paper: PaperSummary | null; onClose: () => void; onDelete: (p: PaperSummary) => void }) {
  const [detail, setDetail] = useState<PaperDetail | null>(null)
  const [busy, setBusy] = useState(false)

  useEffect(() => {
    setDetail(null)
    if (!paper) return
    let alive = true
    api.papers
      .get(paper.id)
      .then((d) => alive && setDetail(d))
      .catch(() => undefined)
    return () => {
      alive = false
    }
  }, [paper])

  if (!paper) return null

  const attach = async (): Promise<void> => {
    setBusy(true)
    try {
      const paths = await pickPdfFiles()
      if (paths.length === 0) return
      const { job_id } = await api.papers.attachPdf(paper.id, paths[0])
      useJobsStore.getState().track(job_id)
      toast.info('正在附加 PDF', '解析完成后即可阅读。')
      onClose()
    } catch (err) {
      toast.error('附加 PDF 失败', errorMessage(err))
    } finally {
      setBusy(false)
    }
  }

  return (
    <Dialog
      open
      onClose={onClose}
      title={paper.status === 'failed' ? '解析失败' : '暂无全文'}
      width={560}
      footer={
        <>
          <Button variant="danger" onClick={() => onDelete(paper)}>
            从文献库删除
          </Button>
          <span className="pd-spacer" />
          <Button onClick={onClose}>关闭</Button>
          <Button variant="primary" icon={<Paperclip size={14} />} loading={busy} onClick={() => void attach()}>
            {paper.status === 'failed' ? '重新附加 PDF' : '附加 PDF'}
          </Button>
        </>
      }
    >
      <div style={{ display: 'flex', flexDirection: 'column', gap: 12 }}>
        <div>
          <div style={{ fontWeight: 600 }}>{paper.title}</div>
          <div className="pd-muted pd-aux" style={{ marginTop: 4 }}>
            {formatAuthors(paper.authors, 6)}
            {paper.venue ? ` · ${paper.venue}` : ''}
            {paper.year ? ` · ${paper.year}` : ''}
          </div>
          {paper.doi && (
            <div className="pd-aux pd-weak" style={{ marginTop: 2 }}>
              DOI：{paper.doi}
            </div>
          )}
        </div>

        {paper.status === 'failed' ? (
          <div style={{ color: 'var(--danger)' }}>{paper.parse_error ?? '这篇论文的 PDF 没有被成功解析。'}</div>
        ) : (
          <p className="pd-muted">
            没有找到可合法下载的开放获取全文，所以只保存了元数据。如果你有权限访问全文，请通过下面的来源自行下载，然后点击“附加 PDF”。
          </p>
        )}

        {detail && detail.access_info.length > 0 && (
          <div>
            <div className="pd-field-label" style={{ marginBottom: 6 }}>
              可用来源
            </div>
            <div style={{ display: 'flex', flexDirection: 'column', gap: 6 }}>
              {detail.access_info.map((l) => (
                <button
                  key={l.url}
                  type="button"
                  className="pd-btn pd-btn--ghost"
                  style={{ justifyContent: 'flex-start', height: 'auto', padding: '6px 8px', whiteSpace: 'normal', textAlign: 'left' }}
                  onClick={() => void openExternal(l.url)}
                >
                  <ExternalLink size={14} />
                  <span style={{ flex: 1 }}>
                    {l.label}
                    <span className="pd-weak pd-aux" style={{ display: 'block', fontWeight: 400, wordBreak: 'break-all' }}>
                      {l.url}
                    </span>
                  </span>
                </button>
              ))}
            </div>
          </div>
        )}
        {detail && detail.access_info.length === 0 && paper.status === 'metadata_only' && <Tag tone="outline">没有记录到可用来源</Tag>}
      </div>
    </Dialog>
  )
}
