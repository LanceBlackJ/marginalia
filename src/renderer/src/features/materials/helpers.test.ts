import { describe, expect, it } from 'vitest'
import type { SupplementAsset } from '@/lib/types.materials'
import { assetFacts, canAttachAsSupplement, hasActiveAssets } from './helpers'

function asset(over: Partial<SupplementAsset>): SupplementAsset {
  return {
    id: 'a1',
    paper_id: 'p1',
    filename: 'x.csv',
    mime: null,
    size: null,
    kind: 'table',
    source: 'local',
    source_url: null,
    category: '数据集',
    category_source: 'rule',
    summary: null,
    match_status: '用户确认',
    match_basis: null,
    download_status: 'done',
    download_error: null,
    analysis_status: 'none',
    analysis: null,
    child_paper_id: null,
    child: null,
    job_id: null,
    created_at: '2026-01-01T00:00:00Z',
    ...over
  }
}

describe('hasActiveAssets', () => {
  it('排队、下载、解析中都算进行中；完成、失败、等待批准不需要轮询', () => {
    expect(hasActiveAssets([{ download_status: 'done' }, { download_status: 'downloading' }])).toBe(true)
    expect(hasActiveAssets([{ download_status: 'pending' }])).toBe(true)
    expect(hasActiveAssets([{ download_status: 'processing' }])).toBe(true)
    expect(hasActiveAssets([{ download_status: 'done' }, { download_status: 'failed' }, { download_status: 'waiting_approval' }, { download_status: 'rejected' }])).toBe(false)
  })
})

describe('assetFacts', () => {
  it('PDF 补充材料显示大小与页数', () => {
    const f = assetFacts(asset({ kind: 'pdf', size: 64 * 1024 * 1024, child: { page_count: 92, status: 'ready', title: 'S' } }))
    expect(f).toEqual(['64.0 MB', '92 页'])
  })
  it('表格显示行列数，较大文件标注只扫描了一部分', () => {
    const f = assetFacts(asset({ analysis: { kind: 'table', sheets: 1, rows: 1200, columns: 8, partial: true } }))
    expect(f[0]).toContain('1,200 行 × 8 列')
    expect(f[0]).toContain('仅扫描了前一部分')
  })
  it('多个工作表时标出数量', () => {
    const f = assetFacts(asset({ analysis: { kind: 'table', sheets: 3, rows: 10, columns: 2 } }))
    expect(f[0]).toMatch(/^3 个工作表 · 10 行 × 2 列$/)
  })
  it('压缩包与文档', () => {
    expect(assetFacts(asset({ analysis: { kind: 'archive', total_files: 42 } }))).toEqual(['42 个文件'])
    expect(assetFacts(asset({ analysis: { kind: 'document', paragraphs: 7 } }))).toEqual(['7 段'])
  })
})

describe('canAttachAsSupplement', () => {
  const base = { can_attach_as_supplement: true, target_paper_id: 'p1' }
  it('本地路径与文件链接可以作为补充材料添加', () => {
    expect(canAttachAsSupplement({ ...base, input: String.raw`C:\papers\s.pdf` })).toBe(true)
    expect(canAttachAsSupplement({ ...base, input: String.raw`\\server\share\s.pdf` })).toBe(true)
    expect(canAttachAsSupplement({ ...base, input: 'C:/papers/s.pdf' })).toBe(true)
    expect(canAttachAsSupplement({ ...base, input: '/home/u/s.pdf' })).toBe(true)
    expect(canAttachAsSupplement({ ...base, input: 'https://example.com/s.pdf' })).toBe(true)
  })
  it('DOI 输入、没有提示或缺少目标论文时不可以', () => {
    expect(canAttachAsSupplement({ ...base, input: '10.1038/s42256-020-0177-2' })).toBe(false)
    expect(canAttachAsSupplement({ can_attach_as_supplement: false, target_paper_id: 'p1', input: 'C:/a.pdf' })).toBe(false)
    expect(canAttachAsSupplement({ can_attach_as_supplement: true, target_paper_id: null, input: 'C:/a.pdf' })).toBe(false)
  })
})
