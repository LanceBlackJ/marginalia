import { describe, expect, it } from 'vitest'
import { placeSelectionToolbar } from './selectionToolbarLayout'

const view = { width: 1440, height: 900 }
const tool = { w: 360, h: 36 }

describe('划词工具条定位', () => {
  it('优先放在选区上方，水平居中于选区，且不压到选区', () => {
    const r = { left: 600, top: 400, right: 800, bottom: 420 }
    const p = placeSelectionToolbar(r, tool, view)
    expect(p.place).toBe('above')
    expect(p.top + tool.h).toBeLessThanOrEqual(r.top) // 工具条底边在选区上边之上
    expect(p.left).toBeCloseTo(700 - tool.w / 2, 5)
  })

  it('上方放不下（选区在窗口顶部）时放到下方', () => {
    const r = { left: 600, top: 70, right: 800, bottom: 90 }
    const p = placeSelectionToolbar(r, tool, view)
    expect(p.place).toBe('below')
    expect(p.top).toBeGreaterThanOrEqual(r.bottom)
  })

  it('靠左右边缘时按自身宽度夹在视口内', () => {
    const left = placeSelectionToolbar({ left: 4, top: 400, right: 40, bottom: 420 }, tool, view)
    expect(left.left).toBeGreaterThanOrEqual(8)
    const right = placeSelectionToolbar({ left: 1400, top: 400, right: 1436, bottom: 420 }, tool, view)
    expect(right.left + tool.w).toBeLessThanOrEqual(view.width - 8)
  })

  it('选区占满整个视口高度：贴在可见部分内，不跑出窗口', () => {
    const p = placeSelectionToolbar({ left: 600, top: 0, right: 800, bottom: 920 }, tool, view)
    expect(p.place).toBe('inside')
    expect(p.top).toBeGreaterThanOrEqual(56)
    expect(p.top + tool.h).toBeLessThanOrEqual(view.height)
  })
})
