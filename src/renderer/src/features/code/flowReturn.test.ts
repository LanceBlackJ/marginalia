// @vitest-environment jsdom
import { beforeEach, describe, expect, it, vi } from 'vitest'

vi.mock('@/lib/api.code', () => ({
  codeApi: {
    graph: async (_r: string, id: number) => ({ symbol: { id, path: 'a.py', name: 'f', qualname: 'f', kind: 'function', parent: null, line_start: 1, line_end: 5, signature: '', doc: null }, callees: [], callers: [] }),
    file: async (_r: string, path: string) => ({ path, language: 'python', size: 1, lines: 5, role: 'other', skipped: null, symbols: [], content: 'x\n', truncated: false, note: null }),
    symbolPaper: async () => null
  }
}))

import { useCodeStore } from './codeStore'

beforeEach(() => useCodeStore.setState({ repoId: 'r1', tab: 'flow', flowSelected: 7, flowReturn: null, openPath: null, file: null }))

describe('运行流程 → 代码 → 返回', () => {
  it('从节点进入代码页后记住节点；返回时回到流程并仍选中它', async () => {
    await useCodeStore.getState().viewFromFlow(9, '读数据')
    let s = useCodeStore.getState()
    expect(s.tab).toBe('code')
    expect(s.flowReturn).toEqual({ tab: "flow", id: 9, label: "读数据" })
    s.backFromCode()
    s = useCodeStore.getState()
    expect(s.tab).toBe('flow')
    expect(s.flowSelected).toBe(9)
    expect(s.flowReturn).toBeNull()
  })
  it('离开代码页去别的标签后，不再显示返回按钮', async () => {
    await useCodeStore.getState().viewFromFlow(9, 'x')
    useCodeStore.getState().setTab('mapping')
    expect(useCodeStore.getState().flowReturn).toBeNull()
  })
})

describe('小窗里的代码片段', () => {
  it('从论文↔代码打开小窗，再进代码页，返回时回到论文↔代码', async () => {
    const st = useCodeStore.getState()
    useCodeStore.setState({ tab: 'mapping' })
    st.showSnippet({ path: 'a.py', a: 3, b: 9, title: 'f', symbolId: null, from: 'mapping' })
    expect(useCodeStore.getState().snippet?.b).toBe(9)
    await useCodeStore.getState().openSnippetInCode()
    let s = useCodeStore.getState()
    expect(s.snippet).toBeNull()
    expect(s.tab).toBe('code')
    expect(s.flowReturn).toMatchObject({ tab: 'mapping', label: 'f' })
    s.backFromCode()
    s = useCodeStore.getState()
    expect(s.tab).toBe('mapping')
    expect(s.flowReturn).toBeNull()
  })
})
