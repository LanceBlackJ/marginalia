import { beforeEach, describe, expect, it, vi } from 'vitest'

const card = { id: 'c1', conversation_id: 'v1', chain_id: 'k1', status: 'complete', saved: false, answer: { direct: '已经写出的前半句', detail: null }, question: '问', evidence: [] }

vi.mock('@/lib/api', async (orig) => {
  const real = await orig<typeof import('@/lib/api')>()
  return {
    ...real,
    api: {
      ...real.api,
      ai: {
        // 先给 meta 和两段文字，然后等用户点“停止”：流被中止后正常结束（不抛错）
        ask: (_body: unknown, cb: (e: string, d: unknown) => void, signal: AbortSignal) =>
          new Promise<void>((resolve) => {
            cb('meta', { card_id: 'c1', conversation_id: 'v1', model: 'm', context: { passage_ords: [], passage_count: 0, chars_sent: 0, includes: {} } })
            cb('delta', { text: '已经写出的' })
            cb('delta', { text: '前半句' })
            signal.addEventListener('abort', () => resolve())
          })
      },
      cards: { ...real.api.cards, list: async () => ({ items: [card] }) },
      chains: { list: async () => ({ items: [] }) },
      conversations: { list: async () => ({ items: [] }) }
    }
  }
})

import { useInsightStore } from './insightStore'

beforeEach(() => useInsightStore.setState({ paperId: 'p1', cards: [], pending: null, conversations: [], activeConversation: null, activeChain: null }))

describe('停止生成', () => {
  it('流在中止后正常结束：不是“连接意外中断”，已经输出的文字留着，随后换成保存下来的卡片', async () => {
    const run = useInsightStore.getState().ask({ question: '问', intent: 'free' })
    await new Promise((r) => setTimeout(r, 20))
    expect(useInsightStore.getState().pending?.text).toBe('已经写出的前半句')
    useInsightStore.getState().stop()
    await run
    const p = useInsightStore.getState().pending
    expect(p?.status).toBe('stopped')
    expect(p?.text).toBe('已经写出的前半句') // 输出到哪就留在哪
    expect(p?.error).toBeNull()
    await new Promise((r) => setTimeout(r, 600))
    expect(useInsightStore.getState().pending).toBeNull()
    expect(useInsightStore.getState().cards.map((c) => c.id)).toEqual(['c1']) // 后端存下的那张卡片取回来了
  })
})
