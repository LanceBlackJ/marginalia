// @vitest-environment jsdom
import { afterEach, describe, expect, it } from 'vitest'
import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import { Pager, usePaging } from './Pager'

function Demo({ items, resetKey }: { items: number[]; resetKey: string }) {
  const p = usePaging(items, resetKey, 20)
  return (
    <div>
      <ul>
        {p.shown.map((n) => (
          <li key={n}>{n}</li>
        ))}
      </ul>
      <Pager page={p.page} pages={p.pages} total={items.length} onPage={p.setPage} />
    </div>
  )
}

afterEach(cleanup)

describe('结果分页', () => {
  const items = Array.from({ length: 45 }, (_, i) => i + 1)

  it('每页 20 条，翻页、页码和总数都对', () => {
    const { container } = render(<Demo items={items} resetKey="a" />)
    expect(container.querySelectorAll('li').length).toBe(20)
    expect(screen.getByText('第 1 / 3 页 · 共 45 条')).toBeTruthy()
    fireEvent.click(screen.getByLabelText('下一页'))
    fireEvent.click(screen.getByLabelText('下一页'))
    expect(container.querySelectorAll('li').length).toBe(5)
    expect((screen.getByLabelText('下一页') as HTMLButtonElement).disabled).toBe(true)
  })

  it('换了一批结果回到第一页；不足一页时不显示分页', () => {
    const { rerender, container } = render(<Demo items={items} resetKey="a" />)
    fireEvent.click(screen.getByLabelText('下一页'))
    rerender(<Demo items={items} resetKey="b" />)
    expect(screen.getByText('第 1 / 3 页 · 共 45 条')).toBeTruthy()
    rerender(<Demo items={items.slice(0, 10)} resetKey="c" />)
    expect(container.querySelector('nav')).toBeNull()
  })
})
