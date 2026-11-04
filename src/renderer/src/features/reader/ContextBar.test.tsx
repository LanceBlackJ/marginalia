// @vitest-environment jsdom
import { afterEach, describe, expect, it } from 'vitest'
import { cleanup, render, screen } from '@testing-library/react'
import { sectionAt, ContextBar } from './ContextBar'
import { useReaderStore } from './readerStore'

afterEach(() => {
  cleanup()
  useReaderStore.setState({ contextSelection: null, currentPage: 1, outline: [] })
})

describe('sectionAt', () => {
  const outline = [
    { page: 1, title: '摘要' },
    { page: 2, title: '方法' },
    { page: 5, title: '实验' }
  ]
  it('取页码不超过当前页的最后一项', () => {
    expect(sectionAt(outline, 1)).toBe('摘要')
    expect(sectionAt(outline, 4)).toBe('方法')
    expect(sectionAt(outline, 9)).toBe('实验')
    expect(sectionAt([], 3)).toBeNull()
  })
})

describe('ContextBar', () => {
  it('只显示一行位置，选中的文字不在这里出现', () => {
    useReaderStore.setState({
      currentPage: 3,
      outline: [{ id: 'a', title: '方法', level: 1, page: 2 }] as never,
      contextSelection: { text: 'giant connected component', page: 3 }
    })
    const { container } = render(<ContextBar />)
    expect(screen.getByText('第 3 页')).toBeTruthy()
    expect(screen.getByText('方法')).toBeTruthy()
    expect(container.textContent).not.toContain('giant connected component')
    expect(container.querySelectorAll('.pd-context-line').length).toBe(1)
  })
  it('右侧可以放当前标签的筛选', () => {
    render(<ContextBar>筛选内容</ContextBar>)
    expect(screen.getByText('筛选内容').closest('.pd-context-right')).toBeTruthy()
  })
})
