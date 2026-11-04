import { useCallback, useEffect, useRef, useState } from 'react'
import { Button, Dialog, ProgressBar, Tag } from '@/components/ui'
import { api, errorMessage } from '@/lib/api'
import { formatBytes } from '@/lib/format'
import { materialsApi } from '@/lib/api.materials'
import type { DiscoverCandidate, DiscoverResult } from '@/lib/types.materials'
import { useMaterialsStore } from './materialsStore'

const AUTO_LIMIT = 20 * 1024 * 1024
const SOURCE_LABEL: Record<DiscoverCandidate['source'], string> = {
  publisher: '出版商页面',
  crossref: 'Crossref',
  arxiv: 'arXiv',
  code: '代码仓库'
}

type Phase = 'idle' | 'running' | 'done' | 'error'

/** 自动查找补充材料：只列出候选，由用户勾选确认后才会关联/下载；不会绕过付费墙或登录。 */
export function DiscoverDialog({ open, onClose }: { open: boolean; onClose: () => void }) {
  const paperId = useMaterialsStore((s) => s.paperId)
  const linkCandidates = useMaterialsStore((s) => s.linkCandidates)
  const [phase, setPhase] = useState<Phase>('idle')
  const [message, setMessage] = useState('')
  const [result, setResult] = useState<DiscoverResult | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [picked, setPicked] = useState<Set<string>>(new Set())
  const [linking, setLinking] = useState(false)
  const run = useRef(0)

  const start = useCallback(async (): Promise<void> => {
    if (!paperId) return
    const my = ++run.current
    setPhase('running')
    setMessage('正在查找出版商页面、Crossref、arXiv 与正文里提到的代码仓库…')
    setResult(null)
    setError(null)
    try {
      const { job_id } = await materialsApi.discover(paperId)
      for (let i = 0; i < 120; i++) {
        await new Promise((r) => window.setTimeout(r, i < 3 ? 700 : 1500))
        if (run.current !== my) return
        const job = await api.jobs.get(job_id)
        if (job.message) setMessage(job.message)
        if (job.status === 'completed') {
          const res = job.result as DiscoverResult
          setResult(res)
          setPicked(new Set(res.candidates.filter((c) => !c.already_linked && c.kind !== 'code' && c.size != null && c.size <= AUTO_LIMIT).map((c) => c.id)))
          setPhase('done')
          return
        }
        if (job.status === 'failed' || job.status === 'cancelled') {
          setError(job.error ?? '查找没有成功')
          setPhase('error')
          return
        }
      }
      setError('查找超时，可以稍后重试')
      setPhase('error')
    } catch (err) {
      if (run.current === my) {
        setError(errorMessage(err))
        setPhase('error')
      }
    }
  }, [paperId])

  useEffect(() => {
    if (open) void start()
    return () => {
      run.current++
    }
  }, [open, start])

  const toggle = (id: string): void =>
    setPicked((prev) => {
      const next = new Set(prev)
      if (next.has(id)) next.delete(id)
      else next.add(id)
      return next
    })

  const link = async (): Promise<void> => {
    const chosen = (result?.candidates ?? []).filter((c) => picked.has(c.id))
    if (chosen.length === 0) return
    setLinking(true)
    await linkCandidates(chosen)
    setLinking(false)
    onClose()
  }

  const needApproval = (result?.candidates ?? []).filter((c) => picked.has(c.id) && (c.kind === 'code' || c.size == null || c.size > AUTO_LIMIT)).length

  return (
    <Dialog
      open={open}
      onClose={onClose}
      title="自动查找补充材料"
      width={620}
      footer={
        phase === 'done' ? (
          <>
            <span className="pd-aux pd-weak" style={{ flex: 1 }}>
              {needApproval > 0 ? `其中 ${needApproval} 项较大或为代码包，下载前会再请你批准。` : '勾选后才会关联，文件会在后台下载。'}
            </span>
            <Button variant="ghost" onClick={onClose}>
              取消
            </Button>
            <Button variant="primary" disabled={picked.size === 0} loading={linking} onClick={() => void link()}>
              关联所选{picked.size > 0 ? ` ${picked.size}` : ''}
            </Button>
          </>
        ) : (
          <Button variant="ghost" onClick={onClose}>
            关闭
          </Button>
        )
      }
    >
      {phase === 'running' && (
        <div style={{ padding: '8px 0 16px' }}>
          <p className="pd-aux" style={{ margin: '0 0 12px' }}>
            {message}
          </p>
          <ProgressBar />
        </div>
      )}

      {phase === 'error' && (
        <div>
          <div className="pd-field-error">{error}</div>
          <Button size="sm" style={{ marginTop: 12 }} onClick={() => void start()}>
            重试
          </Button>
        </div>
      )}

      {phase === 'done' && result && (
        <>
          {result.candidates.length === 0 ? (
            <p style={{ margin: '0 0 8px' }}>没有自动找到可下载的补充材料。你可以在浏览器里下载后，用“添加 → 本地文件”导入。</p>
          ) : (
            <p className="pd-aux pd-weak" style={{ margin: '0 0 12px' }}>
              找到 {result.candidates.length} 个候选。这些只是建议，是否属于这篇论文请你确认。
            </p>
          )}
          {result.candidates.length > 0 && (
            <ul className="pd-disc-list">
              {result.candidates.map((c) => (
                <li key={c.id} className="pd-disc-item" data-disabled={c.already_linked ? 'true' : undefined}>
                  <input
                    id={`disc-${c.id}`}
                    type="checkbox"
                    checked={picked.has(c.id)}
                    disabled={c.already_linked}
                    onChange={() => toggle(c.id)}
                  />
                  <label htmlFor={`disc-${c.id}`} style={{ flex: 1, minWidth: 0, cursor: c.already_linked ? 'default' : 'pointer' }}>
                    <div style={{ fontWeight: 500, wordBreak: 'break-all' }}>{c.filename}</div>
                    <div className="pd-mat-meta">
                      <span>{c.label}</span>
                      <span className="pd-sep">{c.size != null ? formatBytes(c.size) : '大小未知'}</span>
                      <span className="pd-sep">{c.category_guess}</span>
                      <span className="pd-sep">
                        <Tag tone="outline">{SOURCE_LABEL[c.source] ?? c.source}</Tag>
                      </span>
                      {c.already_linked && (
                        <span className="pd-sep">
                          <Tag tone="success">已关联</Tag>
                        </span>
                      )}
                      {!c.already_linked && (c.kind === 'code' || c.size == null || c.size > AUTO_LIMIT) && (
                        <span className="pd-sep">
                          <Tag tone="warning">需批准后下载</Tag>
                        </span>
                      )}
                    </div>
                    <p className="pd-mat-basis">{c.basis}</p>
                  </label>
                </li>
              ))}
            </ul>
          )}
          {result.notes.length > 0 && (
            <ul className="pd-disc-notes">
              {result.notes.map((n, i) => (
                <li key={i}>{n}</li>
              ))}
            </ul>
          )}
        </>
      )}
    </Dialog>
  )
}
