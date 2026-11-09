import { beforeEach, describe, expect, it, vi } from 'vitest'

vi.mock('@/lib/api', async (orig) => {
  const real = await orig<typeof import('@/lib/api')>()
  return {
    ...real,
    api: {
      ...real.api,
      ai: {
        ask: (_b: unknown, cb: (e: string, d: unknown) => void) => {
          cb('done', { card: { id: 'n1', conversation_id: 'v2', chain_id: 'k9', status: 'complete', saved: true, answer: { direct: 'ok', detail: null }, question: 'q', evidence: [] } })
          return Promise.resolve()
        }
      },
      chains: { list: async () => ({ items: [] }) },
      conversations: { list: async () => ({ items: [] }) }
    }
  }
})

import { TEMP_CHAIN, useInsightStore, visibleCards } from './insightStore'

beforeEach(() => useInsightStore.setState({ paperId: 'p1', cards: [], pending: null, queue: [], conversations: [], activeConversation: 'v1', activeChain: TEMP_CHAIN }))

describe('答完的卡片不能凭空消失', () => {
  it('当前停在“临时卡片”、或卡片属于别的对话时，换到能显示它的视图', async () => {
    await useInsightStore.getState().ask({ question: 'q', intent: 'free' })
    const s = useInsightStore.getState()
    expect(visibleCards(s.cards, s.activeChain, s.activeConversation).map((c) => c.id)).toEqual(['n1'])
  })
})
