import { describe, expect, it } from 'vitest'
import { unionRects } from './geometry'

describe('unionRects', () => {
  it('没有矩形时返回 null', () => {
    expect(unionRects([])).toBeNull()
  })
  it('取外接矩形', () => {
    expect(unionRects([[10, 20, 50, 30], [5, 32, 40, 44], [12, 18, 60, 28]])).toEqual([5, 18, 60, 44])
  })
})
