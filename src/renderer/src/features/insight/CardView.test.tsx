// @vitest-environment jsdom
import { afterEach, describe, expect, it, vi } from 'vitest'
import { cleanup, fireEvent, render } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'

vi.mock('@/features/materials/SupplementPermissionHint', () => ({ SupplementPermissionHint: () => null }))

import type { InsightCard } from '@/lib/types'
import { CardView } from './CardView'

afterEach(cleanup)

const card = (over: Record<string, unknown> = {}): InsightCard =>
  ({
    id: 'c1',
    chain_id: null,
    paper_id: 'p1',
    task_type: 'term',
    intent: 'term',
    object_type: 'selection',
    object_label: 'giant connected component',
    question: '这是什么',
    selection: { page: 1, text: 'giant connected component', passage_ords: [3] },
    model: 'DeepSeek-V4-Flash-0731-W8A8',
    created_at: new Date().toISOString(),
    saved: false,
    status: 'complete',
    answer: {
      direct: 'GCC 是网络里规模最大的连通子图 [P3]。',
      detail: '详细解释 [P3]。',
      inferences: [{ text: '作者可能是为了衡量连通性', basis_ords: [3], basis_refs: null }],
      follow_ups: ['为什么用 GCC？'],
      evidence_notes: []
    },
    evidence: [{ ord: 3, page: 1, section_title: '正文', quote: 'The GCC is the largest connected component.', bbox: [0, 0, 1, 1], verified: true, source: null }],
    verification: { status: 'verified', notes: [], invalid_citations: [], invalid_refs: [] },
    ...over
  }) as unknown as InsightCard

const view = (c: InsightCard) =>
  render(
    <MemoryRouter>
      <CardView card={c} />
    </MemoryRouter>
  )

describe('CardView 重做后的结构', () => {
  it('信息行合成一行：类型、页码、模型短名、时间，不再有方框标签和大标题', () => {
    const { container } = view(card())
    const meta = container.querySelector('.pd-card-meta-text')!
    expect(meta.textContent).toContain('术语解释')
    expect(meta.textContent).toContain('第 1 页')
    expect(meta.textContent).toContain('DeepSeek-V4-Flash')
    expect(meta.textContent).not.toContain('W8A8')
    expect(meta.getAttribute('title')).toBe('DeepSeek-V4-Flash-0731-W8A8')
    expect(container.querySelector('.pd-card-title')).toBeNull()
    expect(container.querySelector('.pd-card-tags')).toBeNull()
    expect(container.querySelector('.pd-tag')).toBeNull()
  })

  it('选中的文字以引用样式放在问题上面', () => {
    const { container } = view(card())
    expect(container.querySelector('.pd-card-question blockquote')!.textContent).toBe('giant connected component')
    expect(container.querySelector('.pd-card-question p')!.textContent).toBe('这是什么')
  })

  it('论文之外的补充单独成块、标明不是论文原文，引用的网络资料能点开', () => {
    const c = card({
      answer: {
        direct: '论文里没有写这一点。',
        detail: null,
        inferences: [],
        follow_ups: [],
        evidence_notes: [],
        outside: '这是个通用概念，见资料 [W1]；编造的 [W9] 不显示。',
        web: [{ n: 1, title: 'Cascading failure', url: 'https://en.wikipedia.org/wiki/Cascading_failure', source: '维基百科' }]
      },
      evidence: []
    })
    const { container } = view(c)
    const box = container.querySelector('.pd-card-outside')!
    expect(box.textContent).toContain('不是论文原文')
    expect(box.textContent).toContain('通用概念')
    expect(box.textContent).not.toContain('[W9]')
    expect(box.querySelector('.pd-card-web button')!.textContent).toContain('维基百科 · Cascading failure')
  })

  it('保存、加入笔记、复制、删除收进更多操作菜单，不常驻占行', () => {
    const { container } = view(card())
    expect(container.querySelector('.pd-card-foot')).toBeNull()
    expect(container.querySelectorAll('.pd-card-menu button').length).toBe(1)
    expect(container.querySelector('.pd-card-menu button')!.getAttribute('aria-label')).toBe('更多操作')
  })

  it('回答正文、详细部分和问题行都带 data-translatable，输入框没有', () => {
    const { container } = view(card())
    expect(container.querySelector('.pd-card-direct')!.hasAttribute('data-translatable')).toBe(true)
    expect(container.querySelector('.pd-card-question p')!.hasAttribute('data-translatable')).toBe(true)
    expect(container.querySelector('textarea')).toBeNull()
  })

  it('展开后依据、AI 推断、后续问题都在带 data-translatable 的容器里，AI 推断用小标题而不是逐条标签', () => {
    const { container, getByText } = view(card())
    fireEvent.click(getByText('展开详细分析与依据'))
    const more = container.querySelector('.pd-card-more')!
    expect(more.hasAttribute('data-translatable')).toBe(true)
    const titles = Array.from(more.querySelectorAll('.pd-card-sec')).map((e) => e.textContent)
    expect(titles).toEqual(['详细分析', '原文依据', 'AI 推断', '后续问题'])
    expect(more.querySelector('.pd-card-inf')!.querySelector('.pd-tag')).toBeNull()
    expect(more.querySelector('.pd-ev-quote')!.textContent).toContain('largest connected component')
  })

  it('核实状态用一行小字，已核实时跟在依据后面', () => {
    const { container } = view(card())
    expect(container.querySelector('.pd-card-status')!.textContent).toBe('已核实')
    expect(container.querySelector('.pd-card-hint')).toBeNull()
  })

  it('部分核实时给一行说明，而不是大块提示', () => {
    const { container } = view(card({ verification: { status: 'partial', notes: [], invalid_citations: [], invalid_refs: [] } }))
    const hint = container.querySelector('.pd-card-hint')!
    expect(hint.textContent).toContain('部分核实')
    expect(hint.tagName).toBe('P')
  })
})
