import { describe, expect, it } from 'vitest'
import type { TextItem } from '@/lib/api.text'
import { buildVocab, cleanDisplay, dehyphenate, joinParts, parseCaption, splitCitations, splitFirstSentence, splitTag, stripBroken, tidySpaces } from './textClean'

const para = (ord: number, text: string): TextItem => ({ ord, page: 1, kind: 'paragraph', section_title: null, text, bbox: [0, 0, 1, 1] })

describe('dehyphenate', () => {
  it('rejoins words cut at a line end by a common suffix or a short prefix', () => {
    expect(dehyphenate('a predefined connectiv- ity measure')).toBe('a predefined connectivity measure')
    expect(dehyphenate('a careful consider- ation of cost')).toBe('a careful consideration of cost')
    expect(dehyphenate('the in- formation flow')).toBe('the information flow')
  })

  it('keeps real hyphenated compounds, only dropping the space', () => {
    expect(dehyphenate('a well- known result')).toBe('a well-known result')
    expect(dehyphenate('state- of- the- art methods')).toBe('state-of-the-art methods')
    expect(dehyphenate('a node- level task')).toBe('a node-level task')
    expect(dehyphenate('Yang- Yu Liu and Barabási- Albert graphs')).toBe('Yang-Yu Liu and Barabási-Albert graphs')
  })

  it('does not touch ordinary hyphens, minus signs or ranges', () => {
    expect(dehyphenate('well-known 30-50 nodes, x - y, a − b')).toBe('well-known 30-50 nodes, x - y, a − b')
  })

  it('is conservative when unsure: keeps the hyphen', () => {
    expect(dehyphenate('a scalable archi- tecture')).toBe('a scalable archi-tecture')
  })
})

describe('stripBroken and tidySpaces', () => {
  it('removes lone surrogates, replacement characters and control characters', () => {
    expect(stripBroken('Yizhou Sun 2 \ud83d and Liu�\u0001')).toBe('Yizhou Sun 2  and Liu')
    expect(stripBroken('ok 😀 fine')).toBe('ok 😀 fine') // 完整的 emoji 保留
  })

  it('collapses spaces and tidies punctuation and parentheses', () => {
    expect(tidySpaces('a  b ,  c ( d ) .')).toBe('a b, c (d).')
  })

  it('cleanDisplay chains the three steps', () => {
    expect(cleanDisplay('the connectiv-  ity ( ANC ) �.')).toBe('the connectivity (ANC).')
  })
})

describe('citation superscripts', () => {
  const vocab = buildVocab([
    para(0, 'complex networks and information flow in systems with the ANC measure. The ANC value and the ANC score. We train resnet models on graphs. These methods are useful methods.'),
    para(1, 'resnet blocks are used.')
  ])
  const render = (t: string): string => splitCitations(t, vocab).map((p) => (p.sup ? `^${p.text}^` : p.text)).join('')

  it('turns digits glued to a word of the paper into superscripts', () => {
    expect(render('describe interactive systems1,2, such as networks8 and information3.')).toBe('describe interactive systems^1,2^, such as networks^8^ and information^3^.')
    expect(render('accumulated normalized connectivity (ANC29) is')).toBe('accumulated normalized connectivity (ANC^29^) is')
    expect(render('recently emerged machine learning methods4,9—predominantly focus')).toBe('recently emerged machine learning methods^4,9^—predominantly focus')
  })

  it('leaves identifiers, maths tokens and unknown words alone', () => {
    expect(render('use log2 and top5 and resnet50 and COVID19 and v2')).toBe('use log2 and top5 and resnet50 and COVID19 and v2')
    expect(render('the foobar12 token')).toBe('the foobar12 token')
    expect(render('networks08 stays')).toBe('networks08 stays')
  })

  it('requires an acronym to appear at least twice before splitting it', () => {
    const once = buildVocab([para(0, 'a single XYZ here')])
    expect(splitCitations('XYZ12 is', once)).toEqual([{ text: 'XYZ12 is' }])
  })

  it('does not split when there is nothing to split', () => {
    expect(splitCitations('plain text', vocab)).toEqual([{ text: 'plain text' }])
  })
})

describe('first sentence', () => {
  const long = 'Finding an optimal set of key players in complex networks has been a long-standing problem in network science, as described by Smith et al. and others. Then comes the second sentence which carries more detail about the topic.'
  it('splits at the first real sentence end, not at et al.', () => {
    const r = splitFirstSentence(long)
    expect(r?.first.endsWith('and others.')).toBe(true)
    expect(r?.rest.trim().startsWith('Then comes')).toBe(true)
  })

  it('returns null for short paragraphs or when no clean break exists', () => {
    expect(splitFirstSentence('A short one. Another.')).toBeNull()
    expect(splitFirstSentence('x'.repeat(300))).toBeNull()
  })
})

describe('captions', () => {
  it('splits label, title and the rest', () => {
    const c = parseCaption('Fig. 1 | Finding key players in a network. a, The 9/11 terrorist network, which contains 62 nodes.')
    expect(c.label).toBe('Fig. 1')
    expect(c.title).toBe('Finding key players in a network.')
    expect(c.rest.startsWith('a, The 9/11')).toBe(true)
    expect(parseCaption('Table 2: Results on real networks.').label).toBe('Table 2')
    expect(parseCaption('Extended Data Fig. 3 | Another figure.').label).toBe('Extended Data Fig. 3')
  })

  it('returns no label for ordinary text', () => {
    expect(parseCaption('Figures of merit are used.').label).toBeNull()
  })
})

describe('joinParts', () => {
  it('joins parts with a single space', () => {
    expect(joinParts(['a b', 'c d', 'e'])).toBe('a b c d e')
    expect(joinParts(['only'])).toBe('only')
  })
})

describe('splitTag', () => {
  it('pulls the \\tag out of recognised LaTeX', () => {
    expect(splitTag('R = \\frac{1}{N} \\tag{1}')).toEqual({ tex: 'R = \\frac{1}{N}', tag: '1' })
    expect(splitTag('a = b')).toEqual({ tex: 'a = b', tag: null })
    expect(splitTag('x \\tag*{(2a)}').tag).toBe('(2a)')
  })
})

describe('中文译文的图注与首句', () => {
  it('图注拆成编号、加粗的标题句、其余', async () => {
    const { parseCaptionZh } = await import('./textClean')
    expect(parseCaptionZh('图 1：FINDER 的整体框架。它由编码器和解码器组成。')).toEqual({ label: '图 1', title: 'FINDER 的整体框架。', rest: '它由编码器和解码器组成。' })
    expect(parseCaptionZh('表 2 | 各方法的性能对比。')).toMatchObject({ label: '表 2', title: '各方法的性能对比。' })
    expect(parseCaptionZh('补充图 S3. 额外结果').label).toBe('补充图 S3')
    expect(parseCaptionZh('这是一段普通的正文。')).toEqual({ label: null, title: '', rest: '这是一段普通的正文。' })
  })

  it('首句加粗：句子不太长才加，没有句末标点不加', async () => {
    const { splitFirstSentenceZh } = await import('./textClean')
    expect(splitFirstSentenceZh('我们提出了一种新方法。实验表明它更好。')).toEqual({ first: '我们提出了一种新方法。', rest: '实验表明它更好。' })
    expect(splitFirstSentenceZh('没有句末标点的一段话')).toBeNull()
    expect(splitFirstSentenceZh('短。后面还有很多内容在这里继续写下去。')).toBeNull()
  })
})
