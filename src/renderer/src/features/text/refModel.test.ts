import { describe, expect, it } from 'vitest'
import type { CitationMarker, RefItem, RefResolution } from '@/lib/types.references'
import { bibtex, expandNumbers, matchLabel, placePopover, plainCitation, refHeadline, splitByMarkers, splitSupDigits } from './refModel'

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
