import { describe, expect, it } from 'vitest'
import type { CitationMarker, RefItem, RefResolution } from '@/lib/types.references'
import { bibtex, expandNumbers, matchLabel, placePopover, plainCitation, refHeadline, splitByMarkers, splitCjkSup, splitLooseBrackets, splitSupDigits } from './refModel'

const ref = (over: Partial<RefItem> = {}): RefItem => ({
  index: 1, ord: 5, page: 7, ords: [5], raw: '1. Rivera, A. & Chen, L. Cascade failure in layered networks. Phys. Rev. E 80, 41 (2009).',
  authors: ['Rivera, A.', 'Chen, L.'], title: 'Cascade failure in layered networks', year: 2009, venue: 'Phys. Rev. E 80, 41', doi: '', arxiv: '', url: '',
  merged: false, messy: false, ...over
})

describe('expandNumbers', () => {
  it('expands lists and ranges, drops duplicates and absurd ranges', () => {
    expect(expandNumbers('1,2')).toEqual([1, 2])
    expect(expandNumbers('3, 5-7')).toEqual([3, 5, 6, 7])
    expect(expandNumbers('14–18')).toEqual([14, 15, 16, 17, 18])
    expect(expandNumbers('1,1,2')).toEqual([1, 2])
    expect(expandNumbers('1–200')).toEqual([])
    expect(expandNumbers('9–3')).toEqual([])
  })
})

describe('splitSupDigits', () => {
  const valid = new Set([1, 2, 3, 8, 14, 15, 16])
  it('makes each comma-separated number clickable and keeps ranges whole', () => {
    expect(splitSupDigits('1,2', valid)).toEqual([{ text: '1', refs: [1] }, { text: ',' }, { text: '2', refs: [2] }])
    expect(splitSupDigits('3,8,14–16', valid)).toEqual([
      { text: '3', refs: [3] }, { text: ',' }, { text: '8', refs: [8] }, { text: ',' }, { text: '14–16', refs: [14, 15, 16] }
    ])
  })
  it('leaves numbers the backend did not confirm as plain text', () => {
    expect(splitSupDigits('2,99', valid)).toEqual([{ text: '2', refs: [2] }, { text: ',' }, { text: '99' }])
    expect(splitSupDigits('50', valid)).toEqual([{ text: '50' }])
  })
})

describe('splitByMarkers', () => {
  const mk = (text: string, refs: number[]): CitationMarker => ({ kind: 'bracket', text, start: 0, end: 0, refs })
  it('splits text around markers in order', () => {
    expect(splitByMarkers('As shown [3] and later [4–5], yes.', [mk('[3]', [3]), mk('[4–5]', [4, 5])])).toEqual([
      { text: 'As shown ' }, { text: '[3]', refs: [3] }, { text: ' and later ' }, { text: '[4–5]', refs: [4, 5] }, { text: ', yes.' }
    ])
  })
  it('ignores markers that cannot be found in the display text and returns plain text when nothing matches', () => {
    expect(splitByMarkers('No citation here.', [mk('[3]', [3])])).toEqual([{ text: 'No citation here.' }])
    expect(splitByMarkers('plain', [])).toEqual([{ text: 'plain' }])
  })
  it('finds repeated markers one after another', () => {
    const out = splitByMarkers('[1] and [1] again', [mk('[1]', [1]), mk('[1]', [1])])
    expect(out.filter((p) => p.refs)).toHaveLength(2)
  })
})

describe('citation formats', () => {
  const found: RefResolution = { index: 1, found: true, title: 'Cascade Failure in Layered Networks', authors: ['Alex Rivera', 'Li Chen'], year: 2009, venue: 'Physical Review E', doi: '10.1103/x' }
  it('prefers the looked-up record, falls back to parsed fields, then to the raw text', () => {
    expect(plainCitation(ref(), found)).toContain('Cascade Failure in Layered Networks')
    expect(plainCitation(ref(), found)).toContain('https://doi.org/10.1103/x')
    expect(plainCitation(ref(), null)).toContain('Rivera, A., Chen, L. (2009) Cascade failure')
    expect(plainCitation(ref({ title: '', authors: [] }), null)).toBe(ref().raw)
  })
  it('writes bibtex with only the fields that exist and escapes braces', () => {
    const b = bibtex(ref(), found)
    expect(b).toMatch(/^@article\{Rivera2009cascade,/)
    expect(b).toContain('author = {Alex Rivera and Li Chen}')
    expect(b).toContain('doi = {10.1103/x}')
    const plain = bibtex(ref({ title: 'A {braced} title', venue: '', year: null, authors: [] }), null)
    expect(plain).toContain('title = {A braced title}')
    expect(plain).not.toContain('journal')
    expect(plain).not.toContain('year')
  })
})

describe('placePopover', () => {
  const vp = { w: 1000, h: 700 }
  it('puts the card below the marker when there is room, above otherwise, and clamps horizontally', () => {
    expect(placePopover({ left: 100, right: 110, top: 100, bottom: 114 }, 360, vp)).toMatchObject({ top: 120 })
    const above = placePopover({ left: 100, right: 110, top: 600, bottom: 614 }, 360, vp)
    expect(above.top).toBeUndefined()
    expect(above.bottom).toBe(106)
    expect(placePopover({ left: 5, right: 15, top: 100, bottom: 114 }, 360, vp).left).toBe(8)
    expect(placePopover({ left: 990, right: 998, top: 100, bottom: 114 }, 360, vp).left).toBe(632)
  })
})

describe('labels', () => {
  it('describes match levels and headlines', () => {
    expect(matchLabel('low')).toContain('可能不是同一篇')
    expect(matchLabel('high')).toBe('匹配可信度高')
    expect(refHeadline(ref())).toBe('Cascade failure in layered networks')
    expect(refHeadline(ref({ title: '' }))).toMatch(/^Rivera, A\./)
  })
})

describe('splitLooseBrackets（译文里被改写的方括号引用）', () => {
  const valid = new Set([64, 65, 66, 70, 71])
  it('破折号、全角括号、方头括号都认；只认确实存在的编号', () => {
    const r = splitLooseBrackets('具有[64-66]卓越，［70］与【71】，[99]不存在', valid)
    expect(r.filter((p) => p.refs).map((p) => [p.text, p.refs])).toEqual([['[64-66]', [64, 65, 66]], ['［70］', [70]], ['【71】', [71]]])
    expect(r.map((p) => p.text).join('')).toBe('具有[64-66]卓越，［70］与【71】，[99]不存在')
  })
  it('原样的写法和中文逗号分隔也认', () => {
    expect(splitLooseBrackets('[64–66]具有', valid)[0]).toEqual({ text: '[64–66]', refs: [64, 65, 66] })
    expect(splitLooseBrackets('见[64，70]', valid).find((p) => p.refs)?.refs).toEqual([64, 70])
  })
  it('没有可认的就原样返回', () => {
    expect(splitLooseBrackets('没有引用 [Fig. 3]', valid)).toEqual([{ text: '没有引用 [Fig. 3]' }])
  })
})

describe('splitByMarkers 用前文定位短标记（PDF 链接识别的标记）', () => {
  it('“64”在前面的句子里也出现过：靠前文落在括号里的那一处', () => {
    const text = 'We use 64 layers [64, 66] in total.'
    const r = splitByMarkers(text, [{ kind: 'link', text: '64', start: 12, end: 14, refs: [64], pre: 'layers [' }])
    expect(r.filter((p) => p.refs).map((p) => p.text)).toEqual(['64'])
    expect(r.map((p) => p.text).join('')).toBe(text)
    expect(r[0].text).toBe('We use 64 layers [') // 不会把前面那个“64 layers”当成引用
  })
  it('没有前文时和以前一样按顺序找', () => {
    const r = splitByMarkers('see [3] and [4]', [{ kind: 'bracket', text: '[3]', start: 4, end: 7, refs: [3] }, { kind: 'bracket', text: '[4]', start: 12, end: 15, refs: [4] }])
    expect(r.filter((p) => p.refs).map((p) => p.refs)).toEqual([[3], [4]])
  })
})

describe('译文里的引用角标', () => {
  const valid = new Set([1, 2, 3, 4, 5, 6, 7, 9])
  it('贴在汉字或全角标点后面、编号确实存在的数字，认成引用', () => {
    const p = splitCjkSup('维持基本功能的能力1–3。韧性的丧失3–5。理解其整体系统韧性至关重要6。', valid)
    expect(p.filter((x) => x.refs).map((x) => [x.text, x.refs])).toEqual([
      ['1–3', [1, 2, 3]],
      ['3–5', [3, 4, 5]],
      ['6', [6]]
    ])
  })
  it('数字后直接接英文破折号时仍保持为引用上标', () => {
    expect(splitCjkSup('主要关注网络的拓扑结构的方法4,9——而非动力学。', valid)).toEqual([
      { text: '主要关注网络的拓扑结构的方法' },
      { text: '4,9', refs: [4, 9] },
      { text: '——而非动力学。' }
    ])
  })
  it('数字后接中文全角括号时仍保持为引用上标', () => {
    expect(splitCjkSup('包括度中心性3（DC：d_i）、集体影响力7（CI）。', valid).filter((x) => x.refs).map((x) => [x.text, x.refs])).toEqual([
      ['3', [3]],
      ['7', [7]]
    ])
  })
  it('“图 2”“第 3 节”“提高了 5 倍”“共 3 个”和不存在的编号都不是引用', () => {
    expect(splitCjkSup('如图2，结果见第3节，提高了5倍，共3个。', valid).some((x) => x.refs)).toBe(false)
    expect(splitCjkSup('这是一个结论99。', valid).some((x) => x.refs)).toBe(false)
  })
})
