// @vitest-environment jsdom
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { Lightbox, VECTOR_FIT, VECTOR_FIT_H, VECTOR_MIN } from './Lightbox'

const STAGE_W = 1000
const STAGE_H = 600

beforeEach(() => {
  // jsdom 没有布局：给舞台一个固定的尺寸；blob 的类型决定是不是矢量图
  vi.spyOn(HTMLElement.prototype, 'clientWidth', 'get').mockReturnValue(STAGE_W)
  vi.spyOn(HTMLElement.prototype, 'clientHeight', 'get').mockReturnValue(STAGE_H)
})
afterEach(() => {
  cleanup()
  vi.restoreAllMocks()
  vi.unstubAllGlobals()
  document.documentElement.removeAttribute('style')
})

function openWith(type: string, natural: { w: number; h: number }) {
  vi.stubGlobal('fetch', vi.fn(async () => ({ blob: async () => new Blob(['x'], { type }) })))
  render(<Lightbox open onClose={() => undefined} src="blob:fig" alt="图" />)
  const img = screen.getByRole('img', { name: '图' }) as HTMLImageElement
  Object.defineProperty(img, 'naturalWidth', { value: natural.w, configurable: true })
  Object.defineProperty(img, 'naturalHeight', { value: natural.h, configurable: true })
  fireEvent.load(img)
  return img
}

describe('矢量图的查看', () => {
  it('默认宽度是页面水平宽度的 0.99，高度按比例', async () => {
    openWith('image/svg+xml', { w: 400, h: 200 })
    await waitFor(() => expect((screen.getByRole('img', { name: '图' }) as HTMLImageElement).style.width).toBe(`${STAGE_W * VECTOR_FIT}px`))
    const img = screen.getByRole('img', { name: '图' }) as HTMLImageElement
    expect(VECTOR_FIT).toBe(0.99)
    expect(img.style.height).toBe(`${(STAGE_W * VECTOR_FIT) / 2}px`)
  })

  it('放大是改宽高（浏览器按新尺寸重新绘制矢量），不是 CSS 的 scale（那样会糊）', async () => {
    openWith('image/svg+xml', { w: 400, h: 200 })
    await waitFor(() => expect((screen.getByRole('img', { name: '图' }) as HTMLImageElement).style.width).toBe('990px'))
    fireEvent.click(screen.getByRole('button', { name: '放大' }))
    const img = screen.getByRole('img', { name: '图' }) as HTMLImageElement
    await waitFor(() => expect(parseFloat(img.style.width)).toBeCloseTo(990 * 1.25, 0))
    expect(img.style.transform).not.toContain('scale(')
    expect(img.className).toContain('pd-lb-img--vec')
  })

  it('图比窗口高时，默认按高度缩到整张图都在窗口里（不充满屏幕、不被截断）', async () => {
    openWith('image/svg+xml', { w: 400, h: 600 }) // 按宽度会是 990×1485，窗口高 600
    const h = STAGE_H * VECTOR_FIT_H
    await waitFor(() => expect(parseFloat((screen.getByRole('img', { name: '图' }) as HTMLImageElement).style.height)).toBeCloseTo(h, 1))
    const img = screen.getByRole('img', { name: '图' }) as HTMLImageElement
    expect(parseFloat(img.style.width)).toBeCloseTo((h * 400) / 600, 1)
    expect(parseFloat(img.style.width)).toBeLessThanOrEqual(STAGE_W * VECTOR_FIT)
    expect(img.style.transform).toContain('+ 0px')
  })

  it('滚轮可以缩小到比默认更小，也能放大；缩小有下限', async () => {
    openWith('image/svg+xml', { w: 400, h: 200 })
    await waitFor(() => expect((screen.getByRole('img', { name: '图' }) as HTMLImageElement).style.width).toBe('990px'))
    const stage = screen.getByRole('dialog').querySelector('.pd-lb-stage') as HTMLElement
    const width = (): number => parseFloat((screen.getByRole('img', { name: '图' }) as HTMLImageElement).style.width)
    fireEvent.wheel(stage, { deltaY: 100, clientX: 500, clientY: 300 })
    await waitFor(() => expect(width()).toBeLessThan(990))
    for (let i = 0; i < 40; i++) {
      fireEvent.wheel(stage, { deltaY: 100, clientX: 500, clientY: 300 })
      await Promise.resolve()
    }
    await waitFor(() => expect(width()).toBeCloseTo(990 * VECTOR_MIN, 0))
    fireEvent.wheel(stage, { deltaY: -100, clientX: 500, clientY: 300 })
    await waitFor(() => expect(width()).toBeGreaterThan(990 * VECTOR_MIN))
  })

  it('位图保持原来的显示方式（不改成矢量的宽高）', async () => {
    const img = openWith('image/png', { w: 400, h: 200 })
    await Promise.resolve()
    expect(img.className).toBe('pd-lb-img')
    fireEvent.click(screen.getByRole('button', { name: '放大' }))
    expect(img.style.transform).toContain('scale(')
  })
})
