import { beforeEach, describe, expect, it, vi } from 'vitest'

const insight = { role: '过渡衔接', does: '铺出问题', takeaway: '核心意思', key_sentence: '原话一句', from: '接上文', to: '铺下文', watch: ['术语 A'], see: [{ ord: 3, why: '对照定义' }] }

vi.mock('@/lib/api', async (orig) => {
  const real = await orig<typeof import('@/lib/api')>()
  return {
    ...real,
    api: {
      ...real.api,
      papers: {
        ...real.api.papers,
        insight: vi.fn(async (_id: string, ord: number) => ({ passage: { ord, page: 2, bbox: [1, 2, 3, 4], section_title: null, text: 't' }, insight, cached: false })),
        locate: vi.fn(async () => ({ matches: [{ ord: 7, page: 2, bbox: [0, 0, 1, 1], score: 0.9, section_title: null }] }))
      }
    }
  }
})

import { api } from '@/lib/api'
import { useParaStore } from './paraStore'

beforeEach(() => useParaStore.getState().close())

describe('这一段怎么读：状态', () => {
  it('打开一段：先加载，返回后有分析结果和段落位置（PDF 据此高亮）；关闭后清空', async () => {
    const run = useParaStore.getState().open('p1', 5)
    expect(useParaStore.getState().status).toBe('loading')
    await run
    const s = useParaStore.getState()
    expect(s.status).toBe('ready')
    expect(s.passage?.bbox).toEqual([1, 2, 3, 4])
    expect(s.insight?.takeaway).toBe('核心意思')
    s.close()
    expect(useParaStore.getState().ord).toBeNull()
  })

  it('PDF 里的选区没有段落序号：先按文字定位再分析；已知序号（文字视图）直接分析', async () => {
    await useParaStore.getState().openFromSelection('p1', { text: '一段话', page: 2 })
    expect(api.papers.locate).toHaveBeenCalled()
    expect(useParaStore.getState().ord).toBe(7)
    await useParaStore.getState().openFromSelection('p1', { text: 'x', page: 2, ord: 11 })
    expect(useParaStore.getState().ord).toBe(11)
  })

  it('连续点了两段：后一次的结果为准，前一次晚到的不会盖掉它', async () => {
    vi.mocked(api.papers.insight).mockImplementationOnce(
      () => new Promise((resolve) => setTimeout(() => resolve({ passage: { ord: 1, page: 1, bbox: [0, 0, 1, 1], section_title: null, text: 'a' }, insight, cached: false }), 30))
    )
    const first = useParaStore.getState().open('p1', 1)
    await useParaStore.getState().open('p1', 2)
    await first
    expect(useParaStore.getState().ord).toBe(2)
    expect(useParaStore.getState().passage?.ord).toBe(2)
  })
})
