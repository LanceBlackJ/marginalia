import { useCallback, useEffect, useState } from 'react'
import { useSearchParams } from 'react-router-dom'
import { ChevronDown, ChevronRight, Search } from 'lucide-react'
import { Button, Input, Segmented, Select, Switch } from '@/components/ui'
import { useLibraryPapers } from '@/features/shared/useLibraryPapers'
import type { DiscoveryMode, Work } from '@/lib/types.discovery'
import { SearchReport } from './Report'
import { WorkDrawer } from './WorkDrawer'
import { KindFilter, filterByKind, useKindFilter } from './KindFilter'
import { WorkItem } from './WorkItem'
import { useDiscovery } from './discoveryStore'
import { useLibraryMatchRefresh } from './useWorkActions'
import { DcLoading } from './shared'
import { Pager, usePaging } from './Pager'
import { MODE_HINTS, MODE_OPTIONS } from './workUtils'
import './discovery.css'

const EXAMPLES = ['cascading failure network recovery deep reinforcement learning', '用图神经网络识别复杂网络中的关键节点', 'single-cell RNA sequencing batch effect correction']

export function DiscoverPage() {
  const s = useDiscovery()
  const [params, setParams] = useSearchParams()
  const [selected, setSelected] = useState<Work | null>(null)
  const [advOpen, setAdvOpen] = useState(false)
  const { items: libraryItems } = useLibraryPapers()

  // 深链：/discover?q=…&mode=…&seed=<paper_id>（阅读器或文献库里的“相关工作”入口使用）。
  // 处理后只清掉这三个参数，保留页内方式 tab。
  useEffect(() => {
    const q = params.get('q')
    const seed = params.get('seed')
    if (!q && !seed) return
    const mode = (params.get('mode') as DiscoveryMode | null) ?? (seed ? 'related' : useDiscovery.getState().mode)
    useDiscovery.getState().set({ query: q ?? '', mode, seedPaperId: seed ?? '' })
    void useDiscovery.getState().search()
    setParams(
      (prev) => {
        const next = new URLSearchParams(prev)
        next.delete('q')
        next.delete('mode')
        next.delete('seed')
        return next
      },
      { replace: true }
    )
  }, [params, setParams])

  const onMatches = useCallback((matches: Record<string, string>) => {
    for (const [key, paperId] of Object.entries(matches)) useDiscovery.getState().patchWork(key, { in_library: paperId })
  }, [])
  useLibraryMatchRefresh(s.data?.results ?? [], onMatches)

  const changeMode = (mode: DiscoveryMode): void => {
    s.set({ mode })
    if (s.status !== 'idle') void useDiscovery.getState().search()
  }

  const allResults = s.data?.results ?? []
  const kindState = useKindFilter(allResults)
  const results = filterByKind(allResults, kindState.kind)
  const paging = usePaging(results, `${s.data?.query ?? ''}|${s.data?.mode ?? ''}|${allResults.length}|${kindState.kind ?? ''}`)
  const seedOptions = [
    { value: '', label: '手动输入标题或 DOI' },
    ...libraryItems.map((p) => ({ value: p.id, label: `${p.title.slice(0, 70)}${p.year ? ` · ${p.year}` : ''}` }))
  ]

  return (
    <>
      <div>
        <div className="pd-dc-search">
          <Input
            value={s.query}
            onChange={(v) => s.set({ query: v })}
            onEnter={() => void s.search()}
            icon={<Search size={16} />}
            clearable
            placeholder={s.mode === 'related' ? '基准论文的标题或 DOI……' : '描述你想研究的问题、方法或领域……'}
            aria-label="检索内容"
          />
          <Button variant="primary" data-dc-primary="search" loading={s.status === 'loading'} onClick={() => void s.search()}>
            搜索
          </Button>
        </div>

        <div className="pd-dc-options">
          <Segmented variant="text" aria-label="检索模式" value={s.mode} options={MODE_OPTIONS.map((o) => ({ value: o.value, label: o.label }))} onChange={changeMode} />
          <button type="button" className="pd-dc-advbtn" aria-expanded={advOpen} onClick={() => setAdvOpen((v) => !v)}>
            {advOpen ? <ChevronDown size={14} /> : <ChevronRight size={14} />}
            高级选项
            <span className="pd-dc-advstate">
              AI 重排{s.ai ? '开' : '关'}
              {s.yearFrom || s.yearTo ? ` · ${s.yearFrom || '…'}–${s.yearTo || '…'} 年` : ''}
            </span>
          </button>
        </div>
        {advOpen && (
          <div className="pd-dc-adv">
            <Switch checked={s.ai} onChange={(v) => s.set({ ai: v })} label="AI 理解与重排" />
            <span className="pd-dc-years">
              年份
              <Input value={s.yearFrom} onChange={(v) => s.set({ yearFrom: v.replace(/\D/g, '').slice(0, 4) })} placeholder="起" inputMode="numeric" aria-label="起始年份" />
              –
              <Input value={s.yearTo} onChange={(v) => s.set({ yearTo: v.replace(/\D/g, '').slice(0, 4) })} placeholder="止" inputMode="numeric" aria-label="截止年份" />
            </span>
          </div>
        )}
        <p className="pd-dc-hint">{MODE_HINTS[s.mode]}</p>

        {s.mode === 'related' && (
          <div className="pd-dc-seed">
            <span>基准论文</span>
            <Select value={s.seedPaperId} onChange={(v) => s.set({ seedPaperId: v })} options={seedOptions} aria-label="从文献库选择基准论文" />
          </div>
        )}

        {s.status === 'error' && s.error && <p className="pd-status-text pd-status-text--danger" style={{ marginTop: 'var(--sp-lg)' }}>{s.error}</p>}

        {s.status === 'idle' && (
          <div className="pd-dc-examples">
            <span>试试这样搜：</span>
            {EXAMPLES.map((e) => (
              <button
                key={e}
                type="button"
                onClick={() => {
                  s.set({ query: e })
                  void useDiscovery.getState().search()
                }}
              >
                {e}
              </button>
            ))}
          </div>
        )}

        {s.status === 'loading' && <DcLoading rows={5} text={`正在检索多个来源、去重并排序……${s.ai ? '开启 AI 时通常需要 10–40 秒。' : ''}`} />}

        {s.status === 'done' && s.data && (
          <>
            <SearchReport data={s.data} />
            <KindFilter works={allResults} value={kindState.kind} onChange={kindState.setKind} />
            <div className="pd-dc-results">
              {results.length === 0 && <p className="pd-dc-hint pd-dc-empty-line">没有找到符合条件的论文。可以换一种说法、放宽年份，或切换到“综合搜索”。</p>}
              {paging.shown.map((w) => (
                <WorkItem key={w.key} work={w} onOpen={setSelected} />
              ))}
            </div>
            <Pager page={paging.page} pages={paging.pages} total={results.length} onPage={paging.setPage} />
          </>
        )}

      </div>
      <WorkDrawer work={selected ? (results.find((w) => w.key === selected.key) ?? selected) : null} onClose={() => setSelected(null)} onOpenOther={setSelected} />
    </>
  )
}
