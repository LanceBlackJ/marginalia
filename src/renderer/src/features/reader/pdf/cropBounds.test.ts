import { describe, expect, it } from 'vitest'
import type { TextItem } from '@/lib/api.text'
import { contentBounds } from './cropBounds'

const para = (ord: number, x0: number, x1: number, text = 'x'.repeat(60)): TextItem => ({ ord, page: 1, kind: 'paragraph', section_title: null, text, bbox: [x0, 100, x1, 140] })

describe('裁掉白边', () => {
  const body = Array.from({ length: 20 }, (_, i) => para(i, 70 + (i % 2), 520 - (i % 3)))

  it('由正文段落的左右分位数推出内容边界', () => {
    const c = contentBounds(body, 595)
    expect(c).not.toBeNull()
    expect(c!.left).toBeGreaterThan(55)
    expect(c!.left).toBeLessThan(72)
    expect(c!.right).toBeGreaterThan(518)
    expect(c!.right).toBeLessThan(535)
  })

  it('个别越界的段落（页眉页码）不会撑开边界', () => {
    const c = contentBounds([...body, para(99, 5, 590), para(98, 3, 592)], 595)
    expect(c!.left).toBeGreaterThan(55)
    expect(c!.right).toBeLessThan(540)
  })

  it('样本太少、太短的文字、没有白边时都不裁剪', () => {
    expect(contentBounds(body.slice(0, 4), 595)).toBeNull()
    expect(contentBounds(body.map((b) => ({ ...b, text: '短' })), 595)).toBeNull()
    expect(contentBounds(Array.from({ length: 12 }, (_, i) => para(i, 4, 592)), 595)).toBeNull()
  })
})
