import { describe, expect, it } from 'vitest'
import { estimateMinutes } from './ReadProgress'
import { neighbourSection } from './useReaderShortcuts'
import { passageAtPoint } from '@/features/text/textModel'
import type { TextItem } from '@/lib/api.text'
import { DEFAULTS, MEASURES, sanitizePrefs } from '@/stores/prefsStore'

const item = (ord: number, page: number, bbox: [number, number, number, number]): TextItem =>
  ({ ord, page, kind: 'paragraph', section_title: null, text: 'x'.repeat(30), bbox }) as TextItem

describe('passageAtPoint（PDF 上的点 → 段落）', () => {
  const items = [item(1, 1, [50, 50, 300, 100]), item(2, 1, [50, 120, 300, 200]), item(3, 2, [50, 50, 300, 100])]
  it('点落在段落框里就命中它', () => {
    expect(passageAtPoint(items, 1, 100, 150)?.ord).toBe(2)
  })
  it('只在同一页里找；点在段落之间时取最近的一段', () => {
    expect(passageAtPoint(items, 1, 100, 110)?.ord).toBe(1)
    expect(passageAtPoint(items, 2, 400, 400)?.ord).toBe(3)
  })
  it('这一页没有任何段落时返回 null', () => {
    expect(passageAtPoint(items, 9, 10, 10)).toBeNull()
  })
})

describe('neighbourSection（[ ] 切章节）', () => {
  const outline = [
    { title: 'Abstract', page: 1 },
    { title: 'Model', page: 2 },
    { title: 'Results', page: 4 }
  ]
  it('按当前章节取上一节/下一节，且不越界', () => {
    expect(neighbourSection(outline, 2, 'Model', 1)?.title).toBe('Results')
    expect(neighbourSection(outline, 2, 'Model', -1)?.title).toBe('Abstract')
    expect(neighbourSection(outline, 4, 'Results', 1)?.title).toBe('Results')
    expect(neighbourSection(outline, 1, 'Abstract', -1)?.title).toBe('Abstract')
  })
  it('没有当前章节时按页码推断；没有目录返回 null', () => {
    expect(neighbourSection(outline, 3, null, 1)?.title).toBe('Results')
    expect(neighbourSection([], 1, null, 1)).toBeNull()
  })
})

describe('estimateMinutes（预计剩余阅读时间）', () => {
  it('英文约 1100 字符/分钟，中文约 450；读完为 0', () => {
    expect(estimateMinutes(11000, 0, 0)).toBe(10)
    expect(estimateMinutes(4500, 0, 0.9)).toBe(10)
    expect(estimateMinutes(11000, 1, 0)).toBe(0)
    expect(estimateMinutes(11000, 0.5, 0)).toBe(5)
  })
})

describe('sanitizePrefs（本地存储里读出的外观偏好）', () => {
  it('缺的补默认值，未知枚举回到默认', () => {
    const p = sanitizePrefs({ theme: 'neon' as never, measure: 'huge' as never })
    expect(p.theme).toBe(DEFAULTS.theme)
    expect(p.measure).toBe(DEFAULTS.measure)
    expect(sanitizePrefs(null).readSize).toBe(DEFAULTS.readSize)
  })
  it('越界的数值夹回范围', () => {
    const p = sanitizePrefs({ readSize: 99, readLineHeight: 0.5, splitRatio: 0.99 })
    expect(p.readSize).toBe(26)
    expect(p.readLineHeight).toBe(1.4)
    expect(p.splitRatio).toBe(0.7)
  })
  it('每种行宽都有对应的 CSS 宽度', () => {
    expect(Object.keys(MEASURES).sort()).toEqual(['full', 'medium', 'narrow', 'wide'])
  })
})
