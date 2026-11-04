import { useCallback, useEffect, useRef, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { RefreshCw } from 'lucide-react'
import { Button, EmptyState, ProgressBar, Skeleton, toast } from '@/components/ui'
import { errorMessage } from '@/lib/api'
import { reproApi } from '@/lib/api.repro'
import type { AssessItem, AssessmentState, Evidence } from '@/lib/types.repro'
import { openPaperAt, useCodeStore } from '../codeStore'
import { ASSESS_STATUS_LABEL, evidenceLabel } from './reproUtils'
import { waitJob } from './waitJob'

function EvidenceList({ evidence }: { evidence: Evidence[] }) {
  const navigate = useNavigate()
  const paperId = useCodeStore((s) => s.paperId)
  const goToLines = useCodeStore((s) => s.goToLines)
  const setTab = useCodeStore((s) => s.setTab)
  if (evidence.length === 0) return null
  const open = (e: Evidence): void => {
    if (e.type === 'code') {
      setTab('code')
      void goToLines(e.path, Math.max(1, e.line_start), Math.max(1, e.line_end))
    } else if (e.type === 'paper' && paperId) {
      void openPaperAt(navigate, paperId, { page: e.page ?? undefined, ord: e.ord ?? undefined })
    }
  }
  return (
    <ul className="pd-repro-evidence">
      {evidence.map((e, i) => (
        <li key={`${evidenceLabel(e)}-${i}`}>
          {e.type === 'repo' ? (
            <span className="pd-aux">{evidenceLabel(e)}：</span>
          ) : (
            <button type="button" className="pd-link" onClick={() => open(e)} title={e.type === 'code' ? '在代码里查看' : '在论文里查看原文'}>
              {evidenceLabel(e)}
            </button>
          )}
          {e.quote && <span className="pd-aux pd-muted"> {e.quote.length > 110 ? `${e.quote.slice(0, 110)}…` : e.quote}</span>}
        </li>
      ))}
    </ul>
  )
}

function Item({ item }: { item: AssessItem }) {
  return (
    <li className="pd-repro-item" data-status={item.status}>
      <div className="pd-repro-item-head">
        <span className="pd-repro-badge" data-status={item.status}>
          {ASSESS_STATUS_LABEL[item.status]}
        </span>
        <span className="pd-repro-item-title">{item.title}</span>
      </div>
      <p className="pd-repro-item-text">{item.text}</p>
      {item.note && <p className="pd-aux pd-muted">{item.note}</p>}
      <EvidenceList evidence={item.evidence} />
    </li>
  )
}

/** A：可行性评估。只来自静态分析与论文原文；找不到写“没找到”，证据不足写“不确定”。 */
export function AssessView({ paperId, repoId, onNext }: { paperId: string; repoId: string; onNext: () => void }) {
  const [state, setState] = useState<AssessmentState | null>(null)
  const [running, setRunning] = useState(false)
  const [message, setMessage] = useState('')
  const [progress, setProgress] = useState<number | undefined>(undefined)
  const token = useRef({ cancelled: false })

  const load = useCallback(async (): Promise<void> => {
    try {
      setState(await reproApi.assessment(paperId, repoId))
    } catch (err) {
      toast.error('无法读取评估', errorMessage(err))
    }
  }, [paperId, repoId])

  useEffect(() => {
    token.current = { cancelled: false }
    setState(null)
    void load()
    return () => {
      token.current.cancelled = true
    }
  }, [load])

  const start = async (): Promise<void> => {
    setRunning(true)
    setMessage('正在开始…')
    setProgress(undefined)
    try {
      const { job_id } = await reproApi.startAssessment(paperId, repoId)
      const job = await waitJob(
        job_id,
        (j) => {
          setMessage(j.message || '评估中…')
          setProgress(j.progress)
        },
        token.current
      )
      if (!job) return
      if (job.status === 'completed') toast.success('评估完成')
      else toast.error('评估失败', job.error ?? '请到任务页查看原因')
      await load()
    } catch (err) {
      toast.error('无法开始评估', errorMessage(err))
    } finally {
      setRunning(false)
    }
  }

  if (!state) return <Skeleton height={16} />
  const report = state.report

  return (
    <div className="pd-repro-panel">
      <div className="pd-repro-actions">
        <Button variant="primary" loading={running} onClick={() => void start()} icon={report ? <RefreshCw size={14} /> : undefined}>
          {report ? '重新评估' : '生成可行性评估'}
        </Button>
        <span className="pd-aux pd-muted">
          只做静态分析，不运行代码。若已配置 AI，会把论文里提到数据集的段落发送给模型，并记入隐私日志，模型给出的名称必须能在原文里找到引文，否则丢弃。
        </span>
      </div>
      {running && (
        <div className="pd-repro-progress">
          <ProgressBar value={progress !== undefined ? Math.max(0.03, progress) : undefined} />
          <span className="pd-aux pd-muted">{message}</span>
        </div>
      )}
      {!report ? (
        <EmptyState title="还没有评估" description="评估会列出：环境、入口与命令、数据、资源、参数、风险与缺口，每一项都带证据：文件:行 或 论文页码。" />
      ) : (
        <>
          <section className="pd-repro-effort">
            <div className="pd-repro-effort-head">
              <span className="pd-repro-level">工作量：{report.effort.level}</span>
              <span className="pd-aux pd-muted">{report.effort.note}</span>
            </div>
            <ul>
              {report.effort.basis.map((b) => (
                <li key={b}>{b}</li>
              ))}
            </ul>
            <p className="pd-aux pd-muted">{report.note}</p>
          </section>
          {report.sections.map((sec) => (
            <section key={sec.key} className="pd-repro-section">
              <h3 className="pd-repro-h3">{sec.title}</h3>
              <ul className="pd-repro-items">
                {sec.items.map((it) => (
                  <Item key={it.key} item={it} />
                ))}
              </ul>
            </section>
          ))}
          <div className="pd-repro-actions">
            <Button onClick={onNext}>下一步：生成复现计划</Button>
          </div>
        </>
      )}
    </div>
  )
}
