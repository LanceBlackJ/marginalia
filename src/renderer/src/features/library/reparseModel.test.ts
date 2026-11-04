import { describe, expect, it } from 'vitest'
import { canReparse, describeReparse, isSuspiciousResult, needsReparse, noteIsStale, outdatedCount } from './reparseModel'

const paper = (over: Record<string, unknown> = {}) => ({ has_pdf: true, status: 'ready' as const, parser_outdated: true, role: 'paper' as const, ...over })

describe('needsReparse', () => {
  it('提示有新版解析：有 PDF 的、已过期的正文论文', () => {
    expect(needsReparse(paper())).toBe(true)
  })
  it('已经是新版解析、没有 PDF、补充材料、解析中都不提示', () => {
    expect(needsReparse(paper({ parser_outdated: false }))).toBe(false)
    expect(needsReparse(paper({ parser_outdated: undefined }))).toBe(false)
    expect(needsReparse(paper({ has_pdf: false, status: 'metadata_only' }))).toBe(false)
    expect(needsReparse(paper({ role: 'supplement' }))).toBe(false)
    expect(needsReparse(paper({ status: 'parsing' }))).toBe(false)
  })
  it('canReparse 不要求已过期，但仍要求有 PDF、不是补充材料', () => {
    expect(canReparse(paper({ parser_outdated: false }))).toBe(true)
    expect(canReparse(paper({ has_pdf: false }))).toBe(false)
    expect(canReparse(paper({ role: 'supplement' }))).toBe(false)
  })
  it('outdatedCount 只数需要提示的', () => {
    expect(outdatedCount([paper(), paper({ parser_outdated: false }), paper({ has_pdf: false }), paper()])).toBe(2)
  })
})

describe('describeReparse', () => {
  it('说明段落变化与笔记重新对应；失效的明确说出来', () => {
    const s = describeReparse({ passages: { old: 163, new: 160, mapped: 150, unmapped: 13 }, notes: { total: 4, mapped: 3, stale: 1 } })
    expect(s).toContain('段落 163 → 160')
    expect(s).toContain('3/4 条笔记已对应到新位置')
    expect(s).toContain('1 条的位置已失效')
    expect(s).toContain('原文引用和页码已保留')
  })
  it('没有笔记时不提笔记；没有统计时给一句通用的话', () => {
    expect(describeReparse({ passages: { old: 10, new: 12, mapped: 10, unmapped: 0 }, notes: { total: 0, mapped: 0, stale: 0 } })).toBe('段落 10 → 12。')
    expect(describeReparse(undefined)).toBe('已用新版解析器更新。')
  })
})

describe('其它判断', () => {
  it('后端因新解析段落数少得可疑而中止时，识别出来以便提供“仍然替换”', () => {
    expect(isSuspiciousResult('新的解析只得到 3 个段落（原来 40 个），差别太大，原有数据保持不变。')).toBe(true)
    expect(isSuspiciousResult('模型服务暂时不可用')).toBe(false)
    expect(isSuspiciousResult(null)).toBe(false)
  })
  it('笔记位置失效：标了 stale 且没有段落序号', () => {
    expect(noteIsStale({ stale: true, passage_ord: null, quote: 'x' })).toBe(true)
    expect(noteIsStale({ stale: true, passage_ord: 5 })).toBe(false)
    expect(noteIsStale({ stale: false, passage_ord: null })).toBe(false)
    expect(noteIsStale({ passage_ord: null })).toBe(false)
  })
})
