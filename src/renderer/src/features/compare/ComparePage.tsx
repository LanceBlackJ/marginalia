import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { useSearchParams } from 'react-router-dom'
import { Trash2 } from 'lucide-react'
import { Button, Dialog, EmptyState, Input, ProgressBar, Segmented, Tag, toast } from '@/components/ui'
import { api, errorMessage } from '@/lib/api'
import { compareApi } from '@/lib/api.discovery'
import type { CompareMode, ComparePreflight, Comparison, ComparisonSummary } from '@/lib/types.discovery'
import type { Job } from '@/lib/types'
import { useBasket } from '@/features/discovery/discoveryStore'
import { useLibraryPapers } from '@/features/shared/useLibraryPapers'
import { useJobsStore } from '@/stores/jobsStore'
import { CompareResultView } from './CompareResultView'
import '@/features/discovery/discovery.css'
import './compare.css'

const MODES: Array<{ value: CompareMode; label: string; hint: string }> = [
  { value: 'quick', label: '快速比较', hint: '读摘要与关键段落，给出总体结论和对比表。适合先判断几篇论文是不是同一类工作。' },
  { value: 'method', label: '方法比较', hint: '逐篇读方法、公式、实验与结果，按维度对比，附带原文证据。适合挑方法或写相关工作。' },
  { value: 'lineage', label: '研究脉络', hint: '在方法比较之上，加入论文之间的引用与改进关系图，只画有证据的关系。' }
]
const BASIS_TAG: Record<string, string> = { fulltext: '全文', abstract: '仅摘要', none: '无内容' }
const STATUS_LABEL: Record<string, string> = { pending: '排队中', running: '比较中', ready: '已完成', failed: '失败', cancelled: '已取消' }

function fmtTime(iso: string): string {
  const d = new Date(iso.endsWith('Z') || /[+-]\d\d:?\d\d$/.test(iso) ? iso : `${iso}Z`)
  return Number.isNaN(d.getTime()) ? iso : d.toLocaleString('zh-CN', { month: 'numeric', day: 'numeric', hour: '2-digit', minute: '2-digit' })
}

export function ComparePage() {
  const [params, setParams] = useSearchParams()
  const activeId = params.get('id')
  const ids = useBasket((s) => s.ids)
  const toggle = useBasket((s) => s.toggle)
  const clear = useBasket((s) => s.clear)
  const { items: papers, loading: papersLoading } = useLibraryPapers()
  const [filter, setFilter] = useState('')
  const [mode, setMode] = useState<CompareMode>('method')
  const [pre, setPre] = useState<{ loading: boolean; data?: ComparePreflight; error?: string }>({ loading: false })
  const [starting, setStarting] = useState(false)
  const [history, setHistory] = useState<ComparisonSummary[]>([])
  const [current, setCurrent] = useState<Comparison | null>(null)
  const [job, setJob] = useState<Job | null>(null)
  const [loadError, setLoadError] = useState<string | null>(null)
  const [toDelete, setToDelete] = useState<ComparisonSummary | null>(null)
  const finishedTick = useJobsStore((s) => s.finishedTick)

  const reloadHistory = useCallback(async (): Promise<void> => {
    try {
      setHistory((await compareApi.list()).items)
    } catch {
      /* 历史加载失败不阻塞新建 */
    }
  }, [])
  useEffect(() => {
    void reloadHistory()
  }, [reloadHistory, finishedTick])

  // 预检：选中 ≥2 篇后检查每篇的内容基础（全文/仅摘要/无）并给出阻塞项与警告
  const idsKey = ids.join(',')
  useEffect(() => {
    if (ids.length < 2) {
      setPre({ loading: false })
      return
    }
    setPre((p) => ({ ...p, loading: true, error: undefined }))
    const t = window.setTimeout(() => {
      compareApi
        .preflight(ids, mode)
        .then((data) => setPre({ loading: false, data }))
        .catch((err) => setPre({ loading: false, error: errorMessage(err) }))
    }, 250)
    return () => window.clearTimeout(t)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [idsKey, mode])

  // 当前比较：未完成时轮询比较记录与任务进度
  const pollRef = useRef<number | null>(null)
  useEffect(() => {
    setCurrent(null)
    setJob(null)
    setLoadError(null)
    if (!activeId) return
    let alive = true
    const load = async (): Promise<void> => {
      try {
        const c = await compareApi.get(activeId)
        if (!alive) return
        setCurrent(c)
        if (c.job_id && (c.status === 'pending' || c.status === 'running')) {
          api.jobs.get(c.job_id).then((j) => alive && setJob(j)).catch(() => undefined)
          pollRef.current = window.setTimeout(() => void load(), 1500)
        } else {
          setJob(null)
          void reloadHistory()
        }
      } catch (err) {
        if (alive) setLoadError(errorMessage(err))
      }
    }
    void load()
    return () => {
      alive = false
      if (pollRef.current) window.clearTimeout(pollRef.current)
    }
  }, [activeId, reloadHistory])

  const select = (id: string | null): void => {
    const next = new URLSearchParams(params)
    if (id) next.set('id', id)
    else next.delete('id')
    setParams(next, { replace: true })
  }

  const start = async (): Promise<void> => {
    setStarting(true)
    try {
      const r = await compareApi.create(ids, mode)
      useJobsStore.getState().track(r.job_id)
      select(r.id)
      void reloadHistory()
    } catch (err) {
      toast.error('无法开始比较', errorMessage(err))
    } finally {
      setStarting(false)
    }
  }

  const rerun = async (): Promise<void> => {
    if (!current) return
    try {
      const r = await compareApi.rerun(current.id)
      useJobsStore.getState().track(r.job_id)
      select(r.id)
      void reloadHistory()
    } catch (err) {
      toast.error('无法重新比较', errorMessage(err))
    }
  }

  const doDelete = async (): Promise<void> => {
    if (!toDelete) return
    try {
      await compareApi.remove(toDelete.id)
      if (activeId === toDelete.id) select(null)
      setToDelete(null)
      void reloadHistory()
    } catch (err) {
      toast.error('删除失败', errorMessage(err))
    }
  }

  const visible = useMemo(() => {
    const q = filter.trim().toLowerCase()
    return papers.filter((p) => p.role !== 'supplement' && (!q || p.title.toLowerCase().includes(q) || p.authors.join(' ').toLowerCase().includes(q)))
  }, [papers, filter])
  const canStart = ids.length >= 2 && !!pre.data?.ok && !pre.loading
  const modeHint = MODES.find((m) => m.value === mode)?.hint

  return (
    <div className="pd-page">
      <div className="pd-page-inner">
        <h1 className="pd-page-title">多论文比较</h1>
        <p className="pd-page-subtitle">选 2–8 篇论文做比较。每个具体结论都附带可点击的原文证据，没有全文的论文不会被当作全文来比较。</p>

        <div className="pd-cmp-layout">
          <aside className="pd-cmp-side">
            <h2 className="pd-cmp-step">
              <span className="pd-cmp-stepno">1</span>选择论文<span className="pd-cmp-stepaux">{ids.length}/8</span>
            </h2>
            <Input value={filter} onChange={setFilter} clearable placeholder="筛选文献库…" aria-label="筛选文献库" />
            <div className="pd-cmp-picker" role="group" aria-label="文献库论文">
              {papersLoading && <p className="pd-dc-hint">加载中…</p>}
              {!papersLoading && visible.length === 0 && <p className="pd-dc-hint">{papers.length === 0 ? '文献库还没有论文。先导入几篇，或在“发现论文”里把结果加入文献库。' : '没有匹配的论文。'}</p>}
              {visible.map((p) => (
                <label key={p.id} className="pd-cmp-pick">
                  <input type="checkbox" checked={ids.includes(p.id)} onChange={() => toggle(p.id)} />
                  <span className="pd-cmp-pick-title">{p.title}</span>
                  <span className="pd-dc-sub" style={{ margin: 0 }}>{[p.year, p.status === 'ready' ? null : p.status === 'metadata_only' ? '仅元数据' : p.status === 'parsing' ? '解析中' : '解析失败'].filter(Boolean).join(' · ')}</span>
                </label>
              ))}
            </div>
            {ids.length > 0 && (
              <button type="button" className="pd-dc-more" onClick={clear}>
                清空选择
              </button>
            )}

            <h2 className="pd-cmp-step pd-cmp-step--sep">
              <span className="pd-cmp-stepno">2</span>比较方式
            </h2>
            <Segmented variant="text" aria-label="比较方式" value={mode} onChange={setMode} options={MODES.map((m) => ({ value: m.value, label: m.label }))} />
            <p className="pd-dc-hint">{modeHint}</p>

            <h2 className="pd-cmp-step pd-cmp-step--sep">
              <span className="pd-cmp-stepno">3</span>检查并开始
            </h2>
            {ids.length < 2 && <p className="pd-dc-hint">先选满 2 篇以上，这里会检查每篇论文的内容基础。</p>}
            {ids.length >= 2 && (
              <div className="pd-cmp-pre">
                {pre.loading && <p className="pd-dc-hint">正在检查每篇论文的内容基础…</p>}
                {pre.error && <p className="pd-status-text pd-status-text--danger">{pre.error}</p>}
                {pre.data && !pre.loading && (
                  <>
                    <ul className="pd-cmp-pre-list">
                      {pre.data.papers.map((p) => (
                        <li key={p.paper_id}>
                          <span className="pd-cmp-letter">{p.label}</span>
                          <span className="pd-cmp-pre-title">{p.title}</span>
                          <Tag>{BASIS_TAG[p.basis] ?? p.basis}</Tag>
                        </li>
                      ))}
                    </ul>
                    {pre.data.blockers.map((b) => (
                      <p key={b} className="pd-status-text pd-status-text--danger">{b}</p>
                    ))}
                    {pre.data.warnings.map((w) => (
                      <p key={w} className="pd-status-text pd-status-text--warning">{w}</p>
                    ))}
                  </>
                )}
              </div>
            )}

            <div style={{ marginTop: 'var(--sp-lg)' }}>
              <Button
                variant="primary"
                loading={starting}
                disabled={!canStart}
                disabledReason={ids.length < 2 ? '至少选择 2 篇论文' : pre.loading ? '正在检查' : (pre.data?.blockers[0] ?? '无法比较')}
                onClick={() => void start()}
              >
                开始比较
              </Button>
            </div>

            <h2 className="pd-cmp-step pd-cmp-step--history">
              历史比较<span className="pd-cmp-stepaux">{history.length}</span>
            </h2>
            {history.length === 0 && <p className="pd-dc-hint">还没有比较记录。</p>}
            <ul className="pd-cmp-history">
              {history.map((h) => (
                <li key={h.id} className={h.id === activeId ? 'is-active' : undefined}>
                  <button type="button" onClick={() => select(h.id)}>
                    <span className="pd-cmp-history-title">{h.title}</span>
                    <span className="pd-dc-sub" style={{ margin: 0 }}>
                      <span>{h.mode_label}</span>
                      <span>{STATUS_LABEL[h.status] ?? h.status}</span>
                      <span>{fmtTime(h.updated_at)}</span>
                    </span>
                  </button>
                  <button type="button" className="pd-cmp-del" aria-label="删除这条比较" onClick={() => setToDelete(h)}>
                    <Trash2 size={14} />
                  </button>
                </li>
              ))}
            </ul>
          </aside>

          <main className="pd-cmp-main">
            {!activeId && <EmptyState title="选择论文，开始比较" description="在左侧勾选 2–8 篇论文并选择比较方式；也可以在“发现论文”结果里点“比较”把论文放进比较篮。" />}
            {activeId && loadError && <p className="pd-status-text pd-status-text--danger">{loadError}</p>}
            {activeId && current && (
              <>
                <div className="pd-cmp-head">
                  <div>
                    <h2 className="pd-cmp-title">{current.title}</h2>
                    <p className="pd-dc-sub" style={{ marginTop: 4 }}>
                      <span>{current.mode_label}</span>
                      <span>{STATUS_LABEL[current.status] ?? current.status}</span>
                      <span>{fmtTime(current.updated_at)}</span>
                    </p>
                  </div>
                  <Button size="sm" variant="secondary" disabled={current.status === 'running' || current.status === 'pending'} onClick={() => void rerun()}>
                    重新比较
                  </Button>
                </div>
                {(current.status === 'pending' || current.status === 'running') && (
                  <div className="pd-cmp-progress">
                    <ProgressBar value={job && job.progress > 0 ? job.progress : undefined} />
                    <p className="pd-dc-hint">{job?.message || '比较任务正在后台运行，可以离开本页，结果会保存在历史里。'}</p>
                  </div>
                )}
                {current.status === 'failed' && <p className="pd-status-text pd-status-text--danger">{current.error ?? '比较失败'}</p>}
                {current.status === 'cancelled' && <p className="pd-dc-hint">这次比较已取消。</p>}
                {current.status === 'ready' && current.result && <CompareResultView result={current.result} />}
              </>
            )}
          </main>
        </div>
      </div>

      <Dialog
        open={!!toDelete}
        onClose={() => setToDelete(null)}
        title="删除这条比较？"
        footer={
          <>
            <Button variant="ghost" onClick={() => setToDelete(null)}>
              取消
            </Button>
            <Button variant="danger" solid onClick={() => void doDelete()}>
              删除
            </Button>
          </>
        }
      >
        <p>“{toDelete?.title}”的比较结果会被删除，不会影响文献库中的论文。</p>
      </Dialog>
    </div>
  )
}
