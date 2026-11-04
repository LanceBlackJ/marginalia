// @vitest-environment jsdom
import { afterEach, describe, expect, it } from 'vitest'
import { cleanup, render } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import type { Evidence } from '@/lib/types'
import { buildEvidenceMap, CiteGroup } from './CiteChip'

afterEach(cleanup)

const ev = (ord: number, page: number): Evidence =>
  ({ ord, page, section_title: null, quote: `原文 ${ord}`, bbox: [0, 0, 1, 1], verified: true, source: null }) as unknown as Evidence

describe('CiteGroup 相邻引用合并', () => {
  it('多个引用合并成一串，整体是一个 pd-cites；重复的只留一个', () => {
    const evidence = buildEvidenceMap([ev(3, 1), ev(7, 2)])
    const { container } = render(
      <MemoryRouter>
        <CiteGroup refs={[{ doc: 0, ord: 3 }, { doc: 0, ord: 7 }, { doc: 0, ord: 3 }]} evidence={evidence} invalid={new Set()} />
      </MemoryRouter>
    )
    const groups = container.querySelectorAll('.pd-cites')
    expect(groups.length).toBe(1)
    const labels = Array.from(groups[0].querySelectorAll('.pd-cite')).map((e) => e.textContent)
    expect(labels).toEqual(['p.1', 'p.2'])
  })

  it('无法核实的引用仍然保留，不被合并掉', () => {
    const { container } = render(
      <MemoryRouter>
        <CiteGroup refs={[{ doc: 0, ord: 99 }]} evidence={buildEvidenceMap([])} invalid={new Set(['P99'])} />
      </MemoryRouter>
    )
    expect(container.querySelector('.pd-cite--invalid')).toBeTruthy()
  })
})
