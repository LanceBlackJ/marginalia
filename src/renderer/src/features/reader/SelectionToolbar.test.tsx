// @vitest-environment jsdom
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import type { SelectionInfo } from './pdf/selection'

const mocks = vi.hoisted(() => ({
  selection: null as unknown,
  equation: null as unknown,
  byIndex: new Map<number, unknown>(),
  actions: {
    askFreeform: vi.fn(),
    askQuick: vi.fn(),
    copySelection: vi.fn(),
    explainEquation: vi.fn(),
    highlightSelection: vi.fn(),
    noteSelection: vi.fn(),
    openCitation: vi.fn(),
    roleSelection: vi.fn(),
    searchSelection: vi.fn(),
    translateSelection: vi.fn()
  },
  setSelection: vi.fn()
}))

vi.mock('./selectionActions', () => mocks.actions)
vi.mock('./readerStore', () => ({
  useReaderStore: Object.assign((sel: (s: unknown) => unknown) => sel({ selection: mocks.selection }), {
    getState: () => ({ setSelection: mocks.setSelection })
  })
}))
vi.mock('@/features/formula/useEquationAtSelection', () => ({ useEquationAtSelection: () => mocks.equation }))
vi.mock('@/features/text/refStore', () => ({
  useRefStore: (sel: (s: unknown) => unknown) => sel({ byIndex: mocks.byIndex })
}))

import { SelectionToolbar } from './SelectionToolbar'

function select(text: string, extra: Partial<SelectionInfo> = {}): void {
  mocks.selection = {
    text,
    page: 3,
    rects: [],
    clientRect: { left: 300, right: 420, top: 300, bottom: 320 },
    ...extra
  }
}

function mainLabels(): string[] {
  return Array.from(document.querySelectorAll<HTMLButtonElement>('button[data-seltool="main"]')).map((b) => b.getAttribute('aria-label') ?? '')
}

beforeEach(() => {
  Object.values(mocks.actions).forEach((f) => f.mockClear())
  mocks.setSelection.mockClear()
  mocks.equation = null
  mocks.byIndex = new Map()
  localStorage.clear()
})
afterEach(cleanup)

describe('SelectionToolbar 主条', () => {
  it('选一句话（不到一整段）：只有翻译、解释、笔记和更多四个按钮', () => {
    select('The network, or graph in discrete mathematics')
    render(<SelectionToolbar />)
    expect(mainLabels()).toEqual(['翻译', '解释', '笔记', '更多'])
    expect(document.querySelectorAll('.pd-seltool-swatch').length).toBe(0) // 不再并排放颜色圆点
  })

  it('选的是一整段话：多一个“这段怎么读”，点了去分析选中的这一段', () => {
    const text = 'The network, or graph in discrete mathematics, is a common data structure that models relations between objects.'
    select(text)
    render(<SelectionToolbar />)
    expect(mainLabels()).toEqual(['翻译', '解释', '这段怎么读', '笔记', '更多'])
    fireEvent.click(screen.getByRole('button', { name: /这段怎么读/ }))
    expect(mocks.actions.roleSelection).toHaveBeenCalledWith(expect.objectContaining({ text }))
  })

  it('中文选区没有翻译，解释在第一位', () => {
    select('这是一段关于网络瓦解问题的中文描述，作者给出了形式化定义。')
    render(<SelectionToolbar />)
    expect(mainLabels()).toEqual(['解释', '笔记', '更多'])
  })

  it('选中一个词时翻译在第一位，且工具条标记为紧凑', () => {
    select('percolation')
    render(<SelectionToolbar />)
    expect(mainLabels()[0]).toBe('翻译')
    expect(document.querySelector('.pd-seltool')?.getAttribute('data-compact')).toBe('true')
  })

  it('选区落在公式上才出现解释公式，并替换掉解释', () => {
    mocks.equation = { id: 7, score: 0.9 }
    select('R(v1, v2, ..., vN) = 1/N')
    render(<SelectionToolbar />)
    expect(mainLabels()).toEqual(['解释公式', '笔记', '更多'])
    fireEvent.click(screen.getByLabelText('解释公式'))
    expect(mocks.actions.explainEquation).toHaveBeenCalledWith(expect.anything(), 7)
  })

  it('只有选的是对得上参考文献的编号时才出现查看引用', () => {
    select('12')
    const { unmount } = render(<SelectionToolbar />)
    expect(mainLabels()).not.toContain('查看引用') // 参考文献里没有 12
    unmount()
    mocks.byIndex = new Map([[12, {}]])
    select('[12]')
    render(<SelectionToolbar />)
    expect(mainLabels()[0]).toBe('查看引用')
    fireEvent.click(screen.getByLabelText('查看引用'))
    expect(mocks.actions.openCitation).toHaveBeenCalledWith(expect.anything(), 12)
  })

  it('主条的动作按原来的方式调用', () => {
    select('The network is a common data structure to describe systems')
    render(<SelectionToolbar />)
    fireEvent.click(screen.getByLabelText('翻译'))
    expect(mocks.actions.translateSelection).toHaveBeenCalledTimes(1)
    fireEvent.click(screen.getByLabelText('解释'))
    expect(mocks.actions.askQuick).toHaveBeenCalledWith(expect.anything(), 'explain')
    fireEvent.click(screen.getByLabelText('笔记'))
    expect(mocks.actions.noteSelection).toHaveBeenCalledTimes(1)
  })

  it('没有选区时什么也不渲染', () => {
    mocks.selection = null
    render(<SelectionToolbar />)
    expect(document.querySelector('.pd-seltool')).toBeNull()
  })
})

describe('SelectionToolbar 更多菜单', () => {
  const text = 'The network is a common data structure to describe interactive systems'

  it('点更多才展开，里面有总结、术语、设计动机、提问、高亮、复制、搜索', () => {
    select(text)
    render(<SelectionToolbar />)
    expect(screen.queryByRole('menu')).toBeNull()
    fireEvent.click(screen.getByLabelText('更多'))
    const items = screen.getAllByRole('menuitem').map((e) => e.textContent?.trim())
    expect(items).toEqual(['总结', '术语解释', '分析设计动机', '提问', '高亮', '复制', '在文中搜索'])
  })

  it('收进菜单的功能仍然能用', () => {
    select(text)
    render(<SelectionToolbar />)
    const open = (): void => {
      fireEvent.click(screen.getByLabelText('更多'))
    }
    open()
    fireEvent.click(screen.getByText('总结'))
    expect(mocks.actions.askQuick).toHaveBeenLastCalledWith(expect.anything(), 'summarize')
    open()
    fireEvent.click(screen.getByText('术语解释'))
    expect(mocks.actions.askQuick).toHaveBeenLastCalledWith(expect.anything(), 'term')
    open()
    fireEvent.click(screen.getByText('分析设计动机'))
    expect(mocks.actions.askQuick).toHaveBeenLastCalledWith(expect.anything(), 'motivation')
    open()
    fireEvent.click(screen.getByText('提问'))
    expect(mocks.actions.askFreeform).toHaveBeenCalledTimes(1)
    open()
    fireEvent.click(screen.getByText('复制'))
    expect(mocks.actions.copySelection).toHaveBeenCalledTimes(1)
    open()
    fireEvent.click(screen.getByText('在文中搜索'))
    expect(mocks.actions.searchSelection).toHaveBeenCalledTimes(1)
  })

  it('高亮默认用上次的颜色，选了别的颜色后会记住', () => {
    select(text)
    render(<SelectionToolbar />)
    fireEvent.click(screen.getByLabelText('更多'))
    fireEvent.click(screen.getByText('高亮'))
    expect(mocks.actions.highlightSelection).toHaveBeenLastCalledWith(expect.anything(), 'yellow')
    fireEvent.click(screen.getByLabelText('更多'))
    fireEvent.click(screen.getByLabelText('蓝色高亮'))
    expect(mocks.actions.highlightSelection).toHaveBeenLastCalledWith(expect.anything(), 'blue')
    cleanup()
    select(text)
    render(<SelectionToolbar />)
    fireEvent.click(screen.getByLabelText('更多'))
    fireEvent.click(screen.getByText('高亮'))
    expect(mocks.actions.highlightSelection).toHaveBeenLastCalledWith(expect.anything(), 'blue') // 记住了
  })

  it('文字阅读器的选区没有坐标，菜单里没有高亮', () => {
    select(text, { fromText: true })
    render(<SelectionToolbar />)
    fireEvent.click(screen.getByLabelText('更多'))
    expect(screen.queryByText('高亮')).toBeNull()
  })

  it('中文选区的菜单里补上翻译，功能不丢', () => {
    select('这是一段关于网络瓦解问题的中文描述，作者给出了形式化定义。')
    render(<SelectionToolbar />)
    fireEvent.click(screen.getByLabelText('更多'))
    expect(screen.getAllByRole('menuitem')[0].textContent?.trim()).toBe('翻译')
  })

  it('Esc 先收起菜单，再关闭工具条', () => {
    select(text)
    render(<SelectionToolbar />)
    fireEvent.click(screen.getByLabelText('更多'))
    expect(screen.getByRole('menu')).toBeTruthy()
    fireEvent.keyDown(window, { key: 'Escape' })
    expect(screen.queryByRole('menu')).toBeNull()
    expect(mocks.setSelection).not.toHaveBeenCalled()
    fireEvent.keyDown(window, { key: 'Escape' })
    expect(mocks.setSelection).toHaveBeenCalledWith(null)
  })

  it('左右方向键在主条按钮之间移动', () => {
    select(text)
    render(<SelectionToolbar />)
    const first = screen.getByLabelText('翻译')
    first.focus()
    fireEvent.keyDown(first, { key: 'ArrowRight' })
    expect(document.activeElement).toBe(screen.getByLabelText('解释'))
    fireEvent.keyDown(document.activeElement as Element, { key: 'ArrowLeft' })
    expect(document.activeElement).toBe(first)
  })
})
