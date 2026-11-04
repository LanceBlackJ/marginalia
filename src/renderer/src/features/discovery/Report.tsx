import type { SearchPlan, SearchResponse, SourceReport } from '@/lib/types.discovery'
import { MODE_OPTIONS } from './workUtils'

/** 各数据源的实际返回情况：成功/失败如实列出，不隐藏失败的来源。 */
export function SourcesLine({ sources }: { sources: SourceReport[] }) {
  if (sources.length === 0) return null
  return (
    <div>
      <strong>数据来源</strong>
      {'　'}
      {sources.map((s, i) => (
        <span key={s.name} className={s.ok ? undefined : 'is-warn'}>
          {i > 0 && '　·　'}
          {s.ok ? `${s.name} ${s.count} 条 · ${(s.ms / 1000).toFixed(1)}s` : `${s.name} 未返回：${s.error ?? '未知原因'}`}
        </span>
      ))}
    </div>
  )
}

export function PlanLine({ plan }: { plan: SearchPlan }) {
  if (!plan.ai_used) {
    return plan.notes.length ? (
      <div>
        <strong>未使用 AI 理解</strong>
        {'　'}
        {plan.notes.join('；')}
      </div>
    ) : null
  }
  return (
    <>
      <div>
        <strong>AI 对问题的理解</strong>
        {'　'}
        {plan.english_query}
        {plan.alt_queries.length > 0 && ` · 备选检索式：${plan.alt_queries.join('；')}`}
      </div>
      {plan.analogies.length > 0 && (
        <div>
          <strong>类比概念</strong>
          {'　'}
          {plan.analogies.join('；')}
        </div>
      )}
    </>
  )
}

export function SearchReport({ data }: { data: SearchResponse }) {
  const mode = MODE_OPTIONS.find((m) => m.value === data.mode)?.label ?? data.mode
  return (
    <div className="pd-dc-report" aria-live="polite">
      <div>
        <strong>检索结果</strong>
        {'　'}共 {data.total_candidates} 条候选，去重与版本合并后 {data.after_dedupe} 条，按“{mode}”排序，全部 {data.results.length} 条分页展示。
      </div>
      {data.seed && (
        <div>
          <strong>基准论文</strong>
          {'　'}
          {data.seed.title}
          {data.seed.year ? ` · ${data.seed.year}` : ''}
        </div>
      )}
      <PlanLine plan={data.plan} />
      <SourcesLine sources={data.sources} />
      <div>
        <strong>AI 参与</strong>
        {'　'}
        语义向量 {data.ai.embeddings ? '已使用' : '未使用'}　·　AI 重排与推荐理由 {data.ai.rerank ? '已使用' : '未使用'}
      </div>
      {data.warnings.filter((w) => !data.sources.some((s) => !s.ok && w === `${s.name}：${s.error}`)).map((w) => (
        <div key={w} className="is-warn">
          {w}
        </div>
      ))}
    </div>
  )
}
