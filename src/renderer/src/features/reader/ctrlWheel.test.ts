// @vitest-environment jsdom
import { describe, expect, it } from 'vitest'
import { onCtrlWheel } from './ctrlWheel'
import { DEFAULTS, PANEL_SCALE_MAX, PANEL_SCALE_MIN, sanitizePrefs } from '@/stores/prefsStore'

const wheel = (el: HTMLElement, deltaY: number, ctrlKey = true): WheelEvent => {
  const e = new WheelEvent('wheel', { deltaY, ctrlKey, cancelable: true, bubbles: true })
  el.dispatchEvent(e)
  return e
}

describe('Ctrl+鼠标滚轮调大小', () => {
  it('向上滚放大、向下滚缩小，并阻止浏览器整页缩放', () => {
    const el = document.createElement('div')
    const steps: number[] = []
    onCtrlWheel(el, (d) => steps.push(d))
    expect(wheel(el, -100).defaultPrevented).toBe(true)
    expect(wheel(el, 100).defaultPrevented).toBe(true)
    expect(steps).toEqual([1, -1])
  })

  it('不按 Ctrl 的普通滚动不拦、不调；触控板的小幅滚动累计够一格才调一档', () => {
    const el = document.createElement('div')
    const steps: number[] = []
    onCtrlWheel(el, (d) => steps.push(d))
    expect(wheel(el, 100, false).defaultPrevented).toBe(false)
    expect(steps).toEqual([])
    for (let i = 0; i < 3; i++) wheel(el, -10) // 累计 30 < 40
    expect(steps).toEqual([])
    wheel(el, -10) // 累计 40
    expect(steps).toEqual([1])
  })

  it('取消监听后不再响应', () => {
    const el = document.createElement('div')
    const steps: number[] = []
    const off = onCtrlWheel(el, (d) => steps.push(d))
    off()
    wheel(el, -100)
    expect(steps).toEqual([])
  })
})

describe('面板缩放偏好', () => {
  it('默认 1；存进去的越界值夹回 80%–160%', () => {
    expect(DEFAULTS.panelScale).toBe(1)
    expect(sanitizePrefs({ panelScale: 9 }).panelScale).toBe(PANEL_SCALE_MAX)
    expect(sanitizePrefs({ panelScale: 0.1 }).panelScale).toBe(PANEL_SCALE_MIN)
    expect(sanitizePrefs({ panelScale: 1.2 }).panelScale).toBe(1.2)
    expect(sanitizePrefs(null).panelScale).toBe(1)
  })
})
