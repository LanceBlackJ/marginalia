// @vitest-environment jsdom
import { afterEach, describe, expect, it } from 'vitest'
import { cleanup, fireEvent, render } from '@testing-library/react'
import { Clamp } from './Clamp'

afterEach(cleanup)

function withHeights(scroll: number, client: number, run: () => void): void {
  const sh = Object.getOwnPropertyDescriptor(HTMLElement.prototype, 'scrollHeight')
  const ch = Object.getOwnPropertyDescriptor(HTMLElement.prototype, 'clientHeight')
  Object.defineProperty(HTMLElement.prototype, 'scrollHeight', { configurable: true, get: () => scroll })
  Object.defineProperty(HTMLElement.prototype, 'clientHeight', { configurable: true, get: () => client })
  try {
    run()
  } finally {
    if (sh) Object.defineProperty(HTMLElement.prototype, 'scrollHeight', sh)
    if (ch) Object.defineProperty(HTMLElement.prototype, 'clientHeight', ch)
  }
}

describe('笔记折叠', () => {
  it('内容不长：原样显示，没有展开按钮', () => {
    withHeights(40, 40, () => {
      const { container } = render(<Clamp lines={6}>短笔记</Clamp>)
      expect(container.querySelector('.pd-clamp-toggle')).toBeNull()
      expect(container.querySelector('.pd-clamp')!.classList.contains('is-fade')).toBe(false)
    })
  })

  it('内容太长：先折起来（渐隐），点“展开”看全文，再点“收起”', () => {
    withHeights(400, 100, () => {
      const { container, getByText } = render(<Clamp lines={6}>很长的笔记</Clamp>)
      const box = container.querySelector('.pd-clamp')!
      expect(box.classList.contains('is-clamped') && box.classList.contains('is-fade')).toBe(true)
      fireEvent.click(getByText('展开'))
      expect(box.classList.contains('is-clamped')).toBe(false)
      fireEvent.click(getByText('收起'))
      expect(box.classList.contains('is-clamped')).toBe(true)
    })
  })
})
