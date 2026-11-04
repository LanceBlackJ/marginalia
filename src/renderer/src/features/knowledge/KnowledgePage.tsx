import { useEffect } from 'react'
import { useSearchParams } from 'react-router-dom'
import { Segmented } from '@/components/ui'
import { CandidatesView } from './CandidatesView'
import { GraphView } from './GraphView'
import { ItemsView } from './ItemsView'
import { ProfileView } from './ProfileView'
import { useKb } from './knowledgeStore'
import '@/features/discovery/discovery.css'
import '@/features/compare/compare.css'
import './knowledge.css'

type Tab = 'items' | 'graph' | 'candidates' | 'profile'

export function KnowledgePage() {
  const [params, setParams] = useSearchParams()
  const tab: Tab = (params.get('tab') as Tab | null) ?? 'items'
  const loadTopics = useKb((s) => s.loadTopics)

  useEffect(() => {
    void loadTopics()
  }, [loadTopics])

  const setTab = (t: Tab): void => {
    const next = new URLSearchParams(params)
    if (t === 'items') next.delete('tab')
    else next.set('tab', t)
    setParams(next, { replace: true })
  }

  return (
    <div className="pd-page">
      <div className="pd-page-inner">
        <h1 className="pd-page-title">主题知识库</h1>
        <p className="pd-page-subtitle">把分散在各篇论文里的方法、发现和概念按主题整理。AI 只提出候选，每条都要你确认，并能回到原文证据。</p>
        <div className="pd-dc-tabs">
          <Segmented
            variant="text"
            aria-label="知识库视图"
            value={tab}
            onChange={setTab}
            options={[
              { value: 'items', label: '知识条目' },
              { value: 'graph', label: '关系图' },
              { value: 'candidates', label: '待确认候选' },
              { value: 'profile', label: '个人知识画像' }
            ]}
          />
        </div>
        <div style={{ marginTop: 'var(--sp-xl)' }}>
          {tab === 'items' && <ItemsView />}
          {tab === 'graph' && <GraphView />}
          {tab === 'candidates' && <CandidatesView />}
          {tab === 'profile' && <ProfileView />}
        </div>
      </div>
    </div>
  )
}
