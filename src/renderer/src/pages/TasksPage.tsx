import { useEffect, useState } from 'react'
import { Check, ChevronLeft, ChevronRight, RotateCcw, Trash2, X, XCircle } from 'lucide-react'
import { Button, EmptyState, IconButton, ProgressBar, Skeleton, Tag, toast } from '@/components/ui'
import { api, errorMessage } from '@/lib/api'
import { jobsApprovalApi } from '@/lib/api.materials'
import { attachAsSupplement, canAttachAsSupplement } from '@/features/materials/attach'
import { formatDateTime, formatRelativeTime, isActiveJob, jobStatusLabel, jobTypeLabel } from '@/lib/format'
import type { ImportResult, Job, JobDetail } from '@/lib/types'
import { useJobsStore } from '@/stores/jobsStore'
import '@/features/tasks/tasks.css'

const PAGE_SIZE = 10

const FILTERS = [
  { value: 'all', label: '全部状态' },
  { value: 'active', label: '进行中' },
  { value: 'completed', label: '已完成' },
  { value: 'failed', label: '失败' },
  { value: 'cancelled', label: '已取消' }
]

function matches(job: Job, filter: string): boolean {
  if (filter === 'all') return true
  if (filter === 'active') return isActiveJob(job.status) || job.status === 'paused'
  return job.status === filter
}

/** 状态：进行中=黑色小圆点；失败=暗红文字；其余为灰色文字。 */
function StateText({ status }: { status: string }) {
  return (
    <span className={`pd-task-state${status === 'failed' ? ' pd-task-state--failed' : ''}`}>
      {(status === 'running' || status === 'pending') && <span className="pd-dot" />}
      {jobStatusLabel(status)}
    </span>
  )
}

/** 对齐的行式列表：任务名与说明 | 类型 | 进度或状态 | 时间。 */
function TaskRows({ jobs, selectedId, onSelect, onDelete }: { jobs: Job[]; selectedId: string | null; onSelect: (id: string) => void; onDelete: (job: Job) => void }) {
  return (
    <div className="pd-task-list" role="table">
      {jobs.map((j) => (
        <div
          key={j.id}
          className="pd-task-row"
          role="row"
          tabIndex={0}
          data-selected={j.id === selectedId}
          data-failed={j.status === 'failed' ? 'true' : undefined}
          onClick={() => onSelect(j.id)}
          onKeyDown={(e) => {
            if (e.key === 'Enter' || e.key === ' ') {
              e.preventDefault()
              onSelect(j.id)
            }
          }}
        >
          <span style={{ minWidth: 0 }}>
            <div className="pd-ellipsis pd-task-title" title={j.title}>
              {j.title}
            </div>
            {(j.status === 'failed' ? j.error || j.message : j.message) && (
              <div className="pd-aux pd-muted pd-ellipsis" title={j.status === 'failed' ? j.error || j.message : j.message}>
                {j.status === 'failed' ? j.error || j.message : j.message}
              </div>
            )}
          </span>
          <span className="pd-task-type">{jobTypeLabel(j.type)}</span>
          <span className="pd-task-progress">
            {isActiveJob(j.status) ? (
              <ProgressBar value={j.status === 'running' ? j.progress : undefined} />
            ) : (
              <StateText status={j.status} />
            )}
          </span>
          <span className="pd-task-time" title={formatDateTime(j.created_at)}>
            {formatRelativeTime(j.created_at)}
          </span>
          <span className="pd-task-del">
            {!isActiveJob(j.status) && j.status !== 'paused' && (
              <IconButton
                icon={<Trash2 size={14} />}
                label="删除记录"
                size="sm"
                onClick={(e) => {
                  e.stopPropagation()
                  onDelete(j)
                }}
              />
            )}
          </span>
        </div>
      ))}
    </div>
  )
}

export function TasksPage() {
  const jobs = useJobsStore((s) => s.jobs)
  const loaded = useJobsStore((s) => s.loaded)
  const refresh = useJobsStore((s) => s.refresh)
  const [filter, setFilter] = useState('all')
  const [page, setPage] = useState(1)
  const [selectedId, setSelectedId] = useState<string | null>(null)
  const [confirmClear, setConfirmClear] = useState(false)

  // 进行中的排在最前，其余保持新的在前；每页 10 条
  const filtered = jobs.filter((j) => matches(j, filter))
  const visible = [...filtered.filter((j) => isActiveJob(j.status)), ...filtered.filter((j) => !isActiveJob(j.status))]
  const pages = Math.max(1, Math.ceil(visible.length / PAGE_SIZE))
  const cur = Math.min(page, pages)
  const shown = visible.slice((cur - 1) * PAGE_SIZE, cur * PAGE_SIZE)
  const selected = jobs.find((j) => j.id === selectedId) ?? null
  const finishedCount = jobs.filter((j) => !isActiveJob(j.status) && j.status !== 'paused').length

  useEffect(() => {
    void refresh().catch(() => undefined)
  }, [refresh])

  const removeOne = async (job: Job): Promise<void> => {
    try {
      await api.jobs.remove(job.id)
      if (selectedId === job.id) setSelectedId(null)
      await refresh()
    } catch (err) {
      toast.error('删除失败', errorMessage(err))
    }
  }
  const clearFinished = async (): Promise<void> => {
    setConfirmClear(false)
    try {
      const { deleted } = await api.jobs.clearFinished()
      setSelectedId(null)
      await refresh()
      toast.info(`已删除 ${deleted} 条记录`)
    } catch (err) {
      toast.error('清空失败', errorMessage(err))
    }
  }

  return (
    <div className="pd-tasks">
      <div className="pd-tasks-main">
        <div className="pd-tasks-inner">
          <div className="pd-tasks-head">
            <h1 className="pd-page-title">任务中心</h1>
            <p className="pd-page-subtitle">导入、解析、分析等后台任务。应用关闭后任务记录不会丢失。</p>
          </div>

          <div className="pd-tasks-tabs" role="tablist" aria-label="状态筛选">
            {FILTERS.map((f) => (
              <button
                key={f.value}
                type="button"
                role="tab"
                aria-selected={filter === f.value}
                data-active={filter === f.value}
                onClick={() => {
                  setFilter(f.value)
                  setPage(1)
                }}
              >
                {f.label}
                <span className="pd-tasks-count">{jobs.filter((j) => matches(j, f.value)).length}</span>
              </button>
            ))}
          </div>

          {finishedCount > 0 && (
            <div className="pd-tasks-tools">
              {confirmClear ? (
                <>
                  <span className="pd-aux pd-muted">删除全部 {finishedCount} 条已结束的记录？</span>
                  <Button size="sm" variant="primary" onClick={() => void clearFinished()}>
                    确定
                  </Button>
                  <Button size="sm" variant="ghost" onClick={() => setConfirmClear(false)}>
                    取消
                  </Button>
                </>
              ) : (
                <Button size="sm" variant="ghost" icon={<Trash2 size={13} />} onClick={() => setConfirmClear(true)}>
                  清空已结束的记录
                </Button>
              )}
            </div>
          )}

          {!loaded ? (
            <div style={{ display: 'flex', flexDirection: 'column', gap: 14 }}>
              {Array.from({ length: 4 }, (_, i) => (
                <Skeleton key={i} height={18} />
              ))}
            </div>
          ) : visible.length === 0 ? (
            <EmptyState title={jobs.length === 0 ? '还没有任务' : '没有符合条件的任务'} description={jobs.length === 0 ? '导入论文或生成论文分析后，任务会显示在这里。' : undefined} />
          ) : (
            <>
              <TaskRows jobs={shown} selectedId={selectedId} onSelect={setSelectedId} onDelete={(j) => void removeOne(j)} />
              {pages > 1 && (
                <nav className="pd-tasks-pager" aria-label="分页">
                  <IconButton icon={<ChevronLeft size={15} />} label="上一页" size="sm" disabled={cur <= 1} onClick={() => setPage(cur - 1)} />
                  <span className="pd-aux">
                    第 {cur} / {pages} 页，共 {visible.length} 条
                  </span>
                  <IconButton icon={<ChevronRight size={15} />} label="下一页" size="sm" disabled={cur >= pages} onClick={() => setPage(cur + 1)} />
                </nav>
              )}
            </>
          )}
        </div>
      </div>

      {selected && (
        <JobDetailPanel job={selected} onClose={() => setSelectedId(null)} onChanged={() => void refresh()} onSelect={setSelectedId} onDelete={(j) => void removeOne(j)} />
      )}
    </div>
  )
}

function JobDetailPanel({
  job,
  onClose,
  onChanged,
  onSelect,
  onDelete
}: {
  job: Job
  onClose: () => void
  onChanged: () => void
  onSelect: (id: string) => void
  onDelete: (job: Job) => void
}) {
  const [detail, setDetail] = useState<JobDetail | null>(null)
  const [busy, setBusy] = useState<'cancel' | 'retry' | 'approve' | 'reject' | null>(null)
  const [attached, setAttached] = useState<Set<number>>(new Set())
  const active = isActiveJob(job.status)

  useEffect(() => {
    let alive = true
    let timer: number | undefined
    const load = async (): Promise<void> => {
      try {
        const d = await api.jobs.get(job.id)
        if (alive) setDetail(d)
      } catch {
        /* 忽略，下一轮重试 */
      }
      if (alive && active) timer = window.setTimeout(() => void load(), 1000)
    }
    void load()
    return () => {
      alive = false
      window.clearTimeout(timer)
    }
  }, [job.id, active, job.status])

  const cancel = async (): Promise<void> => {
    setBusy('cancel')
    try {
      await api.jobs.cancel(job.id)
      onChanged()
    } catch (err) {
      toast.error('取消失败', errorMessage(err))
    } finally {
      setBusy(null)
    }
  }
  const retry = async (): Promise<void> => {
    setBusy('retry')
    try {
      const { job_id } = await api.jobs.retry(job.id)
      useJobsStore.getState().track(job_id)
      onSelect(job_id)
      toast.info('已重新创建任务')
    } catch (err) {
      toast.error('重试失败', errorMessage(err))
    } finally {
      setBusy(null)
    }
  }

  const decide = async (kind: 'approve' | 'reject'): Promise<void> => {
    setBusy(kind)
    try {
      if (kind === 'approve') await jobsApprovalApi.approve(job.id)
      else await jobsApprovalApi.reject(job.id)
      onChanged()
    } catch (err) {
      toast.error(kind === 'approve' ? '批准失败' : '操作失败', errorMessage(err))
    } finally {
      setBusy(null)
    }
  }

  const importItems = (job.result as ImportResult | null)?.items

  return (
    <aside className="pd-tasks-detail" aria-label="任务详情">
      <div className="pd-row" style={{ alignItems: 'flex-start' }}>
        <div style={{ minWidth: 0, flex: 1 }}>
          <div className="pd-aux pd-muted">{jobTypeLabel(job.type)}</div>
          <h2 style={{ marginTop: 2, fontSize: 'var(--fs-section)', fontWeight: 600, wordBreak: 'break-word' }}>{job.title}</h2>
        </div>
        <Button size="sm" variant="ghost" onClick={onClose}>
          关闭
        </Button>
      </div>

      <div className="pd-row" style={{ marginTop: 12 }}>
        <StateText status={job.status} />
        <span className="pd-aux pd-muted">{job.message}</span>
      </div>
      {active && (
        <div style={{ marginTop: 8 }}>
          <ProgressBar value={job.status === 'running' ? job.progress : undefined} />
        </div>
      )}

      {job.error && <div className="pd-tasks-error">{job.error}</div>}

      <dl className="pd-tasks-meta">
        <dt>创建</dt>
        <dd>{formatDateTime(job.created_at)}</dd>
        <dt>开始</dt>
        <dd>{formatDateTime(job.started_at) || '—'}</dd>
        <dt>结束</dt>
        <dd>{formatDateTime(job.finished_at) || '—'}</dd>
      </dl>

      <div className="pd-row" style={{ marginTop: 16 }}>
        {job.can_approve && (
          <>
            <Button size="sm" variant="primary" icon={<Check size={14} />} loading={busy === 'approve'} onClick={() => void decide('approve')}>
              批准
            </Button>
            <Button size="sm" icon={<X size={14} />} loading={busy === 'reject'} onClick={() => void decide('reject')}>
              拒绝
            </Button>
          </>
        )}
        {job.can_cancel && (
          <Button size="sm" icon={<XCircle size={14} />} loading={busy === 'cancel'} onClick={() => void cancel()}>
            取消任务
          </Button>
        )}
        {job.can_retry && (
          <Button size="sm" variant="primary" icon={<RotateCcw size={14} />} loading={busy === 'retry'} onClick={() => void retry()}>
            重试
          </Button>
        )}
        {!active && job.status !== 'paused' && (
          <Button size="sm" icon={<Trash2 size={14} />} onClick={() => onDelete(job)}>
            删除记录
          </Button>
        )}
      </div>

      {importItems && importItems.length > 0 && (
        <div style={{ marginTop: 24 }}>
          <div className="pd-field-label">结果</div>
          <ul className="pd-tasks-items">
            {importItems.map((it, i) => (
              <li key={i}>
                <Tag tone={it.status === 'failed' ? 'danger' : it.status === 'imported' ? 'neutral' : 'warning'}>
                  {{ imported: '已导入', duplicate: '重复', metadata_only: '无全文', failed: '失败' }[it.status]}
                </Tag>
                <span className="pd-ellipsis" title={it.input}>
                  {it.input}
                </span>
                {it.message && <span className="pd-aux pd-muted">{it.message}</span>}
                {canAttachAsSupplement(it) &&
                  (attached.has(i) ? (
                    <span className="pd-aux pd-muted">已作为补充材料添加</span>
                  ) : (
                    <Button
                      size="sm"
                      onClick={() =>
                        void attachAsSupplement(it).then((ok) => {
                          if (ok) setAttached((prev) => new Set(prev).add(i))
                        })
                      }
                    >
                      作为补充材料添加
                    </Button>
                  ))}
              </li>
            ))}
          </ul>
        </div>
      )}

      <div style={{ marginTop: 24 }}>
        <div className="pd-field-label">日志</div>
        <pre className="pd-tasks-log">{detail ? (detail.log.length > 0 ? detail.log.join('\n') : '暂无日志') : '加载中…'}</pre>
      </div>
    </aside>
  )
}
