import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { requestZh, resetWorkZh, useWorkZhStore } from './workZh'

const apiRequest = vi.fn()
vi.mock('@/lib/api', () => ({ apiRequest: (...a: unknown[]) => apiRequest(...a) }))

const flushTimers = async (): Promise<void> => {
  await vi.advanceTimersByTimeAsync(400)
}

describe('找到的论文自动配中文', () => {
  beforeEach(() => {
    vi.useFakeTimers()
    resetWorkZh()
    apiRequest.mockReset()
  })
  afterEach(() => vi.useRealTimers())

  it('标题一批发出、结果按 key 存下；已经是中文的不请求，同一篇不重复请求', async () => {
    apiRequest.mockResolvedValue({ items: { a: '图注意力网络' } })
    requestZh({ key: 'a', title: 'Graph Attention Networks', abstract: null }, { abstract: false })
    requestZh({ key: 'a', title: 'Graph Attention Networks', abstract: null }, { abstract: false })
    requestZh({ key: 'b', title: '已经是中文的标题', abstract: null }, { abstract: false })
    await flushTimers()
    expect(apiRequest).toHaveBeenCalledTimes(1)
    expect(apiRequest.mock.calls[0][2].body).toEqual({ kind: 'title', items: [{ id: 'a', text: 'Graph Attention Networks' }] })
    expect(useWorkZhStore.getState().byKey.a).toEqual({ title: '图注意力网络' })
  })

  it('请求失败（断网、网关暂时不可用）后歇一会儿，不反复请求；歇过了会自动再试，译文照常出现', async () => {
    apiRequest.mockRejectedValueOnce(new Error('gateway down'))
    requestZh({ key: 'a', title: 'Graph Attention Networks', abstract: null }, { abstract: false })
    await flushTimers()
    expect(useWorkZhStore.getState().disabled).toBe(true)
    const n = apiRequest.mock.calls.length
    requestZh({ key: 'c', title: 'Another English Title Here', abstract: null }, { abstract: false })
    await flushTimers()
    expect(apiRequest.mock.calls.length).toBe(n) // 歇着：不请求
    apiRequest.mockResolvedValue({ items: { a: '图注意力网络', c: '另一个标题' } })
    await vi.advanceTimersByTimeAsync(61_000)
    expect(useWorkZhStore.getState().byKey.a).toEqual({ title: '图注意力网络' })
    expect(useWorkZhStore.getState().byKey.c).toEqual({ title: '另一个标题' })
  })

  it('摘要单独一类请求', async () => {
    apiRequest.mockResolvedValue({ items: { a: '中文摘要' } })
    const abs = 'We present graph attention networks, novel neural network architectures that operate on graph-structured data.'
    requestZh({ key: 'a', title: '中文标题', abstract: abs }, { abstract: true })
    await flushTimers()
    expect(apiRequest.mock.calls[0][2].body.kind).toBe('abstract')
    expect(useWorkZhStore.getState().byKey.a).toEqual({ abstract: '中文摘要' })
  })
})
