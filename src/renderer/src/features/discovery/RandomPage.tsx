import { useCallback, useEffect, useState } from 'react'
import { create } from 'zustand'
import { Dices } from 'lucide-react'
import { Button, Select, Switch } from '@/components/ui'
import { errorMessage } from '@/lib/api'
import { discoveryApi, profileApi } from '@/lib/api.discovery'
import type { FieldInfo, RandomResponse, VenueList, Work } from '@/lib/types.discovery'
import { WorkDrawer } from './WorkDrawer'
import { WorkItem } from './WorkItem'
import { VenueIntro } from './VenueIntro'
import { DcLoading } from './shared'
import { useLibraryMatchRefresh } from './useWorkActions'
import './discovery.css'

interface RandomState {
  fieldId: string // '' = 全部学科
  topic: string // 研究画像里的一个方向；非空时按这个方向探索（不再限定学科）
  ai: boolean
  /** 年份范围，'' 表示默认（最近十年） */
  yearFrom: string
  yearTo: string
  history: Work[]
  current: Work | null
  filter: RandomResponse['filter'] | null
  loading: boolean
  error: string | null
  similar: { loading: boolean; items: Work[]; error?: string } | null
  fields: FieldInfo[]
  interests: string[]
  staticLoaded: boolean
  set: (p: Partial<Pick<RandomState, 'fieldId' | 'topic' | 'ai' | 'yearFrom' | 'yearTo'>>) => void
  loadStatic: () => Promise<void>
  next: () => Promise<void>
  showSimilar: () => Promise<void>
  patchWork: (key: string, patch: Partial<Work>) => void
  select: (w: Work) => void
}

/**
 * 随机探索只在用户点击按钮后才请求：打开页面、切换页面再回来、窗口重新聚焦都不会触发推荐。
 * 状态放在 store 里，回到这个页面时看到的是上次点出来的内容，而不是重新请求。
 */
export const useRandom = create<RandomState>((set, get) => ({
  fieldId: '',
  topic: '',
  ai: true,
  yearFrom: '',
  yearTo: '',
  history: [],
  current: null,
  filter: null,
  loading: false,
  error: null,
  similar: null,
  fields: [],
  interests: [],
  staticLoaded: false,
  set: (p) => set(p),
  /** 只加载静态数据（学科列表、研究画像里的方向），不请求任何推荐；切换方式回来不再重复加载。 */
  loadStatic: async () => {
    if (get().staticLoaded) return
    set({ staticLoaded: true })
    await Promise.all([
      discoveryApi
        .fields()
        .then((r) => set({ fields: r.items }))
        .catch(() => undefined),
      // 研究画像里填过研究方向时，才会出现“我的方向”选项；没有就不显示
      profileApi
        .get()
        .then((p) => set({ interests: (p.meta.interests ?? []).map((x) => x.trim()).filter(Boolean).slice(0, 12) }))
        .catch(() => undefined)
    ])
  },
  next: async () => {
    const s = get()
    if (s.loading) return
    set({ loading: true, error: null, similar: null })
    try {
      const r = await discoveryApi.random({
        field_id: s.fieldId ? Number(s.fieldId) : null,
        topic: s.topic || null,
        exclude: s.history.map((w) => w.key),
        count: 1,
        ai: s.ai,
        year_from: s.yearFrom ? Number(s.yearFrom) : null,
        year_to: s.yearTo ? Number(s.yearTo) : null
      })
      const w = r.items[0]
      set((x) => ({
        loading: false,
        current: w ?? x.current,
        history: w ? [w, ...x.history].slice(0, 50) : x.history,
        filter: r.filter
      }))
    } catch (err) {
      set({ loading: false, error: errorMessage(err) })
    }
  },
  showSimilar: async () => {
    const w = get().current
    if (!w) return
    set({ similar: { loading: true, items: [] } })
    try {
      const r = await discoveryApi.similar({ doi: w.doi, title: w.title })
      set({ similar: { loading: false, items: r.results.slice(0, 6) } })
    } catch (err) {
      set({ similar: { loading: false, items: [], error: errorMessage(err) } })
    }
  },
  patchWork: (key, patch) =>
    set((s) => ({
      current: s.current && s.current.key === key ? { ...s.current, ...patch } : s.current,
      history: s.history.map((w) => (w.key === key ? { ...w, ...patch } : w))
    })),
  select: (w) => set({ current: w, similar: null })
}))

const THIS_YEAR = new Date().getFullYear()
const YEARS = Array.from({ length: THIS_YEAR - 1990 + 1 }, (_, i) => String(THIS_YEAR - i))

const KIND_ZH = { journal: '期刊', conference: '会议' } as const

function VenueNames({ list }: { list: VenueList }) {
  const groups = new Map<string, string[]>()
  for (const v of list.items) groups.set(v.category, [...(groups.get(v.category) ?? []), v.short === v.name ? v.name : `${v.short}`])
  return (
    <div className="pd-dc-venuelist">
      <p className="pd-dc-hint">{list.policy}</p>
      {[...groups.entries()].map(([cat, names]) => (
        <p key={cat}>
          <strong>{cat}</strong>：{names.join('、')}
        </p>
      ))}
    </div>
  )
}

export function RandomPage() {
  const s = useRandom()
  const { fields, interests } = s
  const [drawer, setDrawer] = useState<Work | null>(null)
  const [venues, setVenues] = useState<VenueList | null>(null)
  const [showVenues, setShowVenues] = useState(false)

  useEffect(() => {
    // 这里只加载静态数据；不请求任何推荐
    void useRandom.getState().loadStatic()
  }, [])

  const toggleVenues = (): void => {
    setShowVenues((v) => !v)
    if (!venues) void discoveryApi.venues().then(setVenues).catch(() => undefined)
  }

  const onMatches = useCallback((matches: Record<string, string>) => {
    for (const [key, paperId] of Object.entries(matches)) useRandom.getState().patchWork(key, { in_library: paperId })
  }, [])
  useLibraryMatchRefresh(s.history, onMatches)

  const w = s.current
  const f = s.filter
  const started = s.history.length > 0 || s.loading || s.error !== null

  return (
    <>
      <div>
        <div className="pd-dc-random-bar">
          <Button variant="primary" data-dc-primary="random" icon={<Dices size={15} />} loading={s.loading} onClick={() => void s.next()}>
            {s.history.length > 0 ? '换一篇' : '随机探索一篇'}
          </Button>
          <Select
            value={s.topic ? `topic:${s.topic}` : s.fieldId}
            onChange={(v) => (v.startsWith('topic:') ? s.set({ topic: v.slice(6), fieldId: '' }) : s.set({ fieldId: v, topic: '' }))}
            options={[
              { value: '', label: '全部学科' },
              ...fields.map((x) => ({ value: String(x.id), label: x.name_zh })),
              ...interests.map((t) => ({ value: `topic:${t}`, label: `我的方向：${t}` }))
            ]}
            aria-label="学科或研究方向"
          />
          <Select
            value={s.yearFrom}
            onChange={(v) => s.set({ yearFrom: v, yearTo: v && s.yearTo && Number(s.yearTo) < Number(v) ? v : s.yearTo })}
            options={[{ value: '', label: '起始年份：默认' }, ...YEARS.map((y) => ({ value: y, label: `${y} 年起` }))]}
            aria-label="起始年份"
          />
          <Select
            value={s.yearTo}
            onChange={(v) => s.set({ yearTo: v, yearFrom: v && s.yearFrom && Number(s.yearFrom) > Number(v) ? v : s.yearFrom })}
            options={[{ value: '', label: '截止年份：默认' }, ...YEARS.map((y) => ({ value: y, label: `到 ${y} 年` }))]}
            aria-label="截止年份"
          />
          <Switch checked={s.ai} onChange={(v) => s.set({ ai: v })} label="AI 根据摘要生成导读" />
        </div>

        <p className="pd-dc-hint" style={{ marginTop: 'var(--sp-md)' }}>
          只显示来源可确认的论文：来源名称必须与收录的顶刊顶会精确对应；预印本、workshop、来源缺失的一律不显示。
          <button type="button" className="pd-dc-more" onClick={toggleVenues}>
            {showVenues ? '收起收录名单' : `查看收录名单${venues ? ` ${venues.count} 个` : ''}`}
          </button>
        </p>
        {showVenues && venues && <VenueNames list={venues} />}

        {!started && (
          <p className="pd-dc-hint" style={{ marginTop: 'var(--sp-2xl)' }}>
            还没有开始。点击“随机探索一篇”，会从收录的顶刊顶会里随机抽一篇你没看过、也不在文献库里的论文。
          </p>
        )}

        {s.error && <p className="pd-status-text pd-status-text--danger" style={{ marginTop: 'var(--sp-lg)' }}>{s.error}</p>}

        {f && (
          <p className="pd-dc-fieldline">
            范围：<strong>{f.topic ? `研究方向「${f.topic}」` : (f.field_zh ?? '全部学科')}</strong>，收录 {f.venue_pool} 个顶刊顶会，本次随机取了其中 {f.venues.length} 个
            ：{f.venues.map((v) => v.short).join('、')}。
            {f.pool_total !== null && f.pool_passed !== null
              ? `这些来源 ${f.thresholds.years} 年共有 ${f.pool_total.toLocaleString()} 篇论文，其中同时满足筛选条件的 ${f.pool_passed.toLocaleString()} 篇；`
              : ''}
            从中随机抽取 {f.sampled} 篇，丢弃来源无法确认的 {f.dropped_unconfirmed} 篇，再去掉已看过、已在文献库和缺摘要的，剩 {f.after_exclusions} 篇，
            按质量加权随机挑一篇。 筛选条件：{f.thresholds.type}；{f.thresholds.years}；
            {f.thresholds.fwci_min !== null ? `领域加权引用影响 ≥ ${f.thresholds.fwci_min}` : '本次未设引用影响阈值，合格样本太少，已放宽'}。
          </p>
        )}

        {s.loading && !w && <DcLoading rows={3} />}

        {w && (
          <div className="pd-dc-single" style={{ opacity: s.loading ? 0.5 : 1 }}>
            {w.top_venue && (
              <p className="pd-dc-venueline">
                <strong>{w.top_venue.label}</strong> · {w.top_venue.name}
                {w.year ? ` · ${w.year}` : ''} · {w.top_venue.category} · {KIND_ZH[w.top_venue.kind]}
              </p>
            )}
            <WorkItem work={w} onOpen={setDrawer} />
            <VenueIntro name={w.venue} type={w.venue_type} />
            {w.summary && (
              <div className="pd-dc-summary">
                <span className="pd-dc-summary-label">{w.summary.source} · 未读全文，可能有遗漏</span>
                <span>研究问题：{w.summary.research_question}</span>
                <span>核心贡献：{w.summary.contribution}</span>
                <span>为什么值得读：{w.summary.why_read}</span>
              </div>
            )}
            <div className="pd-dc-actions">
              <Button size="sm" variant="secondary" loading={s.similar?.loading} onClick={() => void s.showSimilar()}>
                查看类似论文
              </Button>
            </div>
            {s.similar?.error && <p className="pd-status-text pd-status-text--danger" style={{ marginTop: 'var(--sp-sm)' }}>{s.similar.error}</p>}
            {s.similar && !s.similar.loading && !s.similar.error && (
              <div className="pd-dc-group">
                <div className="pd-dc-group-head">
                  <h4>类似论文</h4>
                  <span>来自参考文献、相关工作与引用它的研究</span>
                </div>
                {s.similar.items.length === 0 && <p className="pd-dc-hint">没有找到类似论文。</p>}
                <div className="pd-dc-results">
                  {s.similar.items.map((x) => (
                    <WorkItem key={x.key} work={x} onOpen={setDrawer} showAbstract={false} />
                  ))}
                </div>
              </div>
            )}
          </div>
        )}

        {s.history.length > 1 && (
          <section className="pd-dc-group">
            <div className="pd-dc-group-head">
              <h4>本次已看过</h4>
              <span>{s.history.length} 篇</span>
            </div>
            <ul className="pd-dc-list" style={{ marginTop: 'var(--sp-md)' }}>
              {s.history.filter((x) => x.key !== w?.key).map((x) => (
                <li key={x.key}>
                  <button
                    type="button"
                    className="pd-dc-more"
                    style={{ color: 'var(--text)', textAlign: 'left', fontSize: 'var(--fs-small)' }}
                    onClick={() => s.select(x)}
                  >
                    {x.title}
                  </button>
                  <span className="pd-dc-sub" style={{ display: 'inline', marginLeft: 8 }}>
                    {[x.top_venue?.short ?? x.venue, x.year].filter(Boolean).join(' · ')}
                  </span>
                </li>
              ))}
            </ul>
          </section>
        )}

      </div>
      <WorkDrawer work={drawer ? (s.history.find((x) => x.key === drawer.key) ?? drawer) : null} onClose={() => setDrawer(null)} onOpenOther={setDrawer} />
    </>
  )
}
