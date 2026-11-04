import { useCallback, useEffect, useRef, useState } from 'react'
import { Lightbulb, RefreshCw } from 'lucide-react'
import { Button, EmptyState, ProgressBar, Skeleton, Tag, toast } from '@/components/ui'
import { api, errorMessage } from '@/lib/api'
import { findingsApi } from '@/lib/api.mindmap'
import { formatRelativeTime } from '@/lib/format'
import type { Finding, FindingList, FindingUserState } from '@/lib/types.mindmap'
import { useJobsStore } from '@/stores/jobsStore'
import { useReaderStore } from '@/features/reader/readerStore'
import { EvidenceChips, useMindJump } from './evidence'
import './mindmap.css'

const active = (s?: string): boolean => s === 'pending' || s === 'running'

/**
 * 阅读器右侧的“提示”标签：AI 在读完论文后主动指出值得核查的地方（动机偏弱、证据偏弱、参数未解释、结论外推、延伸阅读）。
 * 每条都附有原文依据；状态只有“待核查 / AI 推断”，只有用户自己点了“已核查”才会变成“已核查”；AI 从不自称已经核实。
 */
export function FindingsPanel() {
  const paperId = useReaderStore((s) => s.paperId)
  const jump = useMindJump(paperId)
  const [data, setData] = useState<FindingList | null>(null)
  const [showIgnored, setShowIgnored] = useState(false)
  const [progress, setProgress] = useState<{ progress: number; message: string } | null>(null)
  const [starting, setStarting] = useState(false)
  const [tick, setTick] = useState(0) // 提交扫描后 +1，触发重新轮询
  const timer = useRef<number | undefined>(undefined)

  const load = useCallback(
    async (id: string, withIgnored: boolean): Promise<FindingList | null> => {
      try {
        const d = await findingsApi.list(id, withIgnored)
        setData(d)
        return d
      } catch (err) {
        toast.error('无法读取研究提示', errorMessage(err))
        return null
      }
    },
    []
  )

  useEffect(() => {
    setData(null)
    setProgress(null)
    if (!paperId) return
    let cancelled = false
    const poll = async (): Promise<void> => {
      const d = await load(paperId, showIgnored)
      if (cancelled || !d) return
      if (active(d.status)) {
        if (d.job_id) {
          try {
            const j = await api.jobs.get(d.job_id)
            if (!cancelled) setProgress({ progress: j.progress, message: j.message })
          } catch {
            /* 下一轮再取 */
          }
        }
        timer.current = window.setTimeout(() => void poll(), 1500)
      } else {
        setProgress(null)
      }
    }
    void poll()
    return () => {
      cancelled = true
      window.clearTimeout(timer.current)
    }
  }, [paperId, showIgnored, tick, load])

  const scan = async (): Promise<void> => {
    if (!paperId) return
    setStarting(true)
    try {
      const r = await findingsApi.scan(paperId)
      useJobsStore.getState().track(r.job_id)
      setTick((t) => t + 1)
    } catch (err) {
      toast.error('无法开始扫描', errorMessage(err))
    } finally {
      setStarting(false)
    }
  }

  const setState = async (f: Finding, user_state: FindingUserState): Promise<void> => {
    if (!paperId) return
    try {
      await findingsApi.patch(paperId, f.id, { user_state })
      await load(paperId, showIgnored)
    } catch (err) {
      toast.error('操作没有成功', errorMessage(err))
    }
  }
  const markChecked = async (f: Finding): Promise<void> => {
    if (!paperId) return
    try {
      await findingsApi.patch(paperId, f.id, { verified: f.check_status !== 'verified' })
      await load(paperId, showIgnored)
    } catch (err) {
      toast.error('操作没有成功', errorMessage(err))
    }
  }

  if (!data) {
    return (
      <div className="pd-rpanel-scroll">
        <Skeleton height={60} />
      </div>
    )
  }

  if (active(data.status)) {
    return (
      <div className="pd-rpanel-scroll">
        <EmptyState
          icon={<Lightbulb size={20} />}
          title="正在通读全文并寻找值得核查的地方…"
          description={progress?.message || '通常需要 1~3 分钟，可以先去做别的事。'}
          action={
            <div style={{ width: 220 }}>
              <ProgressBar value={data.status === 'running' && progress ? progress.progress : undefined} />
            </div>
          }
        />
      </div>
    )
  }

  if (data.status === 'none' || data.status === 'failed') {
    return (
      <div className="pd-rpanel-scroll">
        <EmptyState
          icon={<Lightbulb size={20} />}
          title={data.status === 'failed' ? '扫描没有成功' : '还没有扫描过这篇论文'}
          description={
            data.status === 'failed' ? (
              (data.error ?? '生成过程中出现错误，可以重试。')
            ) : (
              <>
                AI 会通读全文，指出动机偏弱、证据偏弱、参数没解释、结论可能外推之处，并给出延伸阅读线索。每一条都附有原文依据，并标明是“待核查”还是“AI 推断”。
                <div style={{ marginTop: 8 }}>
                  <Tag tone="outline">会调用云端模型</Tag>
                  <div className="pd-aux pd-weak" style={{ marginTop: 6 }}>
                    论证充分时它会如实说“没发现明显问题”，不会为了凑数硬挑毛病。
                  </div>
                </div>
              </>
            )
          }
          action={
            <Button variant="primary" loading={starting} onClick={() => void scan()}>
              {data.status === 'failed' ? '重新扫描' : '扫描这篇论文'}
            </Button>
          }
        />
      </div>
    )
  }

  const items = data.items
  return (
    <div className="pd-rpanel-scroll">
      <div className="pd-row" style={{ marginBottom: 6 }}>
        <span className="pd-aux pd-weak" style={{ flex: 1 }}>
          {data.model ? `${data.model} · ` : ''}
          {data.scanned_at ? `${formatRelativeTime(data.scanned_at)}扫描` : ''}
        </span>
        <Button size="sm" variant="ghost" icon={<RefreshCw size={13} />} loading={starting} onClick={() => void scan()}>
          重新扫描
        </Button>
      </div>
      {data.summary && <p className="pd-fnd-summary">{data.summary}</p>}
      {items.length === 0 && (
        <p className="pd-aux pd-weak" style={{ margin: '8px 0' }}>
          {data.verdict === 'no_issues' ? '这次没有发现明显需要核查的地方。' : '当前没有需要处理的提示。'}
        </p>
      )}
      {items.map((f) => (
        <article key={f.id} className="pd-fnd" data-state={f.user_state}>
          <div className="pd-fnd-tags">
            <Tag tone="outline">{f.type_label}</Tag>
            <Tag tone={f.check_status === 'verified' ? 'accent' : 'neutral'}>{f.check_label}</Tag>
            {f.confidence === 'medium' && <span className="pd-aux pd-weak">把握一般</span>}
          </div>
          <h4 className="pd-fnd-title">{f.title}</h4>
          <p className="pd-fnd-detail">{f.detail}</p>
          {f.note && <p className="pd-aux pd-weak">备注：{f.note}</p>}
          <div className="pd-fnd-ev">
            <span className="pd-aux pd-weak">依据</span>
            <EvidenceChips refs={f.evidence} jump={jump} />
          </div>
          <div className="pd-fnd-actions">
            <Button size="sm" variant="ghost" onClick={() => void setState(f, f.user_state === 'useful' ? 'read' : 'useful')}>
              {f.user_state === 'useful' ? '取消“有用”' : '有用'}
            </Button>
            <Button size="sm" variant="ghost" onClick={() => void markChecked(f)}>
              {f.check_status === 'verified' ? '取消“已核查”' : '我已核查'}
            </Button>
            <Button size="sm" variant="ghost" onClick={() => void setState(f, f.user_state === 'ignored' ? 'read' : 'ignored')}>
              {f.user_state === 'ignored' ? '恢复' : '忽略'}
            </Button>
          </div>
        </article>
      ))}
      {data.ignored_count > 0 && (
        <Button size="sm" variant="ghost" onClick={() => setShowIgnored((v) => !v)}>
          {showIgnored ? '隐藏已忽略的提示' : `显示已忽略的提示 ${data.ignored_count}`}
        </Button>
      )}
      <p className="pd-aux pd-weak" style={{ marginTop: 10 }}>
        以上都是 AI 的线索，不是结论；“已核查”只有你自己确认后才会标记。
      </p>
    </div>
  )
}
