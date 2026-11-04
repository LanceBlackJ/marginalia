import { useEffect, useMemo, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { Input, Skeleton, Tag } from '@/components/ui'
import { errorMessage } from '@/lib/api'
import { discoveryApi } from '@/lib/api.discovery'
import type { LibraryVenue, VenueInfo, VenueList, VenueSearchHit } from '@/lib/types.discovery'
import './discovery.css'

type KindFilter = 'all' | 'journal' | 'conference'
type LevelFilter = 'all' | 'top' | 'core'

/** 来源名录：收录的期刊和会议按分类列出，点一个进它自己的页面。 */
export function VenueDirectoryPage() {
  const navigate = useNavigate()
  const [list, setList] = useState<VenueList | null>(null)
  const [mine, setMine] = useState<{ items: LibraryVenue[]; papers: number } | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [q, setQ] = useState('')
  const [kind, setKind] = useState<KindFilter>('all')
  const [level, setLevel] = useState<LevelFilter>('all')
  const [hits, setHits] = useState<VenueSearchHit[] | null>(null)
  const [searching, setSearching] = useState(false)

  useEffect(() => {
    discoveryApi
      .libraryVenues()
      .then(setMine)
      .catch(() => undefined)
    discoveryApi
      .venues()
      .then(setList)
      .catch((e) => setError(errorMessage(e)))
  }, [])

  // 名录里没有的期刊/会议：停下打字 0.5 秒后到 OpenAlex 里按名称或缩写检索，点开就是它的页面
  useEffect(() => {
    const t = q.trim()
    if (t.length < 2) {
      setHits(null)
      return
    }
    setSearching(true)
    let alive = true
    const timer = setTimeout(() => {
      discoveryApi
        .venueSearch(t)
        .then((r) => alive && setHits(r.items))
        .catch(() => alive && setHits([]))
        .finally(() => alive && setSearching(false))
    }, 500)
    return () => {
      alive = false
      clearTimeout(timer)
    }
  }, [q])

  const groups = useMemo(() => {
    const items = (list?.items ?? []).filter((v) => {
      if (kind !== 'all' && v.kind !== kind) return false
      if (level !== 'all' && (v.level ?? 'top') !== level) return false
      const t = q.trim().toLowerCase()
      return !t || `${v.name} ${v.short} ${v.category}`.toLowerCase().includes(t)
    })
    const by = new Map<string, VenueInfo[]>()
    for (const v of items) by.set(v.category, [...(by.get(v.category) ?? []), v])
    return [...by.entries()]
  }, [list, q, kind, level])

  const chip = <T extends string>(value: T, cur: T, set: (v: T) => void, label: string) => (
    <button type="button" className="pd-dc-kind" data-active={cur === value} onClick={() => set(value)}>
      {label}
    </button>
  )

  return (
    <div className="pd-page">
      <div className="pd-page-inner pd-dc-page">
        <h1 className="pd-page-title">期刊与会议</h1>
        <div className="pd-vd-bar">
          <Input value={q} onChange={setQ} placeholder="搜索名称、缩写、分类" aria-label="搜索期刊或会议" />
          <div className="pd-dc-kinds" role="group" aria-label="类型">
            {chip<KindFilter>('all', kind, setKind, '全部')}
            {chip<KindFilter>('journal', kind, setKind, '期刊')}
            {chip<KindFilter>('conference', kind, setKind, '会议')}
            <span className="pd-dc-kinds-label" style={{ marginLeft: 12 }}>
              档次
            </span>
            {chip<LevelFilter>('all', level, setLevel, '全部')}
            {chip<LevelFilter>('top', level, setLevel, '顶刊顶会')}
            {chip<LevelFilter>('core', level, setLevel, '重要期刊会议')}
          </div>
        </div>
        {mine && mine.items.length > 0 && (
          <section className="pd-vd-group">
            <h2 className="pd-dc-h">
              我文献库里的 <span className="pd-muted">{mine.items.length} 个来源 · {mine.papers} 篇</span>
            </h2>
            <ul className="pd-vd-list">
              {mine.items.map((v) => (
                <li key={v.name}>
                  <button
                    type="button"
                    className="pd-vd-item"
                    onClick={() => navigate(v.id ? `/venue?id=${encodeURIComponent(v.id)}` : `/venue?name=${encodeURIComponent(v.name)}`)}
                  >
                    <span className="pd-vd-name">{v.name}</span>
                    <span className="pd-vd-intro">
                      {v.count} 篇{v.year_from ? ` · ${v.year_from === v.year_to || !v.year_to ? v.year_from : `${v.year_from}–${v.year_to}`}` : ''}
                      {v.label ? ` · ${v.label}` : ''}
                    </span>
                  </button>
                </li>
              ))}
            </ul>
          </section>
        )}
        {q.trim().length >= 2 && (
          <section className="pd-vd-group">
            <h2 className="pd-dc-h">
              在 OpenAlex 里找到的 <span className="pd-muted">{searching ? '检索中…' : `${hits?.length ?? 0} 个`}</span>
            </h2>
            <ul className="pd-vd-list">
              {(hits ?? []).map((h) => (
                <li key={h.id}>
                  <button
                    type="button"
                    className="pd-vd-item"
                    onClick={() => navigate(`/venue?oa=${encodeURIComponent(h.id.split('/').pop() ?? '')}&name=${encodeURIComponent(h.name)}`)}
                  >
                    <span className="pd-vd-name">
                      {h.name}
                      {h.abbreviation && <span className="pd-muted"> · {h.abbreviation}</span>}
                    </span>
                    <span className="pd-vd-intro">
                      {[h.type === 'conference' ? '会议' : '期刊', h.publisher, h.works_count ? `${h.works_count.toLocaleString()} 篇` : null, h.h_index ? `h 指数 ${h.h_index}` : null, h.label]
                        .filter(Boolean)
                        .join(' · ')}
                    </span>
                  </button>
                </li>
              ))}
            </ul>
          </section>
        )}
        {error && <p className="pd-status-text pd-status-text--danger">{error}</p>}
        {!list && !error && <Skeleton height={160} />}
        {list && groups.length === 0 && <p className="pd-dc-hint">没有符合的来源。</p>}
        {groups.map(([category, items]) => (
          <section key={category} className="pd-vd-group">
            <h2 className="pd-dc-h">
              {category} <span className="pd-muted">{items.length}</span>
            </h2>
            <ul className="pd-vd-list">
              {items.map((v) => (
                <li key={v.id}>
                  <button type="button" className="pd-vd-item" onClick={() => navigate(`/venue?id=${encodeURIComponent(v.id)}`)}>
                    <span className="pd-vd-name">
                      {v.name}
                      {v.short !== v.name && <span className="pd-muted"> · {v.short}</span>}
                    </span>
                    <Tag tone={(v.level ?? 'top') === 'top' ? 'success' : undefined}>{v.kind === 'conference' ? ((v.level ?? 'top') === 'top' ? '顶会' : '重要会议') : (v.level ?? 'top') === 'top' ? '顶刊' : '重要期刊'}</Tag>
                    {v.intro && <span className="pd-vd-intro">{v.intro}</span>}
                  </button>
                </li>
              ))}
            </ul>
          </section>
        ))}
      </div>
    </div>
  )
}
