// @vitest-environment jsdom
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'

vi.mock('@/features/materials/SupplementPermissionHint', () => ({ SupplementPermissionHint: () => null }))

const sent: Array<Record<string, unknown>> = []
vi.mock('@/lib/api.vision', async (orig) => {
  const real = await orig<typeof import('@/lib/api.vision')>()
  return {
    ...real,
    visionApi: {
      ...real.visionApi,
      explainFigure: async (body: Record<string, unknown>, cb: (e: string, d: unknown) => void) => {
        sent.push(body)
        cb('meta', { card_id: 'f1', conversation_id: 'v1', model: 'vlm', task_type: 'vision', context: { passage_ords: [], passage_count: 0, chars_sent: 0, includes: {} } })
        cb('delta', { text: '图中看不出。' })
        cb('error', { code: 'rate_limited', message: '请求太频繁' }) // 不需要真的出卡片，只看请求是不是发给了图解析接口
      }
    }
  }
})
vi.mock('@/lib/api', async (orig) => {
  const real = await orig<typeof import('@/lib/api')>()
  return { ...real, api: { ...real.api, ai: { ask: vi.fn(async () => undefined) }, chains: { list: async () => ({ items: [] }) }, conversations: { list: async () => ({ items: [] }) } } }
})

import type { InsightCard } from '@/lib/types'
import { api } from '@/lib/api'
import { useReaderStore } from '@/features/reader/readerStore'
import { useTextStore } from '@/features/text/textStore'
import { aiParseFigure } from '@/features/text/figAi'
import { CardView } from './CardView'
import { Composer } from './Composer'
import { useInsightStore } from './insightStore'

const figCard = (over: Record<string, unknown> = {}): InsightCard =>
  ({
    id: 'f1',
    chain_id: 'k1',
    paper_id: 'p1',
    conversation_id: 'v1',
    task_type: 'vision',
    intent: 'figure',
    object_type: 'figure',
    object_label: 'Fig. 1',
    question: '请解析这张图',
    selection: { text: 'Fig. 1 caption', page: 2, figure_id: 7 },
    model: 'vlm',
    created_at: new Date().toISOString(),
    saved: true,
    status: 'complete',
    answer: {
      direct: 'ANC 约 0.52。',
      detail: null,
      inferences: [],
      follow_ups: [],
      evidence_notes: [],
      figure: { id: 7, label: 'Fig. 1', page: 2, answerable: true, seen: '一张折线图', numbers: [{ value: '0.52', how: 'estimated' }, { value: '10', how: 'printed' }], model: 'vlm' }
    },
    evidence: [],
    verification: { status: 'partial', notes: ['其中的数值是读图估计。'], invalid_citations: [], invalid_refs: [] },
    ...over
  }) as unknown as InsightCard

beforeEach(() => {
  sent.length = 0
  vi.mocked(api.ai.ask).mockClear()
  useInsightStore.setState({ paperId: 'p1', cards: [], pending: null, conversations: [], activeConversation: 'v1', activeChain: null })
  useReaderStore.setState({ contextSelection: null, selection: null, rightOpen: false } as never)
  useTextStore.setState({ items: [{ ord: 5, page: 2, kind: 'caption', section_title: null, text: 'Fig. 1  Overview of the system.', bbox: [0, 0, 1, 1] }] as never })
})
afterEach(cleanup)

describe('图的 AI 解析放在右侧对话里', () => {
  it('“让 AI 解析”：打开右侧 AI 面板，把请求发给图解析接口（带这张图的 id 和图注），不走普通问答', async () => {
    aiParseFigure({ id: 7, page: 2, bbox: [0, 0, 1, 1], kind: 'figure', label: 'Fig. 1', caption_ord: 5 })
    await waitFor(() => expect(sent).toHaveLength(1))
    expect(sent[0]).toMatchObject({ paper_id: 'p1', figure_id: 7, conversation_id: 'v1' })
    expect(String(sent[0].question)).toContain('请解析这张图')
    expect(useReaderStore.getState().rightOpen).toBe(true)
    expect(api.ai.ask).not.toHaveBeenCalled()
    expect(useInsightStore.getState().pending?.figureId).toBe(7) // 失败后“重试”还是在解析这张图
  })

  it('图解析卡片显示“AI 看到的”，读出的数字区分图上印的和估读的', () => {
    const { container } = render(
      <MemoryRouter>
        <CardView card={figCard()} editable />
      </MemoryRouter>
    )
    expect(container.textContent).toContain('AI 看到的：一张折线图')
    expect(container.textContent).toContain('0.52')
    expect(container.textContent).toContain('估读的')
    expect(container.textContent).toContain('图上印的')
    expect(container.querySelector('.pd-card-qedit-btn')).toBeNull() // 图解析卡片不能“修改上一次提问”：改写走的是普通问答，不会再带图
  })

  it('最后一张是图解析卡片时，输入框带着“看图”标签：接着问还是问这张图；点 × 改成普通提问', async () => {
    useInsightStore.setState({ cards: [figCard()] })
    const { container } = render(
      <MemoryRouter>
        <Composer />
      </MemoryRouter>
    )
    expect(container.querySelector('.pd-chip--fig')?.textContent).toContain('Fig. 1')
    const ta = container.querySelector('textarea') as HTMLTextAreaElement
    fireEvent.change(ta, { target: { value: '纵轴是什么？' } })
    fireEvent.click(screen.getByRole('button', { name: '发送' }))
    await waitFor(() => expect(sent).toHaveLength(1))
    expect(sent[0]).toMatchObject({ figure_id: 7, question: '纵轴是什么？' })

    useInsightStore.setState({ pending: null })
    fireEvent.click(screen.getByLabelText('不再带着这张图提问'))
    expect(container.querySelector('.pd-chip--fig')).toBeNull()
    fireEvent.change(ta, { target: { value: '换个话题' } })
    fireEvent.click(screen.getByRole('button', { name: '发送' }))
    await waitFor(() => expect(api.ai.ask).toHaveBeenCalledTimes(1))
    expect(sent).toHaveLength(1) // 这次没有再发给图解析接口
  })

  it('对话里最后一张不是图解析卡片时没有“看图”标签', () => {
    useInsightStore.setState({ cards: [figCard(), figCard({ id: 'n2', object_type: 'free', answer: { direct: '好', detail: null, inferences: [], follow_ups: [], evidence_notes: [] } })] })
    const { container } = render(
      <MemoryRouter>
        <Composer />
      </MemoryRouter>
    )
    expect(container.querySelector('.pd-chip--fig')).toBeNull()
  })
})
