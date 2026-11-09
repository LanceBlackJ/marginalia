import { useEffect, useState } from 'react'
import { Check, ExternalLink, Paperclip } from 'lucide-react'
import { Button, Dialog, Skeleton, toast } from '@/components/ui'
import { api, errorMessage } from '@/lib/api'
import { openExternal, openPaperBrowser, pickPdfFiles } from '@/lib/native'
import { formatAuthors } from '@/lib/format'
import { useJobsStore } from '@/stores/jobsStore'
import type { PaperDetail } from '@/lib/types'
import { bestDownloadLink, otherDownloadLinks, useManualDownload, type ManualDownloadItem } from './manualDownload'

function Row({ item }: { item: ManualDownloadItem }) {
  const [detail, setDetail] = useState<PaperDetail | null>(null)
  const [busy, setBusy] = useState(false)
  const [attached, setAttached] = useState(false)

  useEffect(() => {
    let alive = true
    api.papers
      .get(item.paperId)
      .then((d) => alive && setDetail(d))
      .catch(() => undefined)
    return () => {
      alive = false
    }
  }, [item.paperId])

  const link = detail ? bestDownloadLink(detail) : null
  const others = detail ? otherDownloadLinks(detail) : []

  const attach = async (): Promise<void> => {
    setBusy(true)
    try {
      const paths = await pickPdfFiles()
      if (paths.length === 0) return
      const { job_id } = await api.papers.attachPdf(item.paperId, paths[0])
      useJobsStore.getState().track(job_id)
      setAttached(true)
      toast.info('正在附加 PDF', '解析完成后即可阅读。')
    } catch (err) {
      toast.error('附加 PDF 失败', errorMessage(err))
    } finally {
      setBusy(false)
    }
  }

  return (
    <li>
      {detail ? (
        <div style={{ minWidth: 0 }}>
          <div style={{ fontWeight: 600 }}>{detail.title}</div>
          <div className="pd-muted pd-aux" style={{ marginTop: 2 }}>
            {formatAuthors(detail.authors, 4)}
            {detail.venue ? ` · ${detail.venue}` : ''}
            {detail.year ? ` · ${detail.year}` : ''}
          </div>
        </div>
      ) : (
        <Skeleton height={34} />
      )}
      <p className="pd-aux pd-muted" style={{ margin: '6px 0 0' }}>
        {item.reason}
      </p>
      <div className="pd-row" style={{ marginTop: 8, flexWrap: 'wrap' }}>
        {link && (
          <Button size="sm" variant="primary" icon={<ExternalLink size={14} />} onClick={() => void openPaperBrowser(link)} title="在内置浏览器里打开：需要登录就直接登录，点到 PDF 会自动导入">
            打开下载页面（可登录）
          </Button>
        )}
        <Button size="sm" icon={attached ? <Check size={14} /> : <Paperclip size={14} />} loading={busy} disabled={attached} onClick={() => void attach()}>
          {attached ? '已选择 PDF' : '选择已下载的 PDF'}
        </Button>
      </div>
      {others.length > 0 && (
        <div className="pd-aux pd-muted" style={{ marginTop: 6 }}>
          其他来源：
          {others.map((l) => (
            <button key={l.url} type="button" className="pd-tp-link" style={{ marginLeft: 8 }} onClick={() => void openExternal(l.url)}>
              {l.label}
            </button>
          ))}
        </div>
      )}
    </li>
  )
}

/** 导入时没能自动下载到全文：弹出这个窗口，打开下载页面，下载后把 PDF 选回来，接到已经保存的题录上。 */
export function ManualDownloadDialog() {
  const items = useManualDownload((s) => s.items)
  const close = useManualDownload((s) => s.close)
  return (
    <Dialog
      open={items.length > 0}
      onClose={close}
      title={items.length > 1 ? `有 ${items.length} 篇没能自动下载全文` : '没能自动下载全文'}
      width={620}
      footer={<Button onClick={close}>稍后再说</Button>}
    >
      <p className="pd-muted" style={{ marginTop: 0 }}>
        题录已经保存。这些论文没有找到可以直接下载的开放全文，需要你自己去下载。下载到本机后，点“选择已下载的 PDF”，会接到对应的题录上，之后可以直接阅读。
      </p>
      <ul style={{ listStyle: 'none', margin: 0, padding: 0, display: 'flex', flexDirection: 'column', gap: 16 }}>
        {items.map((it) => (
          <Row key={it.paperId} item={it} />
        ))}
      </ul>
    </Dialog>
  )
}
