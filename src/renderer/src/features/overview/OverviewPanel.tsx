import { ProcessingNote } from './ProcessingNote'
import { useCallback, useEffect, useMemo, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { AlertTriangle, ChevronRight, FileSearch, RefreshCw } from 'lucide-react'
import { Button, EmptyState, ProgressBar, Skeleton, Tag, toast } from '@/components/ui'
import { buildEvidenceMap, buildInvalidSet, CiteGroup } from '@/components/CiteChip'
import { Markdown } from '@/components/Markdown'
import { ApiError, api, errorMessage } from '@/lib/api'
import { formatRelativeTime } from '@/lib/format'
import { ANALYSIS_KEYS, type Analysis, type AnalysisKey, type Job } from '@/lib/types'
import { useJobsStore } from '@/stores/jobsStore'
import { useReaderStore } from '@/features/reader/readerStore'
import { firstSentence, leadSentence } from './overviewModel'

const LABELS: Record<AnalysisKey, string> = {
  background: '研究背景',
  motivation: '研究动机',
  challenges: '核心挑战',
  method: '方法设计',
  modules: '模块关系',
  experiment_design: '实验设计',
  results: '实验结果',
  conclusion: '研究结论',
  limitations: '局限性'
}

export function OverviewPanel() {
  const navigate = useNavigate()
  const paperId = useReaderStore((s) => s.paperId)
  const [analysis, setAnalysis] = useState<Analysis | null>(null)
  const [job, setJob] = useState<Job | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [needsConfig, setNeedsConfig] = useState(false)
  const [starting, setStarting] = useState(false)
  const [open, setOpen] = useState<Set<AnalysisKey>>(() => new Set())

  const load = useCallback(async (): Promise<Analysis | null> => {
    if (!paperId) return null
    try {
      const a = await api.analysis.get(paperId)
      setAnalysis(a)
      setError(null)
      return a
    } catch (err) {
      setError(errorMessage(err))
      return null
    }
  }, [paperId])

  useEffect(() => {
    setAnalysis(null)
    setJob(null)
    void load()
  }, [load])

  // 分析进行中：轮询任务进度，结束后重新读取结果
  const running = analysis?.status === 'pending' || analysis?.status === 'running'
  const jobId = analysis?.job_id ?? null
  useEffect(() => {
    if (!running) return
    let alive = true
    let timer: number | undefined
    const tick = async (): Promise<void> => {
      try {
        if (jobId) {
          const j = await api.jobs.get(jobId)
          if (alive) setJob(j)
        }
        const a = await api.analysis.get(paperId!)
        if (!alive) return
        setAnalysis(a)
        if (a.status === 'pending' || a.status === 'running') timer = window.setTimeout(() => void tick(), 1500)
      } catch {
        if (alive) timer = window.setTimeout(() => void tick(), 3000)
      }
    }
    timer = window.setTimeout(() => void tick(), 800)
    return () => {
      alive = false
      window.clearTimeout(timer)
    }
  }, [running, jobId, paperId])

  const run = async (): Promise<void> => {
    if (!paperId) return
    setStarting(true)
    setNeedsConfig(false)
    try {
      const { job_id } = await api.analysis.run(paperId)
      useJobsStore.getState().track(job_id)
      await load()
    } catch (err) {
      if (err instanceof ApiError && (err.code === 'not_configured' || err.code === 'no_api_key')) setNeedsConfig(true)
      else toast.error('无法开始分析', errorMessage(err))
    } finally {
      setStarting(false)
    }
  }

  const evMap = useMemo(() => buildEvidenceMap(analysis?.evidence), [analysis?.evidence])
  const invalid = analysis?.verification?.invalid_citations ?? []
  const invalidSet = useMemo(() => buildInvalidSet(invalid), [invalid])

  if (error && !analysis) {
    return (
      <div className="pd-rpanel-scroll">
        <div className="pd-field-error">论文概览加载失败：{error}</div>
        <Button size="sm" style={{ marginTop: 8 }} onClick={() => void load()}>
          重试
        </Button>
      </div>
    )
  }
  if (!analysis) {
    return (
      <div className="pd-rpanel-scroll">
        <Skeleton height={80} />
      </div>
    )
  }

  const configNotice = needsConfig && (
    <div className="pd-card-hint pd-card-hint--warn" role="note" style={{ marginBottom: 12 }}>
      <AlertTriangle size={14} />
      <span style={{ flex: 1 }}>还没有配置 AI 模型，无法生成分析。</span>
      <Button size="sm" onClick={() => navigate('/settings/ai')}>
        去设置
      </Button>
    </div>
  )

  if (analysis.status === 'ready' && analysis.content) {
    const content = analysis.content
    const hint = analysis.verification?.status
    const hintNotes = analysis.verification?.notes ?? []
    const keys = ANALYSIS_KEYS.filter((k) => content[k])
    const lead = leadSentence(content)
    const allOpen = keys.length > 0 && keys.every((k) => open.has(k))
    const toggle = (k: AnalysisKey): void =>
      setOpen((cur) => {
        const next = new Set(cur)
        if (next.has(k)) next.delete(k)
        else next.add(k)
        return next
      })
    return (
      <div className="pd-rpanel-scroll pd-ovp">
        {configNotice}
        {paperId && <ProcessingNote paperId={paperId} />}
        {hint && hint !== 'verified' && (
          <div className="pd-card-hint" role="note" style={{ marginBottom: 12 }}>
            <AlertTriangle size={13} />
            <span>
              {hintNotes.length > 0
                ? hintNotes.join(' ')
                : hint === 'no_evidence'
                  ? '这份分析没有附上可核对的原文依据，请谨慎对待。'
                  : '部分引用无法在论文中核实，请对照原文确认。'}
            </span>
          </div>
        )}
        {lead && (
          <section className="pd-ovp-lead" aria-label="一句话结论">
            <div className="pd-ovp-kicker">一句话结论</div>
            <p>{lead}</p>
          </section>
        )}
        <section className="pd-ovp-points" aria-label="要点">
          <div className="pd-psec-head">
            <h3 className="pd-psec-title">要点</h3>
            <button type="button" className="pd-link-btn" onClick={() => setOpen(allOpen ? new Set() : new Set(keys))}>
              {allOpen ? '全部收起' : '全部展开'}
            </button>
          </div>
          <ul className="pd-ovp-list">
            {keys.map((k) => {
              const sec = content[k]!
              const isOpen = open.has(k)
              return (
                <li key={k} className="pd-ovp-item" data-open={isOpen}>
                  <button type="button" className="pd-ovp-row" aria-expanded={isOpen} onClick={() => toggle(k)}>
                    <ChevronRight size={14} className="pd-ovp-chev" />
                    <span className="pd-ovp-label">{LABELS[k]}</span>
                    {!isOpen && <span className="pd-ovp-sum">{firstSentence(sec.text)}</span>}
                  </button>
                  {isOpen && (
                    <div className="pd-ovp-detail">
                      <Markdown text={sec.text} evidence={analysis.evidence} invalid={invalid} />
                      {sec.evidence_ords.length > 0 && (
                        <div className="pd-ov-ev">
                          <span className="pd-aux pd-weak" style={{ marginRight: 4 }}>
                            依据
                          </span>
                          <CiteGroup ords={sec.evidence_ords} evidence={evMap} invalid={invalidSet} />
                        </div>
                      )}
                    </div>
                  )}
                </li>
              )
            })}
          </ul>
        </section>
        <footer className="pd-ovp-foot">
          <span className="pd-aux pd-weak">
            {analysis.model ? `${analysis.model} · ` : ''}
            {formatRelativeTime(analysis.updated_at)}生成 · AI 生成，依据可点击核对
          </span>
          <Button size="sm" variant="ghost" icon={<RefreshCw size={13} />} loading={starting} onClick={() => void run()}>
            重新生成
          </Button>
        </footer>
      </div>
    )
  }

  if (running) {
    return (
      <div className="pd-rpanel-scroll">
        <EmptyState
          icon={<FileSearch size={20} />}
          title="正在生成论文分析…"
          description={job?.message || '正在阅读全文并整理背景、动机、方法和结论，请稍候。'}
          action={
            <div style={{ width: 220 }}>
              <ProgressBar value={job?.status === 'running' ? job.progress : undefined} />
            </div>
          }
        />
      </div>
    )
  }

  return (
    <div className="pd-rpanel-scroll">
      {configNotice}
      <EmptyState
        icon={<FileSearch size={20} />}
        title={analysis.status === 'failed' ? '论文分析没有成功' : '还没有论文分析'}
        description={
          analysis.status === 'failed' ? (
            (analysis.error ?? '生成过程中出现错误，可以重试。')
          ) : (
            <>
              生成后会得到研究背景、动机、核心挑战、方法、实验与结论的概要，每一条都附有可定位的原文依据。
              <div style={{ marginTop: 8 }}>
                <Tag tone="outline">会调用云端模型</Tag>
              </div>
            </>
          )
        }
        action={
          <Button variant="primary" loading={starting} onClick={() => void run()}>
            {analysis.status === 'failed' ? '重新生成' : '生成论文分析'}
          </Button>
        }
      />
    </div>
  )
}
