import { useEffect, useRef, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { AlertTriangle, Layers } from 'lucide-react'
import { Button, EmptyState, Skeleton } from '@/components/ui'
import { api } from '@/lib/api'
import { SelectTranslate } from '@/components/SelectTranslate'
import { useReaderStore } from '@/features/reader/readerStore'
import { CardView } from './CardView'
import { Composer } from './Composer'
import { ConversationBar } from './ConversationBar'
import { PendingCardView } from './PendingCardView'
import { isEquationCard } from '@/lib/types.vision'
import { lastCardId, useInsightStore, visibleCards } from './insightStore'

const STARTERS = ['这篇论文要解决什么问题？', '作者为什么要这样设计方法？', '实验结果是否支持作者的结论？']

function useAIConfigured(): boolean | null {
  const [configured, setConfigured] = useState<boolean | null>(null)
  useEffect(() => {
    let alive = true
    api.settings
      .getAI()
      .then((s) => alive && setConfigured(s.configured))
      .catch(() => alive && setConfigured(null))
    return () => {
      alive = false
    }
  }, [])
  return configured
}

export function InsightPanel() {
  const navigate = useNavigate()
  const cards = useInsightStore((s) => s.cards)
  const pending = useInsightStore((s) => s.pending)
  const active = useInsightStore((s) => s.activeChain)
  const conversation = useInsightStore((s) => s.activeConversation)
  const loading = useInsightStore((s) => s.loading)
  const error = useInsightStore((s) => s.error)
  const ask = useInsightStore((s) => s.ask)
  const configured = useAIConfigured()
  const paperId = useReaderStore((s) => s.paperId)
  const scrollRef = useRef<HTMLDivElement>(null)
  const stick = useRef(true)

  // 重答期间，被改写的那张卡片让位给进行中的卡片；没成功时它原样回来
  const list = visibleCards(cards, active, conversation).filter((c) => c.id !== pending?.editingCardId)
  const lastId = lastCardId(cards, conversation)

  // 发起新提问时一定滚到最新卡片
  const pendingId = pending?.tempId
  useEffect(() => {
    if (pendingId) stick.current = true
  }, [pendingId])

  // 新内容出现时，如果用户本来就在底部则跟随滚动
  useEffect(() => {
    const el = scrollRef.current
    if (el && stick.current) el.scrollTop = el.scrollHeight
  }, [list.length, pending?.text, pending?.status, pending?.meta])

  // 往上滚就立刻不再跟随（输出还在继续也不会把你拉回底部）；滚回最底部附近才重新跟随
  const lastTop = useRef(0)
  const onScroll = (): void => {
    const el = scrollRef.current
    if (!el) return
    const dist = el.scrollHeight - el.scrollTop - el.clientHeight
    if (el.scrollTop < lastTop.current - 2) stick.current = false
    else if (dist < 24) stick.current = true
    lastTop.current = el.scrollTop
  }
  const onWheel = (e: React.WheelEvent): void => {
    if (e.deltaY < 0) stick.current = false
  }

  return (
    <div className="pd-rpanel-body">
      <ConversationBar />
      <div className="pd-rpanel-scroll pd-flow" ref={scrollRef} onScroll={onScroll} onWheel={onWheel}>
        {configured === false && (
          <div className="pd-card-hint pd-card-hint--warn" role="note">
            <AlertTriangle size={14} />
            <span style={{ flex: 1 }}>还没有配置 AI 模型，翻译、解释和提问暂时不可用。</span>
            <Button size="sm" onClick={() => navigate('/settings/ai')}>
              去设置
            </Button>
          </div>
        )}

        {loading ? (
          <div style={{ display: 'flex', flexDirection: 'column', gap: 10 }}>
            <Skeleton height={90} />
            <Skeleton height={90} />
          </div>
        ) : error ? (
          <div className="pd-field-error">研究卡片加载失败：{error}</div>
        ) : list.length === 0 && !pending ? (
          <EmptyState
            icon={<Layers size={20} />}
            title={active ? '这里还没有卡片' : '从论文里的一段文字开始'}
            description={active ? '切换到“全部卡片”查看其他内容。' : '选中论文中的文字并选择“解释 / 总结 / 设计动机”，或直接向这篇论文提问。重要的回答可以保存为研究卡片，形成分析链。'}
            action={
              !active && (
                <div className="pd-starters">
                  {STARTERS.map((q) => (
                    <button key={q} type="button" className="pd-followup" onClick={() => void ask({ question: q, intent: 'free' })}>
                      {q}
                    </button>
                  ))}
                </div>
              )
            }
          />
        ) : (
          list.map((c) => <CardView key={c.id} card={c} editable={!pending && c.id === lastId && !!c.conversation_id && !isEquationCard(c)} />)
        )}

        {pending && <PendingCardView pending={pending} />}
      </div>

      <Composer />
      <SelectTranslate paperId={paperId} />
    </div>
  )
}
