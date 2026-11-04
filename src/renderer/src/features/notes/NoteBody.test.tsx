// @vitest-environment jsdom
import { afterEach, describe, expect, it, vi } from 'vitest'
import { cleanup, fireEvent, render, screen } from '@testing-library/react'

vi.mock('@/lib/native', () => ({ openExternal: vi.fn() }))

import { NoteBody, NotePreview } from './NoteBody'

afterEach(cleanup)

describe('笔记正文', () => {
  it('Markdown：加粗、列表、行内代码、表格', () => {
    const { container } = render(<NoteBody text={'**重点** 和 `code`\n\n- 一\n- 二\n\n| a | b |\n|---|---|\n| 1 | 2 |'} />)
    expect(container.querySelector('strong')?.textContent).toBe('重点')
    expect(container.querySelector('code')?.textContent).toBe('code')
    expect(container.querySelectorAll('li')).toHaveLength(2)
    expect(container.querySelector('table td')?.textContent).toBe('1')
  })

  it('LaTeX：行内 $…$ 和独立成行 $$…$$ 都用 KaTeX 渲染', () => {
    const { container } = render(<NoteBody text={'能量 $E=mc^2$ 。\n\n$$\\sum_{i=1}^n x_i$$'} />)
    expect(container.querySelectorAll('.katex').length).toBeGreaterThanOrEqual(2)
    expect(container.querySelector('.katex-display')).not.toBeNull()
  })

  it('公式写错不会让笔记崩掉，原样显示', () => {
    const { container } = render(<NoteBody text={'坏公式 $\\frac{1}{$ 之后还有文字'} />)
    expect(container.textContent).toContain('之后还有文字')
  })

  it('纯文字笔记原样显示，换行不丢', () => {
    render(<NoteBody text={'第一行\n\n第二段'} />)
    expect(screen.getByText('第一行')).toBeTruthy()
    expect(screen.getByText('第二段')).toBeTruthy()
  })
})

describe('写笔记时的预览', () => {
  it('没写内容时不显示；点“预览”才渲染，再点收起', () => {
    const { rerender, container } = render(<NotePreview text="" />)
    expect(container.textContent).toBe('')
    rerender(<NotePreview text={'**粗**'} />)
    expect(container.querySelector('strong')).toBeNull()
    fireEvent.click(screen.getByText('预览'))
    expect(container.querySelector('strong')?.textContent).toBe('粗')
    fireEvent.click(screen.getByText('收起预览'))
    expect(container.querySelector('strong')).toBeNull()
  })
})
