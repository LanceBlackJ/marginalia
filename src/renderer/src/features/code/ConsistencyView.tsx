import { useEffect, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { Button, EmptyState, Spinner } from '@/components/ui'
import type { ConsistencyItem } from '@/lib/types.code'
import { ORIGIN_LABEL, RELATION_LABEL, shortSha } from './codeUtils'
import { openPaperAt, useCodeStore } from './codeStore'
import { ViewFrame, ViewHead } from './ViewFrame'

function Row({ item }: { item: ConsistencyItem }) {
  const navigate = useNavigate()
  const paperId = useCodeStore((s) => s.paperId)
  const goToLines = useCodeStore((s) => s.goToLines)
  const [showQuote, setShowQuote] = useState(false)
  return (
    <tr className="pd-cons-row" data-rel={item.relation}>
      <td className="pd-cons-param">{item.parameter}</td>
      <td>
        <div className="pd-cons-val">{item.paper.value}</div>
        <div className="pd-aux pd-muted">
          {paperId ? (
            <button type="button" className="pd-link" onClick={() => void openPaperAt(navigate, paperId, { page: item.paper.page, ord: item.paper.ord })}>
              论文第 {item.paper.page} 页
            </button>
          ) : (
            `论文第 ${item.paper.page} 页`
          )}
          {' · '}
          {item.paper.method === 'ai' ? (item.paper.check === 'weak' ? 'AI 抽取 · 未通过核对' : 'AI 抽取 · 原句与上下文已核对') : '规则抽取'}
          {item.paper.quote && (
            <>
              {' · '}
              <button type="button" className="pd-link" onClick={() => setShowQuote(!showQuote)}>
                {showQuote ? '收起原句' : '看原句'}
              </button>
            </>
          )}
        </div>
        {showQuote && <p className="pd-aux pd-cons-quote">{item.paper.context && item.paper.context !== item.paper.quote ? item.paper.context : item.paper.quote}</p>}
      </td>
      <td>
        {item.code.length === 0 ? (
          <span className="pd-muted">没有找到</span>
        ) : (
          <ul className="pd-cons-code">
            {item.code.map((c, i) => (
              <li key={`${c.path}-${c.line}-${i}`}>
                <span className="pd-cons-val">
                  {c.name} = {c.value}
                </span>
                <span className="pd-aux pd-muted">
                  {ORIGIN_LABEL[c.origin] ?? c.origin}
                  {c.context ? ` · ${c.context}` : ''}
                  {' · '}
                  <button type="button" className="pd-link" onClick={() => void goToLines(c.path, c.line, c.line)}>
                    {c.path}:{c.line}
                  </button>
                  {c.also_in && c.also_in.length > 0 && ` · 另有 ${c.also_in.length} 处相同`}
                </span>
              </li>
            ))}
          </ul>
        )}
      </td>
      <td className="pd-cons-rel">
        <span className="pd-cons-badge" data-rel={item.relation}>
          {RELATION_LABEL[item.relation]}
        </span>
        {item.note && <div className="pd-aux pd-muted">{item.note}</div>}
        {item.caveats.length > 0 && (
          <ul className="pd-cons-caveats pd-aux">
            {item.caveats.map((c) => (
              <li key={c}>{c}</li>
            ))}
          </ul>
        )}
      </td>
    </tr>
  )
}

export function ConsistencyView() {
  const state = useCodeStore((s) => s.consistency)
  const checking = useCodeStore((s) => s.checking)
  const loadConsistency = useCodeStore((s) => s.loadConsistency)
  const runConsistency = useCodeStore((s) => s.runConsistency)
  const repo = useCodeStore((s) => s.repos.find((r) => r.id === s.repoId) ?? null)

  useEffect(() => {
    if (!state) void loadConsistency()
  }, [state, loadConsistency])

  const report = state?.report ?? null
  const order = { differs: 0, to_check: 1, match: 2, not_found: 3 } as const
  const items = report ? [...report.items].sort((a, b) => order[a.relation] - order[b.relation]) : []

  return (
    <ViewFrame>
      <div className="pd-cons-inner">
        <ViewHead
          title="论文 ↔ 代码 参数一致性"
          desc={
            report ? (
              <>
                一致 {report.summary.match} · 不一致 {report.summary.differs} · 代码中未找到 {report.summary.not_found}
                {(report.summary.to_check ?? 0) > 0 && ` · 待核查 ${report.summary.to_check}`}
                {' · '}
                {report.ai_used ? '含 AI 抽取，原句已逐条核实' : '仅规则抽取，未使用 AI，可能漏掉部分参数'}
              </>
            ) : (
              '从论文方法部分抽取参数，到代码里找对应的取值并逐项对照。'
            )
          }
          actions={
            <Button variant="primary" loading={checking} onClick={() => void runConsistency()}>
              {report ? '重新检查' : '开始检查'}
            </Button>
          }
        />
        <p className="pd-aux pd-muted pd-map-note">
          只列出论文和代码各自写了什么，不替你判断谁对谁错：训练设置有时在代码里被命令行参数或配置文件覆盖，论文也可能描述的是另一组实验。
          {repo && ` 当前代码版本：${shortSha(repo.commit_sha)}${repo.commit_date ? ` · ${repo.commit_date.slice(0, 10)}` : ''}，${repo.authored_by === 'author' ? '论文作者提供' : '非论文作者提供，可能与论文不一致'}。`}
        </p>

        {!state ? (
          <div className="pd-map-loading">
            <Spinner size={18} />
          </div>
        ) : state.status === 'failed' ? (
          <EmptyState tone="danger" title="上次检查失败" description={state.error ?? undefined} action={<Button onClick={() => void runConsistency()}>重试</Button>} />
        ) : !report ? (
          <EmptyState title="还没有检查结果" description="点击“开始检查”，会从论文里抽取方法部分写明的参数，到代码里找对应的取值并逐项对照。" />
        ) : items.length === 0 ? (
          <EmptyState title="论文里没有抽取到可以对照的参数" description={report.note || undefined} />
        ) : (
          <table className="pd-cons-table">
            <thead>
              <tr>
                <th>参数</th>
                <th>论文写的</th>
                <th>代码里的</th>
                <th>结果</th>
              </tr>
            </thead>
            <tbody>
              {items.map((it) => (
                <Row key={`${it.parameter}-${it.paper.ord}-${it.paper.value}`} item={it} />
              ))}
            </tbody>
          </table>
        )}
        {report?.note && items.length > 0 && <p className="pd-aux pd-muted pd-map-note">{report.note}</p>}
        {report && <p className="pd-aux pd-muted">检查时间：{report.generated_at.slice(0, 16).replace('T', ' ')}</p>}
      </div>
    </ViewFrame>
  )
}
