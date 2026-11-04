import { useCallback, useEffect, useState } from 'react'
import { ChevronRight, ShieldCheck } from 'lucide-react'
import { Button, EmptyState, Select, Skeleton, Tag } from '@/components/ui'
import { api, errorMessage } from '@/lib/api'
import { formatDateTime, formatNumber, taskTypeLabel } from '@/lib/format'
import type { PrivacyPage, PrivacyRequest } from '@/lib/types'
import { SettingsPanel } from './SettingRow'

const FIRST = 5
const MORE = 20

/** 发送记录里一行的一句话摘要。 */
export function requestLine(r: PrivacyRequest): string {
  return r.summary || `${r.passage_count} 段原文`
}

function Row({ r }: { r: PrivacyRequest }) {
  const [open, setOpen] = useState(false)
  const tags: string[] = []
  if (r.includes.question) tags.push('问题')
  if (r.includes.selection) tags.push('选中文字')
  if (r.includes.notes) tags.push('个人笔记')
  if (r.includes.supplements) tags.push('补充材料')
  if (r.includes.code) tags.push('代码')
  return (
    <li className="pd-log-item" data-open={open}>
      <button type="button" className="pd-log-row" aria-expanded={open} onClick={() => setOpen(!open)}>
        <ChevronRight size={14} className="pd-log-chev" aria-hidden="true" />
        <span className="pd-log-time">{formatDateTime(r.ts)}</span>
        <span className="pd-log-type">{taskTypeLabel(r.task_type)}</span>
        <span className="pd-log-sum pd-ellipsis">{requestLine(r)}</span>
        <span className="pd-log-chars">约 {formatNumber(r.chars_sent)} 字</span>
      </button>
      {open && (
        <dl className="pd-kv pd-log-detail">
          <dt>模型</dt>
          <dd>{r.model}</dd>
          <dt>论文</dt>
          <dd>{r.paper_title ?? '没有关联论文'}</dd>
          <dt>原文段落</dt>
          <dd>{r.passage_count} 段</dd>
          <dt>包含</dt>
          <dd>
            {tags.length === 0 ? (
              '只有任务本身需要的内容'
            ) : (
              <span className="pd-row" style={{ gap: 4, flexWrap: 'wrap' }}>
                {tags.map((t) => (
                  <Tag key={t} tone={t === '个人笔记' || t === '补充材料' || t === '代码' ? 'warning' : undefined}>
                    {t}
                  </Tag>
                ))}
              </span>
            )}
          </dd>
        </dl>
      )}
    </li>
  )
}

/**
 * 发送记录：默认只列最近 5 条，一行一条；点开一行才看详情；“显示更多”每次再取 20 条（游标分页）。
 * 面板头的摘要是总条数和最新一条的时间。不自动刷新。
 */
export function PrivacySection() {
  const [items, setItems] = useState<PrivacyRequest[]>([])
  const [page, setPage] = useState<PrivacyPage | null>(null)
  const [overall, setOverall] = useState<{ total: number; latest: string | null } | null>(null)
  const [type, setType] = useState('')
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)

  const first = useCallback(async (taskType: string): Promise<void> => {
    setError(null)
    setBusy(true)
    try {
      const p = await api.privacyRequests({ limit: FIRST, ...(taskType ? { task_type: taskType } : {}) })
      setItems(p.items)
      setPage(p)
      if (!taskType) setOverall({ total: p.total, latest: p.items[0]?.ts ?? null })
    } catch (e) {
      setError(errorMessage(e))
    } finally {
      setBusy(false)
    }
  }, [])

  useEffect(() => {
    void first('')
  }, [first])

  const more = async (): Promise<void> => {
    const last = items[items.length - 1]
    if (!last) return
    setBusy(true)
    try {
      const p = await api.privacyRequests({ limit: MORE, before_id: Number(last.id), ...(type ? { task_type: type } : {}) })
      setItems([...items, ...p.items])
      setPage({ ...p, task_types: page?.task_types ?? p.task_types })
    } catch (e) {
      setError(errorMessage(e))
    } finally {
      setBusy(false)
    }
  }

  const summary = error
    ? '加载失败'
    : !overall
      ? '正在读取…'
      : overall.total === 0
        ? '还没有发送过内容'
        : `共 ${formatNumber(overall.total)} 条 · 最新 ${formatDateTime(overall.latest)}`

  return (
    <SettingsPanel id="sendlog" title="发送记录" summary={summary}>
      {error && <div className="pd-notice pd-notice--danger">加载失败：{error}</div>}
      {!page && !error && <Skeleton height={80} />}
      {page && (overall?.total ?? 0) === 0 && (
        <EmptyState icon={<ShieldCheck size={22} />} title="还没有发送过任何内容" description="使用翻译、解释或提问后，每次发给云端模型的内容会记在这里。" />
      )}
      {page && (overall?.total ?? 0) > 0 && (
        <>
          <div className="pd-log-bar">
            <span className="pd-aux pd-muted">{type ? `筛选后共 ${formatNumber(page.total)} 条` : `最近 ${Math.min(items.length, page.total)} 条`}</span>
            <div style={{ width: 200 }}>
              <Select
                aria-label="按任务类型筛选"
                value={type}
                onChange={(v) => {
                  setType(v)
                  void first(v)
                }}
                options={[{ value: '', label: '全部类型' }, ...page.task_types.map((t) => ({ value: t.task_type, label: `${taskTypeLabel(t.task_type)} ${t.count}` }))]}
              />
            </div>
          </div>
          {items.length === 0 ? (
            <p className="pd-aux pd-muted">这个类型下没有记录。</p>
          ) : (
            <ul className="pd-log-list">
              {items.map((r) => (
                <Row key={r.id} r={r} />
              ))}
            </ul>
          )}
          <div className="pd-log-foot">
            {page.has_more ? (
              <Button loading={busy} onClick={() => void more()}>
                显示更多
              </Button>
            ) : (
              items.length > 0 && <span className="pd-aux pd-muted">没有更早的了</span>
            )}
          </div>
        </>
      )}
    </SettingsPanel>
  )
}
