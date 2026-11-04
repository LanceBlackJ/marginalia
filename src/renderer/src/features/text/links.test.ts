import { describe, expect, it } from 'vitest'
import { findLinks, isSafeHref, splitLinks } from './links'

const hrefs = (t: string): string[] => findLinks(t).map((l) => l.href)

describe('链接识别', () => {
  it('修复点后面多了空格的 DOI 网址', () => {
    const t = 'available through our Code Ocean compute capsule (https://doi. org/10.24433/CO.3005605.v1) or on GitHub'
    expect(hrefs(t)).toEqual(['https://doi.org/10.24433/CO.3005605.v1'])
  })

  it('修复斜杠后面多了空格，并剥掉末尾的右括号和句号', () => {
    expect(hrefs('on GitHub (https://github.com/ FFrankyy/FINDER).')).toEqual(['https://github.com/FFrankyy/FINDER'])
    expect(hrefs('(https://github.com/FFrankyy/FINDER).')).toEqual(['https://github.com/FFrankyy/FINDER'])
  })

  it('www 开头的网址去掉末尾逗号', () => {
    expect(hrefs('see www.example.com/path, and more')).toEqual(['https://www.example.com/path'])
  })

  it('网址里自带的成对括号保留', () => {
    expect(hrefs('https://en.wikipedia.org/wiki/Graph_(discrete_mathematics) is')).toEqual(['https://en.wikipedia.org/wiki/Graph_(discrete_mathematics)'])
  })

  it('遇到汉字或全角标点就停', () => {
    expect(hrefs('代码见https://github.com/a/b，作者说明')).toEqual(['https://github.com/a/b'])
  })

  it('doi: 与 DOI 前缀、裸 DOI 都转成 doi.org 链接', () => {
    expect(hrefs('doi:10.1038/s42256-020-0177-2.')).toEqual(['https://doi.org/10.1038/s42256-020-0177-2'])
    expect(hrefs('DOI 10.1103/RevModPhys.74.47')).toEqual(['https://doi.org/10.1103/RevModPhys.74.47'])
    expect(hrefs('Phys. Rev. 10.1103/RevModPhys.74.47, 2002')).toEqual(['https://doi.org/10.1103/RevModPhys.74.47'])
  })

  it('https://doi.org/… 只算一条链接，不重复识别里面的 DOI', () => {
    expect(findLinks('https://doi.org/10.1038/s42256-020-0177-2')).toHaveLength(1)
  })

  it('arXiv 编号和邮箱', () => {
    expect(hrefs('arXiv:1706.03762v5 and arXiv: 2012.12345')).toEqual(['https://arxiv.org/abs/1706.03762v5', 'https://arxiv.org/abs/2012.12345'])
    expect(hrefs('Correspondence: yzsun@cs.ucla.edu.')).toEqual(['mailto:yzsun@cs.ucla.edu'])
  })

  it('不该识别的文字保持原样', () => {
    for (const t of ['volume 10.1038 of the series', 'e.g. the network', 'www', 'See Fig. 3 and Table 1.', 'a@b', 'version 1.2.3', 'p < 0.001, n = 10.5']) {
      expect(findLinks(t), t).toEqual([])
    }
  })

  it('句子后面紧跟的普通单词不会被拼进网址', () => {
    expect(hrefs('visit https://example.com. Next we describe')).toEqual(['https://example.com'])
    expect(hrefs('see www.example.com/ The next section')).toEqual(['https://www.example.com/'])
  })

  it('splitLinks 把原文无损切开', () => {
    const t = 'Code: (https://github.com/ FFrankyy/FINDER). Mail yzsun@cs.ucla.edu ok'
    const pieces = splitLinks(t)
    expect(pieces.map((p) => p.text).join('')).toBe(t)
    expect(pieces.filter((p) => 'href' in p)).toHaveLength(2)
    expect(splitLinks('没有链接')).toEqual([{ text: '没有链接' }])
  })

  it('只放行 http、https 和 mailto', () => {
    expect(isSafeHref('https://a.com')).toBe(true)
    expect(isSafeHref('mailto:a@b.com')).toBe(true)
    expect(isSafeHref('javascript:alert(1)')).toBe(false)
    expect(isSafeHref('file:///C:/x')).toBe(false)
  })
})
