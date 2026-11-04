// @vitest-environment jsdom
import { describe, expect, it } from 'vitest'
import { cjkRatio, placeBar, placePanel, selectionContainer, shouldOfferTranslate, translatableContainer } from './selectTranslateModel'

describe('shouldOfferTranslate', () => {
  it('offers English, refuses Chinese-majority, too short and too long text', () => {
    expect(shouldOfferTranslate('Failures spread across layered networks.')).toBe(true)
    expect(shouldOfferTranslate('这是一段中文，没有英文单词。')).toBe(false)
    expect(shouldOfferTranslate('这段话里夹着 a few words 但主要是中文内容')).toBe(false)
    expect(shouldOfferTranslate('ab')).toBe(false)
    expect(shouldOfferTranslate('12345 67890')).toBe(false)
    expect(shouldOfferTranslate('word '.repeat(2000))).toBe(false)
  })
  it('cjkRatio ignores digits and punctuation', () => {
    expect(cjkRatio('1234 ...')).toBe(0)
    expect(cjkRatio('中文 ab')).toBeCloseTo(2 / 4)
  })
})

describe('selection container', () => {
  it('finds the data-translatable ancestor and requires one container for both ends', () => {
    document.body.innerHTML = '<p data-translatable id="a">Hello <b id="b">world</b> text</p><p id="c">plain text</p>'
    const b = document.getElementById('b')!.firstChild!
    expect(translatableContainer(b)?.id).toBe('a')
    expect(translatableContainer(document.getElementById('c')!.firstChild)).toBeNull()
    const sel = window.getSelection()!
    sel.removeAllRanges()
    const r = document.createRange()
    r.setStart(document.getElementById('a')!.firstChild!, 0)
    r.setEnd(b, 3)
    sel.addRange(r)
    expect(selectionContainer(sel)?.id).toBe('a')
    r.setEnd(document.getElementById('c')!.firstChild!, 3)
    sel.removeAllRanges()
    sel.addRange(r)
    expect(selectionContainer(sel)).toBeNull()
    sel.removeAllRanges()
    expect(selectionContainer(sel)).toBeNull()
  })
})

describe('placement', () => {
  const vp = { w: 1000, h: 700 }
  it('puts the bar above the selection, flips below near the top and stays inside the viewport', () => {
    const above = placeBar({ left: 400, right: 500, top: 300, bottom: 320 }, { w: 150, h: 30 }, vp)
    expect(above.top + 30).toBeLessThanOrEqual(300)
    expect(above.left).toBeCloseTo(375)
    const flipped = placeBar({ left: 400, right: 500, top: 10, bottom: 30 }, { w: 150, h: 30 }, vp)
    expect(flipped.top).toBeGreaterThanOrEqual(30)
    expect(placeBar({ left: 0, right: 20, top: 300, bottom: 320 }, { w: 150, h: 30 }, vp).left).toBe(8)
    expect(placeBar({ left: 980, right: 1000, top: 300, bottom: 320 }, { w: 150, h: 30 }, vp).left).toBe(842)
  })
  it('puts the panel below the selection, or above when there is no room, never covering it', () => {
    const below = placePanel({ left: 400, right: 500, top: 100, bottom: 120 }, 360, vp, 200)
    expect(below.top).toBeGreaterThan(120)
    expect(below.left).toBeGreaterThanOrEqual(8)
    const above = placePanel({ left: 400, right: 500, top: 600, bottom: 620 }, 360, vp, 300)
    expect(above.bottom).toBeDefined()
    expect(above.top).toBeUndefined()
    expect(700 - (above.bottom ?? 0)).toBeLessThan(600)
  })
})
