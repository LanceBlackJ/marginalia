import { useEffect } from 'react'
import { Link, Navigate, useSearchParams } from 'react-router-dom'
import { BasketBar } from './BasketBar'
import { DiscoverPage } from './DiscoverPage'
import { RandomPage } from './RandomPage'
import { RecommendPage, SavedSection } from './RecommendPage'
import { useSaved } from './discoveryStore'
import './discovery.css'

/**
 * “发现”分成三个标签：搜索、为我推荐、随机探索，一次只显示一个面板。
 * 当前标签记在地址的 `tab` 参数里，旧链接（/recommend、/random、/discover?tab=…）直接落到对应标签。
 * 切换标签和打开页面都不会自动发起任何请求（随机探索尤其不会）。我的收藏放在搜索标签的下方。
 */
export type DiscoverTab = 'search' | 'recommend' | 'random'

export interface DiscoverSection {
  value: DiscoverTab
  anchor: string
  title: string
  hint: string
}

export const DISCOVER_SECTIONS: DiscoverSection[] = [
  {
    value: 'search',
    anchor: 'dc-search',
    title: '搜索',
    hint: '同时检索 OpenAlex、Crossref、Semantic Scholar、arXiv、Europe PMC，去重合并并标注发表状态；可用自然语言描述问题。'
  },
  {
    value: 'recommend',
    anchor: 'dc-recommend',
    title: '为我推荐',
    hint: '按研究方向给出“综述 / 经典 / 近期”三组入门路径，或根据你的文献库与知识库推荐新论文。每条推荐都附依据。'
  },
  {
    value: 'random',
    anchor: 'dc-random',
    title: '随机探索',
    hint: '随机看一篇发表在顶级期刊或顶级会议上的论文，用来拓宽视野。只有你点击按钮后才会推荐，不会自动弹出。'
  }
]

export function parseTab(value: string | null): DiscoverTab {
  return value === 'recommend' || value === 'random' || value === 'search' ? value : 'search'
}

/** 旧路径（/recommend、/random）重定向到 /discover?tab=…，书签和内部链接都不会 404。 */
export function LegacyRedirect({ tab }: { tab: DiscoverTab }) {
  return <Navigate to={`/discover?tab=${tab}`} replace />
}

export function DiscoverHub() {
  const [params, setParams] = useSearchParams()
  const loadSaved = useSaved((x) => x.load)
  const tab = parseTab(params.get('tab'))
  const section = DISCOVER_SECTIONS.find((s) => s.value === tab) ?? DISCOVER_SECTIONS[0]

  useEffect(() => {
    // 收藏列表只在进入“发现”时加载一次
    void loadSaved()
  }, [loadSaved])

  const select = (next: DiscoverTab): void => {
    if (next === tab) return
    setParams({ tab: next }, { replace: true })
  }

  const onKey = (e: React.KeyboardEvent, i: number): void => {
    if (e.key !== 'ArrowRight' && e.key !== 'ArrowLeft') return
    e.preventDefault()
    const n = (i + (e.key === 'ArrowRight' ? 1 : DISCOVER_SECTIONS.length - 1)) % DISCOVER_SECTIONS.length
    select(DISCOVER_SECTIONS[n].value)
    document.getElementById(`${DISCOVER_SECTIONS[n].anchor}-tab`)?.focus()
  }

  return (
    <div className="pd-page">
      <div className="pd-page-inner pd-dc-page">
        <div className="pd-dc-head">
          <h1 className="pd-page-title">发现</h1>
          <Link to="/venues" className="pd-dc-venueslink">
            期刊与会议
          </Link>
          <div className="pd-dc-tabs" role="tablist" aria-label="发现方式">
            {DISCOVER_SECTIONS.map((s, i) => (
              <button
                key={s.value}
                id={`${s.anchor}-tab`}
                type="button"
                role="tab"
                aria-selected={s.value === tab}
                aria-controls={`${s.anchor}-panel`}
                tabIndex={s.value === tab ? 0 : -1}
                className="pd-dc-tab"
                onClick={() => select(s.value)}
                onKeyDown={(e) => onKey(e, i)}
              >
                {s.title}
              </button>
            ))}
          </div>
        </div>

        <section id={`${section.anchor}-panel`} role="tabpanel" aria-labelledby={`${section.anchor}-tab`} className="pd-dc-panel">
          <p className="pd-dc-sec-hint">{section.hint}</p>
          {tab === 'search' && (
            <>
              <DiscoverPage />
              <div className="pd-dc-saved">
                <h2 className="pd-dc-sec-title">我的收藏</h2>
                <p className="pd-dc-sec-hint">在任何结果里点星标收藏的论文，之后再决定是否加入文献库。</p>
                <SavedSection />
              </div>
            </>
          )}
          {tab === 'recommend' && <RecommendPage />}
          {tab === 'random' && <RandomPage />}
        </section>

        <BasketBar />
      </div>
    </div>
  )
}
