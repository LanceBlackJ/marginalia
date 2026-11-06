// @vitest-environment jsdom
import { beforeEach, describe, expect, it, vi } from 'vitest'

const state = { text: { revision: 'new', items: [{ ord: 1, page: 1, kind: 'paragraph', section_title: null, text: 'fresh', bbox: [0, 0, 1, 1] }] } as unknown, calls: [] as string[] }
vi.mock('@/lib/api.text', async (orig) => {
  const real = await orig<typeof import('@/lib/api.text')>()
  return {
    ...real,
    textApi: {
      ...real.textApi,
      text: async () => {
        state.calls.push('text')
        return state.text
      },
      translation: async () => {
        state.calls.push('translation')
        return { total: 1, done: 1, complete: true, models: ['m'], items: [{ ord: 1, translation: '新译文' }], revision: 'new' }
      }
    }
  }
})
vi.mock('@/lib/api.vision', () => ({ visionApi: { figures: async () => ({ items: [] }), equations: async () => ({ items: [] }) } }))
vi.mock('@/lib/api', async (orig) => {
  const real = await orig<typeof import('@/lib/api')>()
  return { ...real, api: { ...real.api, jobs: { list: async () => ({ items: [] }) } } }
})

import { useTextStore } from './textStore'

beforeEach(() => {
  state.calls = []
  useTextStore.setState({ paperId: 'p1', revision: 'old', items: [{ ord: 9, page: 1, kind: 'paragraph', section_title: null, text: 'stale', bbox: [0, 0, 1, 1] }] as never, trans: new Map() })
})

describe('阅读页开着时论文被重新解析', () => {
  it('译文的版本号和手里的段落对不上：先把段落重新载入成新版，再配译文，不会整体错位', async () => {
    await useTextStore.getState().refreshTranslation()
    const s = useTextStore.getState()
    expect(s.revision).toBe('new')
    expect(s.items.map((i) => i.text)).toEqual(['fresh']) // 旧版段落被换掉了
    expect(s.trans.get(1)).toBe('新译文')
    expect(state.calls.filter((c) => c === 'text')).toHaveLength(1) // 只重新载入一次，不会循环
  })

  it('版本号一致就直接用译文，不重新载入', async () => {
    useTextStore.setState({ revision: 'new' })
    await useTextStore.getState().refreshTranslation()
    expect(state.calls).toEqual(['translation'])
    expect(useTextStore.getState().trans.get(1)).toBe('新译文')
  })
})
