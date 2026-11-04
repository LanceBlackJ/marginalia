import { describe, expect, it } from 'vitest'
import type { Work } from '@/lib/types.discovery'
import { authorsLine, impactText, importTarget, originalUrl, venueLine, versionLabel } from './workUtils'

function work(p: Partial<Work> = {}): Work {
  return {
    key: 'doi:10.1/x', title: 'T', authors: [], year: 2020, date: null, venue: null, venue_type: null, doi: null, arxiv_id: null, pmid: null,
    abstract: null, url: null, pdf_url: null, status: 'published', status_label: '正式发表', status_evidence: '', kind: 'research', kind_label: '研究论文', kind_evidence: '', peer_reviewed: null,
    core_venue: null, citations: null, fwci: null, percentile: null, field: null, domain: null, topics: [], open_access: null, work_type: null,
    openalex_id: null, sources: [], versions: [], in_library: null, score: 0, breakdown: {}, rank_note: '', reason: '', reason_source: '',
    relevance_note: '', relation: '', ...p
  }
}

describe('impactText', () => {
  it('不会把原始被引数当作唯一依据：有百分位时说明同领域同年份位置', () => {
    expect(impactText(work({ citations: 1200, percentile: 0.995 }))).toBe('被引 1,200 · 同领域同年份前 1%')
    expect(impactText(work({ citations: 80, percentile: 0.93 }))).toBe('被引 80 · 同领域同年份前 10%')
    expect(impactText(work({ citations: 5, percentile: 0.4 }))).toBe('被引 5 · 同领域同年份引用百分位 40')
  })
  it('没有百分位时退回 FWCI；什么都没有则不显示', () => {
    expect(impactText(work({ citations: 10, fwci: 2.34 }))).toBe('被引 10 · FWCI 2.3')
    expect(impactText(work())).toBeNull()
  })
})

describe('文字辅助', () => {
  it('authorsLine 截断并给出总人数', () => {
    expect(authorsLine([])).toBe('作者未知')
    expect(authorsLine(['A', 'B'])).toBe('A、B')
    expect(authorsLine(['A', 'B', 'C', 'D', 'E', 'F'])).toBe('A、B、C、D 等 6 人')
  })
  it('venueLine 与 versionLabel', () => {
    expect(venueLine(work({ venue: 'Nature', year: 2021 }))).toBe('Nature · 2021')
    expect(venueLine(work({ venue: null, year: null }))).toBe('')
    expect(versionLabel({ kind: 'preprint', doi: null, arxiv_id: null, url: null, venue: null })).toBe('预印本')
  })
})

describe('originalUrl / importTarget', () => {
  it('优先 DOI，其次 arXiv，再次来源页面', () => {
    expect(originalUrl(work({ doi: '10.1/a', arxiv_id: '1234.5678' }))).toBe('https://doi.org/10.1/a')
    expect(originalUrl(work({ arxiv_id: '1234.5678' }))).toBe('https://arxiv.org/abs/1234.5678')
    expect(originalUrl(work({ url: 'https://x.org/p' }))).toBe('https://x.org/p')
    expect(originalUrl(work())).toBeNull()
  })
  it('arXiv 自带的 DataCite DOI 不当作正式 DOI 去导入，改用 arXiv 链接', () => {
    expect(importTarget(work({ doi: '10.48550/arXiv.2001.00001', arxiv_id: '2001.00001' }))).toEqual({ kind: 'url', value: 'https://arxiv.org/abs/2001.00001' })
    expect(importTarget(work({ doi: '10.1038/s41467-020-19994-x' }))).toEqual({ kind: 'doi', value: '10.1038/s41467-020-19994-x' })
  })
  it('没有任何可导入标识时返回 null（界面会解释原因而不是假装可以导入）', () => {
    expect(importTarget(work())).toBeNull()
    expect(importTarget(work({ pdf_url: 'https://x.org/a.pdf' }))).toEqual({ kind: 'url', value: 'https://x.org/a.pdf' })
  })
})
