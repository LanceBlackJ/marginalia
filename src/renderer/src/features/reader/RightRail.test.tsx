// @vitest-environment jsdom
import { afterEach, beforeEach, describe, expect, it } from 'vitest'
import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import { useNotesStore } from '@/features/notes/notesStore'
import { RightRail } from './RightRail'
import { useReaderStore } from './readerStore'

beforeEach(() => {
  useReaderStore.setState({ rightOpen: false, rightTab: 'ai', paper: { role: 'paper' } as never })
  useNotesStore.setState({ notes: [] as never })
})
afterEach(cleanup)

describe('阅读区右边的竖向图标栏', () => {
  it('每个面板一个图标（只有图标，名字在提示里）；材料只在主论文里有', () => {
    render(<RightRail />)
    const names = screen.getAllByRole('button').map((b) => b.getAttribute('aria-label'))
    expect(names).toEqual(['AI 提问', '术语', '概览', '笔记', '导图', '自测', '材料'])
    cleanup()
    useReaderStore.setState({ paper: { role: 'supplement' } as never })
    render(<RightRail />)
    expect(screen.queryByLabelText('材料')).toBeNull()
  })

  it('点哪个就打开哪个面板；再点正在显示的那个，面板收起', () => {
    render(<RightRail />)
    fireEvent.click(screen.getByLabelText('术语'))
    expect(useReaderStore.getState()).toMatchObject({ rightOpen: true, rightTab: 'glossary' })
    expect(screen.getByLabelText('术语').getAttribute('aria-pressed')).toBe('true')
    fireEvent.click(screen.getByLabelText('笔记'))
    expect(useReaderStore.getState()).toMatchObject({ rightOpen: true, rightTab: 'notes' }) // 直接换成另一个，不用先关
    expect(screen.getByLabelText('术语').getAttribute('aria-pressed')).toBe('false')
    fireEvent.click(screen.getByLabelText('笔记'))
    expect(useReaderStore.getState().rightOpen).toBe(false)
  })

  it('面板收起时没有哪个图标处于选中；笔记数量显示在图标上', () => {
    useNotesStore.setState({ notes: [{}, {}, {}] as never })
    const { container } = render(<RightRail />)
    expect(container.querySelectorAll('[data-active="true"]')).toHaveLength(0)
    expect(screen.getByLabelText('笔记 3')).toBeTruthy()
    expect(container.querySelector('.pd-rrail-badge')?.textContent).toBe('3')
  })
})
