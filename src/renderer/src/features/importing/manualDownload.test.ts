import { describe, expect, it } from 'vitest'
import { bestDownloadLink, otherDownloadLinks } from './manualDownload'

const link = (label: string, url: string) => ({ label, url }) as never

describe('去哪里下载', () => {
  it('优先用后端记录的可用来源，其次 DOI 链接，最后用你给的链接', () => {
    expect(bestDownloadLink({ doi: '10.1/x', access_info: [link('出版商', 'https://publisher.test/a')] })).toBe('https://publisher.test/a')
    expect(bestDownloadLink({ doi: '10.1/x', access_info: [] })).toBe('https://doi.org/10.1/x')
    expect(bestDownloadLink({ doi: null, source_url: 'https://example.org/p', access_info: [] })).toBe('https://example.org/p')
    expect(bestDownloadLink({ doi: null, access_info: [] })).toBeNull()
  })

  it('只接受 http 和 https 链接', () => {
    expect(bestDownloadLink({ doi: null, access_info: [link('x', 'javascript:alert(1)')], source_url: 'file:///c:/a.pdf' })).toBeNull()
  })

  it('备选链接去掉最佳链接和重复项', () => {
    const p = { doi: '10.1/x', source_url: 'https://doi.org/10.1/x', access_info: [link('出版商', 'https://publisher.test/a'), link('开放获取页', 'https://oa.test/b')] }
    expect(bestDownloadLink(p)).toBe('https://publisher.test/a')
    expect(otherDownloadLinks(p).map((l) => l.url)).toEqual(['https://oa.test/b', 'https://doi.org/10.1/x'])
  })
})
