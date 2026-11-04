import { useEffect, useState } from 'react'
import { ArrowLeft, ExternalLink } from 'lucide-react'
import { useNavigate, useSearchParams } from 'react-router-dom'
import { Button, Skeleton, Tag } from '@/components/ui'
import { errorMessage } from '@/lib/api'
import { discoveryApi } from '@/lib/api.discovery'
import { openExternal } from '@/lib/native'
import { useBack } from '@/lib/useBack'
import type { VenuePageData, Work } from '@/lib/types.discovery'
import { VenueCardBody } from './VenueHover'
import { WorkDrawer } from './WorkDrawer'
import { WorkItem } from './WorkItem'
import './discovery.css'

const KIND_ZH: Record<string, string> = { journal: '期刊', conference: '会议', 'book series': '丛书', repository: '预印本/文库平台' }

/** 一个期刊/会议的页面：它是什么、水平如何（只给数字和依据，不编造分区）、在这里发表的被引最多和最新的论文。 */
export function VenuePage() {
  const [params] = useSearchParams()
  const navigate = useNavigate()
  const back = useBack('/venues')
  const id = params.get('id')
  const name = params.get('name')
  const type = params.get('type')
  const oa = params.get('oa')
  const [worksLoading, setWorksLoading] = useState(false)
  const [data, setData] = useState<VenuePageData | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [tab, setTab] = useState<'cited' | 'recent'>('cited')
  const [selected, setSelected] = useState<Work | null>(null)

  useEffect(() => {
    let alive = true
    setData(null)
    setError(null)
    setWorksLoading(true)
    // 先只取介绍（很快），页面马上出来；论文列表另取，慢一点也不挡着介绍
    discoveryApi
      .venuePage({ id, name, type, oa, works: false })
      .then((d) => {
        if (!alive) return
        setData(d)
        return discoveryApi.venuePage({ id: d.id ?? id, name: d.name, type, oa, works: true }).then((full) => {
          if (alive) setData((x) => (x ? { ...x, works: full.works, errors: full.errors } : full))
        })
      })
      .catch((e) => alive && setError(errorMessage(e)))
      .finally(() => alive && setWorksLoading(false))
    return () => {
      alive = false
    }
  }, [id, name, type, oa])

  const st = data?.info.stats
  const works = data?.works[tab] ?? []
  const kind = data?.kind ? (KIND_ZH[data.kind] ?? data.kind) : null

  return (
    <div className="pd-page">
      <div className="pd-page-inner pd-dc-page">
        <button type="button" className="pd-pp-back" onClick={back}>
          <ArrowLeft size={14} /> 返回
        </button>
        {error && <p className="pd-status-text pd-status-text--danger" style={{ marginTop: 16 }}>{error}</p>}
        {!data && !error && (
          <div style={{ marginTop: 16 }}>
            <Skeleton height={120} />
          </div>
        )}
        {data && (
          <>
            <header className="pd-vp-head">
              <h1 className="pd-page-title">{data.name}</h1>
              <div className="pd-vp-tags">
                {data.short && data.short !== data.name && <Tag>{data.short}</Tag>}
                {kind && <Tag>{kind}</Tag>}
                {data.label && <Tag tone={data.level === 'top' ? 'success' : undefined}>{data.label}</Tag>}
                {data.category && <Tag>{data.category}</Tag>}
              </div>
              {st?.homepage && (
                <Button size="sm" icon={<ExternalLink size={14} />} onClick={() => void openExternal(st.homepage as string)}>
                  官方主页
                </Button>
              )}
            </header>

            <section className="pd-vp-intro">
              <h2 className="pd-dc-h">关于这个来源</h2>
              <div className="pd-vp-introbody">
                <VenueCardBody info={data.info} loading={false} hideBadge />
              </div>
              {st && (
                <dl className="pd-vp-stats">
                  <div>
                    <dt>累计论文</dt>
                    <dd>{st.works_count?.toLocaleString() ?? '—'}</dd>
                  </div>
                  <div>
                    <dt>总被引</dt>
                    <dd>{st.cited_by_count?.toLocaleString() ?? '—'}</dd>
                  </div>
                  <div>
                    <dt>h 指数</dt>
                    <dd>{st.h_index ?? '—'}</dd>
                  </div>
                  <div>
                    <dt>近两年平均被引</dt>
                    <dd>{st.mean_citedness_2y !== null && st.mean_citedness_2y !== undefined ? st.mean_citedness_2y.toFixed(2) : '—'}</dd>
                  </div>
                </dl>
              )}
            </section>

            <section>
              <div className="pd-vp-tabs" role="tablist" aria-label="这个来源上的论文">
                <button type="button" role="tab" aria-selected={tab === 'cited'} className="pd-dc-tab" onClick={() => setTab('cited')}>
                  被引最多
                </button>
                <button type="button" role="tab" aria-selected={tab === 'recent'} className="pd-dc-tab" onClick={() => setTab('recent')}>
                  最新发表
                </button>
              </div>
              {data.errors.map((e) => (
                <p key={e} className="pd-dc-hint">
                  {e}
                </p>
              ))}
              {worksLoading && works.length === 0 && <Skeleton height={80} />}
              {!worksLoading && works.length === 0 && data.errors.length === 0 && <p className="pd-dc-hint">没有取到这个来源上的论文。</p>}
              <div className="pd-dc-results">
                {works.map((w) => (
                  <WorkItem key={`${tab}-${w.key}`} work={w} onOpen={setSelected} />
                ))}
              </div>
            </section>
          </>
        )}
      </div>
      <WorkDrawer work={selected} onClose={() => setSelected(null)} onOpenOther={(w) => navigate(`/discover`, { state: { open: w.key } })} />
    </div>
  )
}
