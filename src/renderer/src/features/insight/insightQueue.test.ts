import { beforeEach, describe, expect, it, vi } from 'vitest'

const asked: string[] = []
let releaseFirst: (() => void) | null = null

vi.mock('@/lib/api', async (orig) => {
  const real = await orig<typeof import('@/lib/api')>()
  return {
    ...real,
    api: {
      ...real.api,
      ai: {
        // 第一条要等测试放行才答完；之后的立刻答完
        ask: (body: { question: string }, cb: (e: string, d: unknown) => void, signal: AbortSignal) =>
          new Promise<void>((resolve) => {
            asked.push(body.question)
            const finish = (): void => {
              cb('done', { card: { id: `c-${asked.length}`, conversation_id: 'v1', chain_id: 'k1', status: 'complete', saved: false, answer: { direct: 'ok', detail: null }, question: body.question, evidence: [] } })
              resolve()
            }
            if (asked.length === 1) {
              releaseFirst = finish
              signal.addEventListener('abort', () => resolve()) // 被停止：流正常结束，没有 done
            }
            else finish()
          })
      },
      chains: { list: async () => ({ items: [] }) },
      conversations: { list: async () => ({ items: [] }) }
    }
  }
})

import { useInsightStore } from './insightStore'

beforeEach(() => {
  asked.length = 0
  releaseFirst = null
  useInsightStore.setState({ paperId: 'p1', cards: [], pending: null, queue: [], conversations: [], activeConversation: null, activeChain: null })
})

describe('提问排队', () => {
  it('上一条没答完时再发的问题进队列，答完后按顺序自动发出', async () => {
    const st = useInsightStore.getState()
    const first = st.ask({ question: '第一问', intent: 'free' })
    await st.ask({ question: '第二问', intent: 'free' })
    await st.ask({ question: '第三问', intent: 'free' })
    expect(asked).toEqual(['第一问']) // 没有并发发出
    expect(useInsightStore.getState().queue.map((q) => q.input.question)).toEqual(['第二问', '第三问'])
    releaseFirst?.()
    await first
    await new Promise((r) => setTimeout(r, 30))
    expect(asked).toEqual(['第一问', '第二问', '第三问'])
    expect(useInsightStore.getState().queue).toEqual([])
    expect(useInsightStore.getState().cards).toHaveLength(3)
  })

  it('可以取消排队中的某一条；点“停止”不会自动继续发队列', async () => {
    const st = useInsightStore.getState()
    const first = st.ask({ question: '第一问', intent: 'free' })
    await st.ask({ question: '第二问', intent: 'free' })
    const id = useInsightStore.getState().queue[0].id
    useInsightStore.getState().removeQueued(id)
    expect(useInsightStore.getState().queue).toEqual([])
    await st.ask({ question: '第三问', intent: 'free' })
    useInsightStore.getState().stop()
    await first
    await new Promise((r) => setTimeout(r, 30))
    expect(asked).toEqual(['第一问']) // 停止后队列保留，等用户决定
    expect(useInsightStore.getState().queue).toHaveLength(1)
  })
})
