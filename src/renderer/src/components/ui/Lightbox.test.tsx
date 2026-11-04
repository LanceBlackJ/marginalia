// @vitest-environment jsdom
import { afterEach, describe, expect, it, vi } from 'vitest'
import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import { LB_MAX, LB_MIN, Lightbox, clampScale, lockScroll, unlockScroll, zoomAround } from './Lightbox'

afterEach(() => {
  cleanup()
  document.documentElement.removeAttribute('style')
})

describe('Lightbox 组件', () => {
  it('挂在 document.body 上，不在触发它的容器里，所以不受滚动容器和 content-visibility 影响', () => {
    const { container } = render(
      <div className="pd-tchunk" style={{ contentVisibility: 'auto' }}>
        <Lightbox open onClose={() => undefined} src="blob:x" alt="Fig. 5" />
      </div>
    )
    const dlg = screen.getByRole('dialog')
    expect(container.contains(dlg)).toBe(false)
    expect(dlg.parentElement).toBe(document.body)
    expect(dlg.className).toContain('pd-lb')
    expect(screen.getByRole('img', { name: 'Fig. 5' })).toBeTruthy()
  })

  it('没有 src 时显示加载骨架，不显示图', () => {
    render(<Lightbox open onClose={() => undefined} src={null} alt="图" />)
    expect(screen.queryByRole('img')).toBeNull()
    expect(document.querySelector('.pd-lb-wait')).toBeTruthy()
  })

  it('打开时锁定背景滚动，关闭后恢复原来的样式', () => {
    document.documentElement.style.overflow = 'scroll'
    const { rerender } = render(<Lightbox open onClose={() => undefined} src="blob:x" alt="图" />)
    expect(document.documentElement.style.overflow).toBe('hidden')
    rerender(<Lightbox open={false} onClose={() => undefined} src="blob:x" alt="图" />)
    expect(document.documentElement.style.overflow).toBe('scroll')
  })

  it('Esc、点空白处、点关闭按钮都会关闭，点图本身不关闭', () => {
    const onClose = vi.fn()
    render(<Lightbox open onClose={onClose} src="blob:x" alt="图" />)
    fireEvent.click(screen.getByRole('img'))
    expect(onClose).not.toHaveBeenCalled()
    fireEvent.click(document.querySelector('.pd-lb-stage') as HTMLElement)
    expect(onClose).toHaveBeenCalledTimes(1)
    fireEvent.click(screen.getByRole('button', { name: '关闭' }))
    expect(onClose).toHaveBeenCalledTimes(2)
    fireEvent.keyDown(document, { key: 'Escape' })
    expect(onClose).toHaveBeenCalledTimes(3)
  })

  it('打开后焦点进入关闭按钮，Tab 在控件之间循环，关闭后焦点回到原来的按钮', () => {
    const opener = document.createElement('button')
    document.body.appendChild(opener)
    opener.focus()
    const { rerender } = render(<Lightbox open onClose={() => undefined} src="blob:x" alt="图" />)
    expect(document.activeElement).toBe(screen.getByRole('button', { name: '关闭' }))
    const last = screen.getByRole('button', { name: '适应窗口' })
    // 适应窗口在没有缩放时是禁用的；把焦点放到最后一个可聚焦控件“放大”上再按 Tab
    const plus = screen.getByRole('button', { name: '放大' })
    plus.focus()
    fireEvent.keyDown(document, { key: 'Tab' })
    expect(document.activeElement).toBe(screen.getByRole('button', { name: '关闭' }))
    expect(last.hasAttribute('disabled')).toBe(true)
    rerender(<Lightbox open={false} onClose={() => undefined} src="blob:x" alt="图" />)
    expect(document.activeElement).toBe(opener)
    opener.remove()
  })

  it('按钮缩放会更新百分比，并限制在上下界内', () => {
    render(<Lightbox open onClose={() => undefined} src="blob:x" alt="图" />)
    expect(screen.getByText('100%')).toBeTruthy()
    fireEvent.click(screen.getByRole('button', { name: '放大' }))
    expect(screen.getByText('125%')).toBeTruthy()
    fireEvent.click(screen.getByRole('button', { name: '适应窗口' }))
    expect(screen.getByText('100%')).toBeTruthy()
    expect((screen.getByRole('button', { name: '缩小' }) as HTMLButtonElement).disabled).toBe(true)
  })

  it('清晰版还在加载时提示', () => {
    render(<Lightbox open onClose={() => undefined} src="blob:small" alt="图" loadingLarge />)
    expect(screen.getByText('正在加载清晰版本…')).toBeTruthy()
  })
})

describe('Lightbox 纯函数', () => {
  it('缩放倍数限制在上下界内', () => {
    expect(clampScale(0.3)).toBe(LB_MIN)
    expect(clampScale(50)).toBe(LB_MAX)
    expect(clampScale(2)).toBe(2)
  })

  it('以光标为中心缩放：光标下的那一点保持不动，缩回 1 倍时平移归零', () => {
    // 图以舞台中心为原点；光标在中心右侧 100px，从 1 倍放大到 2 倍
    const t = zoomAround({ x: 0, y: 0 }, 1, 2, { x: 100, y: 0 })
    expect(t.x).toBe(-100)
    // 放大后图上原先在光标下的点：偏移 100 → 100*2 + (-100) = 100，仍在光标处
    expect(100 * 2 + t.x).toBe(100)
    expect(zoomAround({ x: 40, y: 10 }, 2, 1, { x: 30, y: 30 })).toEqual({ x: 0, y: 0 })
  })

  it('滚动锁可嵌套：最后一个解锁才恢复', () => {
    document.documentElement.style.overflow = 'auto'
    lockScroll()
    lockScroll()
    expect(document.documentElement.style.overflow).toBe('hidden')
    unlockScroll()
    expect(document.documentElement.style.overflow).toBe('hidden')
    unlockScroll()
    expect(document.documentElement.style.overflow).toBe('auto')
  })
})
