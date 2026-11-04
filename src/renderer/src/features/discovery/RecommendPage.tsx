import { useCallback, useState } from 'react'
import { create } from 'zustand'
import { Search } from 'lucide-react'
import { Button, Input, Select, Switch } from '@/components/ui'
import { errorMessage } from '@/lib/api'
import { discoveryApi, type RecommendOpts } from '@/lib/api.discovery'
import type { DirectionResponse, PersonalResponse, Work } from '@/lib/types.discovery'
import { PlanLine, SourcesLine } from './Report'
import { WorkDrawer } from './WorkDrawer'
import { KindFilter, filterByKind, useKindFilter } from './KindFilter'
import { WorkItem } from './WorkItem'
import { useSaved } from './discoveryStore'
import { DcEmpty, DcLoading } from './shared'
import { useLibraryMatchRefresh } from './useWorkActions'
import './discovery.css'

type Kind = 'direction' | 'personal'

const THIS_YEAR = new Date().getFullYear()
const YEARS = Array.from({ length: THIS_YEAR - 1990 + 1 }, (_, i) => String(THIS_YEAR - i))
const LEVEL_OPTIONS = [
  { value: 'any', label: '来源水平：不限' },
  { value: 'peer', label: '已发表（同行评审）' },
  { value: 'core', label: '核心来源及以上' },
  { value: 'top', label: '仅顶刊顶会' }
]

interface RecState {
  topic: string
  ai: boolean
  yearFrom: string
  yearTo: string
  level: NonNullable<RecommendOpts['level']>
  moreLoading: Kind | null
  direction: DirectionResponse | null
  personal: PersonalResponse | null
  loading: Kind | null
  error: { kind: Kind; message: string } | null
  set: (p: Partial<Pick<RecState, 'topic' | 'ai' | 'yearFrom' | 'yearTo' | 'level'>>) => void
  runDirection: () => Promise<void>
  runPersonal: () => Promise<void>
  /** 现在这些都不喜欢：排除已经显示过的，再要一批 */
  moreDirection: () => Promise<void>
  morePersonal: () => Promise<void>
  patchWork: (key: string, patch: Partial<Work>) => void
}

function optsOf(s: Pick<RecState, 'yearFrom' | 'yearTo' | 'level'>, exclude: string[] = []): RecommendOpts {
  return { year_from: s.yearFrom ? Number(s.yearFrom) : null, year_to: s.yearTo ? Number(s.yearTo) : null, level: s.level, exclude }
}

const useRec = create<RecState>((set, get) => ({
  topic: '',
  ai: true,
  yearFrom: '',
  yearTo: '',
  level: 'any',
  moreLoading: null,
  direction: null,
  personal: null,
  loading: null,
  error: null,
  set: (p) => set(p),
  runDirection: async () => {
    const { topic, ai } = get()
    if (!topic.trim()) {
      set({ error: { kind: 'direction', message: '请输入你关心的研究方向或主题。' } })
      return
    }
    set({ loading: 'direction', error: null })
    try {
      set({ direction: await discoveryApi.direction(topic.trim(), ai, optsOf(get())), loading: null })
    } catch (err) {
      set({ loading: null, error: { kind: 'direction', message: errorMessage(err) } })
    }
  },
  runPersonal: async () => {
    set({ loading: 'personal', error: null })
    try {
      set({ personal: await discoveryApi.personal(get().ai, optsOf(get())), loading: null })
    } catch (err) {
      set({ loading: null, error: { kind: 'personal', message: errorMessage(err) } })
    }
  },
  moreDirection: async () => {
    const { direction, ai } = get()
    if (!direction || get().moreLoading) return
    const shown = direction.groups.flatMap((g) => g.items.map((w) => w.key))
    set({ moreLoading: 'direction', error: null })
    try {
      const r = await discoveryApi.direction(direction.topic, ai, optsOf(get(), shown))
      set((s) => ({
        moreLoading: null,
        direction: s.direction
          ? { ...s.direction, groups: s.direction.groups.map((g) => ({ ...g, items: [...g.items, ...(r.groups.find((x) => x.id === g.id)?.items ?? [])] })) }
          : s.direction,
      }))
      if (r.groups.every((g) => g.items.length === 0)) set({ error: { kind: 'direction', message: '没有更多符合条件的论文了。可以放宽年份或来源水平，或换一个主题。' } })
    } catch (err) {
      set({ moreLoading: null, error: { kind: 'direction', message: errorMessage(err) } })
    }
  },
  morePersonal: async () => {
    const { personal, ai } = get()
    if (!personal || get().moreLoading) return
    set({ moreLoading: 'personal', error: null })
    try {
      const r = await discoveryApi.personal(ai, optsOf(get(), personal.results.map((w) => w.key)))
      set((s) => ({ moreLoading: null, personal: s.personal ? { ...s.personal, results: [...s.personal.results, ...r.results] } : s.personal }))
      if (r.results.length === 0) set({ error: { kind: 'personal', message: '没有更多符合条件的论文了。可以放宽年份或来源水平。' } })
    } catch (err) {
      set({ moreLoading: null, error: { kind: 'personal', message: errorMessage(err) } })
    }
  },
  patchWork: (key, patch) =>
    set((s) => ({
      direction: s.direction ? { ...s.direction, groups: s.direction.groups.map((g) => ({ ...g, items: g.items.map((w) => (w.key === key ? { ...w, ...patch } : w)) })) } : null,
      personal: s.personal ? { ...s.personal, results: s.personal.results.map((w) => (w.key === key ? { ...w, ...patch } : w)) } : null
    }))
}))

function MoreBar({ loading, onMore }: { loading: boolean; onMore: () => void }) {
  return (
    <div style={{ margin: 'var(--sp-lg) 0' }}>
      <Button variant="secondary" loading={loading} onClick={onMore}>
        这些都不喜欢，再推荐一些
      </Button>
      <span className="pd-dc-hint" style={{ marginLeft: 12 }}>
        会排除已经显示过的，按当前的年份和来源水平再取一批。
      </span>
    </div>
  )
}

/**
 * “为我推荐”一区：不再有子标签。上面是两种触发方式（输入研究方向、或直接根据文献库），
 * 都需要点按钮才请求；结果在下面依次展开，互不覆盖。
 */
export function RecommendPage() {
  const s = useRec()
  const [selected, setSelected] = useState<Work | null>(null)
  const saved = useSaved()

  const all: Work[] = [...(s.direction?.groups.flatMap((g) => g.items) ?? []), ...(s.personal?.results ?? [])]
  const kindState = useKindFilter(all)
  const onMatches = useCallback((matches: Record<string, string>) => {
    for (const [key, paperId] of Object.entries(matches)) useRec.getState().patchWork(key, { in_library: paperId })
  }, [])
  useLibraryMatchRefresh(all, onMatches)

  const shown = selected ? (all.concat(saved.items).find((w) => w.key === selected.key) ?? selected) : null
  const errDirection = s.error?.kind === 'direction' ? s.error.message : null
  const errPersonal = s.error?.kind === 'personal' ? s.error.message : null

  return (
    <>
      <div className="pd-dc-search">
        <Input
          value={s.topic}
          onChange={(v) => s.set({ topic: v })}
          onEnter={() => void s.runDirection()}
          icon={<Search size={16} />}
          clearable
          placeholder="输入一个研究方向，例如：复杂网络上的级联失效与恢复"
          aria-label="研究方向"
        />
        <Button variant="primary" data-dc-primary="recommend" loading={s.loading === 'direction'} onClick={() => void s.runDirection()}>
          获取推荐
        </Button>
      </div>
      <div className="pd-dc-options">
        <Switch checked={s.ai} onChange={(v) => s.set({ ai: v })} label="AI 理解主题并生成检索式" />
        <span className="pd-dc-or">
          或者不输入方向：
          <Button size="sm" variant="secondary" loading={s.loading === 'personal'} onClick={() => void s.runPersonal()}>
            {s.personal ? '重新根据我的文献库推荐' : '根据我的文献库推荐'}
          </Button>
        </span>
      </div>
      <div className="pd-dc-options">
        <Select
          value={s.yearFrom}
          onChange={(v) => s.set({ yearFrom: v, yearTo: v && s.yearTo && Number(s.yearTo) < Number(v) ? v : s.yearTo })}
          options={[{ value: '', label: '起始年份：不限' }, ...YEARS.map((y) => ({ value: y, label: `${y} 年起` }))]}
          aria-label="起始年份"
        />
        <Select
          value={s.yearTo}
          onChange={(v) => s.set({ yearTo: v, yearFrom: v && s.yearFrom && Number(s.yearFrom) > Number(v) ? v : s.yearFrom })}
          options={[{ value: '', label: '截止年份：不限' }, ...YEARS.map((y) => ({ value: y, label: `到 ${y} 年` }))]}
          aria-label="截止年份"
        />
        <Select value={s.level} onChange={(v) => s.set({ level: v as RecState['level'] })} options={LEVEL_OPTIONS} aria-label="来源水平" />
      </div>
      <p className="pd-dc-hint">
        筛选在下一次点推荐时生效。来源水平只按可核对的事实判断：名单里的顶刊顶会、数据库标记的核心来源、已同行评审发表；预印本在选了任何水平时都不显示。
      </p>
      <p className="pd-dc-hint">根据文献库推荐的依据：文献库中的论文标题与主题、知识库主题与你确认过的知识条目，不会上传论文全文。</p>

      {errDirection && <p className="pd-status-text pd-status-text--danger pd-dc-error">{errDirection}</p>}
      {errPersonal && <p className="pd-status-text pd-status-text--danger pd-dc-error">{errPersonal}</p>}

      {s.loading === 'direction' && <DcLoading text="正在排学习路线、检索综述与近期论文，并逐篇核对……通常需要 20–60 秒" />}
      {s.loading !== 'direction' && s.direction && (
        <div className="pd-dc-block">
          <h3 className="pd-dc-block-title">研究方向推荐：{s.direction.topic}</h3>
          <div className="pd-dc-report">
            <PlanLine plan={s.direction.plan} />
            <SourcesLine sources={s.direction.sources} />
            {s.direction.warnings.map((w) => (
              <div key={w} className="is-warn">
                {w}
              </div>
            ))}
          </div>
          <KindFilter works={s.direction.groups.flatMap((g) => g.items)} value={kindState.kind} onChange={kindState.setKind} />
          {s.direction.groups.map((g) => (
            <section key={g.id} className="pd-dc-group">
              <div className="pd-dc-group-head">
                <h4>{g.title}</h4>
                <span>{g.hint}</span>
              </div>
              {filterByKind(g.items, kindState.kind).length === 0 && <p className="pd-dc-hint">{kindState.kind ? '这一组里没有这个类型的论文。' : '这一组暂时没有符合质量条件的论文。'}</p>}
              <div className="pd-dc-results">
                {filterByKind(g.items, kindState.kind).map((w) => (
                  <WorkItem key={`${g.id}-${w.key}`} work={w} onOpen={setSelected} />
                ))}
              </div>
            </section>
          ))}
          <MoreBar loading={s.moreLoading === 'direction'} onMore={() => void s.moreDirection()} />
        </div>
      )}

      {s.loading === 'personal' && <DcLoading text="正在分析你的文献库并检索……" />}
      {s.loading !== 'personal' && s.personal && (
        <div className="pd-dc-block">
          <h3 className="pd-dc-block-title">根据我的文献库推荐</h3>
          <div className="pd-dc-report">
            <div>
              <strong>你的兴趣主题</strong>
              {'　'}
              {s.personal.interests.length ? s.personal.interests.join('；') : '未能归纳出兴趣主题'}
              {!s.personal.ai_used && ' · 未使用 AI，直接取自论文标题与知识库主题'}
            </div>
            {s.personal.queries.map((q) => (
              <div key={q.query}>
                <strong>检索式</strong>
                {'　'}
                {q.query}
                {q.why && `——${q.why}`}
              </div>
            ))}
            <SourcesLine sources={s.personal.sources} />
            {s.personal.warnings.map((w) => (
              <div key={w} className="is-warn">
                {w}
              </div>
            ))}
          </div>
          {s.personal.results.length === 0 && <p className="pd-dc-hint">没有找到合适的新论文。</p>}
          <KindFilter works={s.personal.results} value={kindState.kind} onChange={kindState.setKind} />
          <div className="pd-dc-results">
            {filterByKind(s.personal.results, kindState.kind).map((w) => (
              <WorkItem key={w.key} work={w} onOpen={setSelected} />
            ))}
          </div>
          <MoreBar loading={s.moreLoading === 'personal'} onMore={() => void s.morePersonal()} />
        </div>
      )}

      {!s.direction && !s.personal && !s.loading && !s.error && (
        <p className="pd-dc-hint pd-dc-idle">还没有开始。输入一个方向后点“获取推荐”，会得到先读的综述、领域经典、近期进展三组论文；或直接根据文献库推荐。</p>
      )}

      <WorkDrawer work={shown} onClose={() => setSelected(null)} onOpenOther={setSelected} />
    </>
  )
}

/** “我的收藏”一区：在任何结果里点星标收藏的论文，之后再决定是否加入文献库。 */
export function SavedSection() {
  const saved = useSaved()
  const [selected, setSelected] = useState<Work | null>(null)
  const shown = selected ? (saved.items.find((w) => w.key === selected.key) ?? selected) : null
  return (
    <>
      {saved.items.length === 0 ? (
        <DcEmpty title="还没有收藏" description="在上面任何一个结果里点星标即可收藏，便于之后再决定是否加入文献库。" />
      ) : (
        <div className="pd-dc-results">
          {saved.items.map((w) => (
            <WorkItem key={w.key} work={w} onOpen={setSelected} />
          ))}
        </div>
      )}
      <WorkDrawer work={shown} onClose={() => setSelected(null)} onOpenOther={setSelected} />
    </>
  )
}
