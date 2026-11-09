// @vitest-environment jsdom
import { beforeEach, describe, expect, it } from 'vitest'
import { useReaderStore } from './readerStore'

beforeEach(() => useReaderStore.setState({ numPages: 10, currentPage: 3, textAnchor: 42, readView: 'text', backStack: [], jump: null }))

describe('跳转历史', () => {
  it('跳走之前记住位置，goBack 回到那一段', () => {
    const s = useReaderStore.getState()
    s.jumpToPage(8)
    expect(useReaderStore.getState().backStack).toEqual([{ page: 3, ord: 42 }])
    useReaderStore.setState({ currentPage: 8, textAnchor: 90 })
    useReaderStore.getState().goBack()
    const st = useReaderStore.getState()
    expect(st.backStack).toEqual([])
    expect(st.jump).toMatchObject({ page: 3, ord: 42, flash: false })
  })

  it('切换界面（跳到当前页）不算跳走；PDF 界面不记段落序号；可以连退多步', () => {
    const s = useReaderStore.getState()
    s.jumpToPage(3)
    expect(useReaderStore.getState().backStack).toEqual([])
    useReaderStore.setState({ readView: 'pdf' })
    s.jumpToSection(5, '方法')
    expect(useReaderStore.getState().backStack).toEqual([{ page: 3, ord: null }])
    useReaderStore.setState({ currentPage: 5 })
    s.jumpToEvidence({ page: 9, bbox: [0, 0, 1, 1], ord: 7 })
    expect(useReaderStore.getState().backStack.map((b) => b.page)).toEqual([3, 5])
    useReaderStore.getState().goBack()
    useReaderStore.getState().goBack()
    expect(useReaderStore.getState().backStack).toEqual([])
  })
})
