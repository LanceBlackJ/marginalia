import { describe, expect, it } from 'vitest'
import type { TextItem } from '@/lib/api.text'
import { parseAffiliations, parseAuthors, splitFront } from './frontMatter'

const it_ = (ord: number, section: string | null, text: string, kind: TextItem['kind'] = 'paragraph'): TextItem => ({
  ord,
  page: 1,
  kind,
  section_title: section,
  text,
  bbox: [0, 0, 1, 1]
})

describe('parseAuthors', () => {
  it('drops affiliation digits, the email glyph and broken characters', () => {
    expect(parseAuthors('Changjun Fan1,2, Li Zeng1, Yizhou Sun 2 \ud889 and Yang-Yu Liu 3,4 \ud889')).toEqual(['Changjun Fan', 'Li Zeng', 'Yizhou Sun', 'Yang-Yu Liu'])
    expect(parseAuthors('Ann Lee* and Bo Chen✉')).toEqual(['Ann Lee', 'Bo Chen'])
  })
})

describe('parseAffiliations', () => {
  it('splits numbered affiliations into lines', () => {
    const lines = parseAffiliations('1College of Systems Engineering, NUDT, Changsha, China. 2Department of Computer Science, UCLA, USA. 3Center for Complex Networks, Northeastern University, USA.')
    expect(lines).toHaveLength(3)
    expect(lines[0].startsWith('1College of Systems')).toBe(true)
    expect(lines[2].startsWith('3Center for Complex')).toBe(true)
  })
})

describe('splitFront', () => {
  const items = [
    it_(0, '首部信息', 'https://doi.org/10.1038/s42256-020-0177-2'),
    it_(1, '首部信息', 'Changjun Fan1,2, Li Zeng1 and Yang-Yu Liu 3,4 \ud889'),
    it_(2, 'Abstract', 'Finding an optimal set of nodes, called key players, is a fundamental problem.'),
    it_(3, '正文', 'The network is a common data structure.'),
    it_(4, '首部信息', '1College of Systems Engineering, Changsha, China. 2Department of Computer Science, UCLA. e-mail: yyl@neu.edu'),
    it_(5, '首部信息', 'Received: 6 October 2019; Accepted: 14 April 2020;'),
    it_(6, '正文', 'caption', 'caption')
  ]

  it('sorts the front section into DOI, authors, affiliations, dates and emails', () => {
    const { front, abstract, rest } = splitFront(items)
    expect(front?.doi?.text).toContain('10.1038')
    expect(front?.authors?.names).toEqual(['Changjun Fan', 'Li Zeng', 'Yang-Yu Liu'])
    expect(front?.affiliations?.lines.length).toBe(2)
    expect(front?.dates.map((d) => d.ord)).toEqual([5])
    expect(front?.emails.map((e) => e.text)).toEqual(['yyl@neu.edu'])
    expect(abstract.map((a) => a.ord)).toEqual([2])
    expect(rest.map((r) => r.ord)).toEqual([3, 6]) // 信息块和摘要里的段落不再在正文里重复
  })

  it('leaves everything in place when there is no front section', () => {
    const plain = [it_(0, 'Introduction', 'text one'), it_(1, 'Methods', 'text two')]
    const { front, abstract, rest } = splitFront(plain)
    expect(front).toBeNull()
    expect(abstract).toEqual([])
    expect(rest).toHaveLength(2)
  })
})

describe('splitFront: 首页底部的作者信息（kind=front）', () => {
  it('不管落在哪个章节，都从正文里拿出来放进信息块', () => {
    const items = [
      it_(0, 'Introduction', 'Enhancing the reliability of agents has long been promising.'),
      it_(1, 'Introduction', '*Correspondence: Yu Wu wuyucs@whu.edu.cn 1 School of Computer Science, Wuhan University, China', 'front'),
      it_(2, 'Introduction', '© The Author(s) 2024. Open Access This article is licensed under a Creative Commons license.', 'front'),
      it_(3, 'Introduction', 'Large language models have demonstrated exceptional potential.')
    ]
    const { front, rest } = splitFront(items)
    expect(rest.map((r) => r.ord)).toEqual([0, 3])
    expect(front?.emails.map((e) => e.text)).toContain('wuyucs@whu.edu.cn')
    expect(front?.other.some((o) => o.text.includes('licensed'))).toBe(true)
  })
})
