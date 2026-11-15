import { describe, expect, it } from 'vitest'
import { findSectionRefs, splitSectionRefs } from './sectionRefs'

describe('section references', () => {
  it('recognizes English and Chinese section forms', () => {
    expect(findSectionRefs('See Section 3.3 and 第 4.1 节.').map((x) => x.number)).toEqual(['3.3', '4.1'])
  })
  it('links only sections that exist', () => {
    expect(splitSectionRefs('See Section 3.3 and Section 9.', (n) => n === '3.3')).toEqual([
      { text: 'See ' },
      { text: 'Section 3.3', number: '3.3' },
      { text: ' and Section 9.' }
    ])
  })
})
