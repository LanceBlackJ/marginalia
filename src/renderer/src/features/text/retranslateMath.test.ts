// @vitest-environment jsdom
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { mathRanges } from './textModel'

const sent: unknown[] = []
vi.mock('@/lib/api.text', async (orig) => {
  const real = await orig<typeof import('@/lib/api.text')>()
  return {
    ...real,
    textApi: {
      ...real.textApi,
      text: async () => ({ revision: 'r', items: [] }),
      translation: async () => ({ total: 0, done: 0, complete: false, models: [], items: [], revision: 'r' })
    }
  }
})
vi.mock('@/lib/api.vision', () => ({ visionApi: { figures: async () => ({ items: [] }), equations: async () => ({ items: [] }) } }))
vi.mock('@/lib/api', async (orig) => {
  const real = await orig<typeof import('@/lib/api')>()
  return {
    ...real,
    api: {
      ...real.api,
      jobs: { list: async () => ({ items: [] }) },
      ai: {
        ...real.api.ai,
        translate: async (body: unknown) => {
          sent.push(body)
        }
      }
    }
  }
})

import { useTextStore } from './textStore'

const MATH = [{ src: 'b_i ≡ b(si)', tex: 'b_i\\equiv b(s_i)' }]

beforeEach(() => {
  sent.length = 0
  useTextStore.setState({
    paperId: 'p1',
    items: [{ ord: 7, page: 3, kind: 'paragraph', section_title: null, text: 'we denote b_i ≡ b(si).', bbox: [0, 0, 1, 1], math: MATH }] as never,
    trans: new Map(),
    retranslating: new Set<number>()
  })
})

describe('“重新翻译这一段”带上行内公式', () => {
  it('把这段的公式列表和段落序号一起发给后端：后端把公式当占位符保护起来，译文里放回原来的 src', async () => {
    await useTextStore.getState().retranslate(7)
    expect(sent).toEqual([{ paper_id: 'p1', text: 'we denote b_i ≡ b(si).', page: 3, passage_ord: 7, force: true, math: MATH }])
  })

  it('译文里放回了原来的 src，渲染时就能找到它们并变成公式（模型自己改写的“bi ≡ b(si)”不行）', () => {
    expect(mathRanges('我们记 b_i ≡ b(si)。', MATH)).toEqual([[4, 15, 'b_i\\equiv b(s_i)']])
    expect(mathRanges('我们记 bi ≡ b(si)。', MATH)).toEqual([])
  })

  it('公式紧贴中文时仍然按 token 边界渲染', () => {
    const math = [{ src: 'd\\cdot s', tex: 'd\\cdot s' }]
    expect(mathRanges('节点级d\\cdot s公式扩展为网络级量。', math)).toEqual([[3, 11, 'd\\cdot s']])
  })
})
