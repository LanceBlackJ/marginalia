import { describe, expect, it } from 'vitest'
import type { Work } from '@/lib/types.discovery'
import { formatReference, splitName, toBibtex, toCsv, toReferenceList, toRis } from './exportWorks'

const w = (over: Partial<Work> = {}): Work =>
  ({
    key: 'k', title: 'Attention Is All You Need', authors: ['Ashish Vaswani', 'Noam Shazeer'], year: 2017, venue: 'Advances in Neural Information Processing Systems', venue_type: 'conference',
    doi: '10.1/xyz', arxiv_id: '1706.03762', url: 'https://x.test/p', abstract: 'We propose, "a" model & more.', status: 'published', status_label: '正式发表', kind_label: '研究论文',
    citations: 100, open_access: true, field: 'Computer Science', ...over
  }) as Work

describe('导出', () => {
  it('姓名拆分：西文取最后一个词作姓，中文整体当姓名', () => {
    expect(splitName('Ashish Vaswani')).toMatchObject({ family: 'Vaswani', given: 'Ashish' })
    expect(splitName('Vaswani, Ashish')).toMatchObject({ family: 'Vaswani', given: 'Ashish' })
    expect(splitName('张三')).toMatchObject({ family: '张三', cjk: true })
  })
  it('BibTeX：会议论文用 inproceedings，键不重复，特殊字符转义', () => {
    const out = toBibtex([w(), w()])
    expect(out).toContain('@inproceedings{vaswani2017attention,')
    expect(out).toContain('@inproceedings{vaswani2017attention2,')
    expect(out).toContain('author = {Vaswani, Ashish and Shazeer, Noam}')
    expect(out).toContain('\\&')
  })
  it('RIS：每条以 TY 开头、ER 结尾，作者逐行', () => {
    const out = toRis([w()])
    expect(out.startsWith('TY  - CONF')).toBe(true)
    expect(out).toContain('AU  - Ashish Vaswani\r\nAU  - Noam Shazeer')
    expect(out.trimEnd().endsWith('ER  -')).toBe(true)
  })
  it('CSV：带 BOM、逗号和引号正确转义、公式开头加单引号防注入', () => {
    const out = toCsv([w({ title: '=cmd|"x"', abstract: 'a,b' })])
    expect(out.startsWith('﻿')).toBe(true)
    expect(out).toContain('"\'=cmd|""x"""')
    expect(out).toContain('"a,b"')
  })
  it('参考文献文本：三种格式，缺失的信息直接省略', () => {
    expect(formatReference(w({ venue_type: 'journal', venue: 'Nature' }), 'gbt7714')).toBe('VASWANI A, SHAZEER N. Attention Is All You Need[J]. Nature, 2017. DOI:10.1/xyz.')
    expect(formatReference(w({ venue_type: 'journal', venue: 'Nature' }), 'apa')).toBe('Vaswani, A., & Shazeer, N. (2017). Attention Is All You Need. Nature. https://doi.org/10.1/xyz')
    expect(formatReference(w({ venue_type: 'journal', venue: 'Nature' }), 'ieee')).toBe('A. Vaswani and N. Shazeer, "Attention Is All You Need," Nature, 2017, doi: 10.1/xyz.')
    expect(formatReference(w({ authors: [], doi: null, venue: null, year: null }), 'gbt7714')).toBe('Attention Is All You Need[C].')
    expect(toReferenceList([w(), w()], 'ieee').split('\n')[1].startsWith('[2] ')).toBe(true)
  })
})
