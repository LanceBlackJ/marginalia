import { useEffect, useState } from 'react'
import { AlertTriangle } from 'lucide-react'
import { Input, ProgressBar, Skeleton } from '@/components/ui'
import { api, errorMessage } from '@/lib/api'
import { formatNumber, taskTypeLabel } from '@/lib/format'
import type { UsageSummary } from '@/lib/types'

function currentMonth(): string {
  const d = new Date()
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}`
}

function money(v: number | null): string {
  return v === null ? '未配置价格' : v.toFixed(4)
}

export function UsageSection() {
  const [month, setMonth] = useState(currentMonth())
  const [data, setData] = useState<UsageSummary | null>(null)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    let alive = true
    setData(null)
    setError(null)
    api
      .usage(month)
      .then((d) => alive && setData(d))
      .catch((e) => alive && setError(errorMessage(e)))
    return () => {
      alive = false
    }
  }, [month])

  return (
    <div>
      <div className="pd-row" style={{ marginBottom: 'var(--sp-lg)' }}>
        <span className="pd-muted">统计月份</span>
        <div style={{ width: 160 }}>
          <Input type="month" value={month} onChange={(v) => v && setMonth(v)} aria-label="统计月份" />
        </div>
      </div>

      {error && <div className="pd-notice pd-notice--danger">用量加载失败：{error}</div>}
      {!data && !error && <Skeleton height={80} />}

      {data && (
        <>
          {data.over_budget && (
            <div className="pd-notice pd-notice--warning" style={{ marginBottom: 'var(--sp-lg)' }}>
              <AlertTriangle size={14} style={{ flex: 'none', marginTop: 2 }} />
              <span>本月估算成本已超过预算 {data.budget}。</span>
            </div>
          )}

          <dl className="pd-kv">
            <dt>调用次数</dt>
            <dd>{formatNumber(data.calls)}</dd>
            <dt>输入 Token</dt>
            <dd>{formatNumber(data.prompt_tokens)}</dd>
            <dt>输出 Token</dt>
            <dd>{formatNumber(data.completion_tokens)}</dd>
            <dt>估算成本</dt>
            <dd>{money(data.estimated_cost)}</dd>
            {data.budget !== null && data.estimated_cost !== null && (
              <>
                <dt>预算使用</dt>
                <dd style={{ maxWidth: 260 }}>
                  <ProgressBar value={data.budget > 0 ? data.estimated_cost / data.budget : 1} />
                </dd>
              </>
            )}
          </dl>

          <section className="pd-settings-group">
            <h3>按模型</h3>
            {data.by_model.length === 0 ? (
              <p className="pd-muted pd-aux">这个月还没有模型调用。</p>
            ) : (
              <table className="pd-table">
                <thead>
                  <tr>
                    <th>模型</th>
                    <th>调用</th>
                    <th>输入 Token</th>
                    <th>输出 Token</th>
                    <th>估算成本</th>
                  </tr>
                </thead>
                <tbody>
                  {data.by_model.map((m) => (
                    <tr key={m.model}>
                      <td>{m.model}</td>
                      <td>{formatNumber(m.calls)}</td>
                      <td>{formatNumber(m.prompt_tokens)}</td>
                      <td>{formatNumber(m.completion_tokens)}</td>
                      <td>{m.estimated_cost === null ? '—' : m.estimated_cost.toFixed(4)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            )}
          </section>

          <section className="pd-settings-group">
            <h3>按任务类型</h3>
            {data.by_task_type.length === 0 ? (
              <p className="pd-muted pd-aux">暂无数据。</p>
            ) : (
              <table className="pd-table">
                <thead>
                  <tr>
                    <th>任务类型</th>
                    <th>调用</th>
                    <th>输入 Token</th>
                    <th>输出 Token</th>
                  </tr>
                </thead>
                <tbody>
                  {data.by_task_type.map((t) => (
                    <tr key={t.task_type}>
                      <td>{taskTypeLabel(t.task_type)}</td>
                      <td>{formatNumber(t.calls)}</td>
                      <td>{formatNumber(t.prompt_tokens)}</td>
                      <td>{formatNumber(t.completion_tokens)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            )}
          </section>
        </>
      )}
    </div>
  )
}
