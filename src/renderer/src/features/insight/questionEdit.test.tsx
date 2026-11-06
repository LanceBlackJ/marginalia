// @vitest-environment jsdom
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'

vi.mock('@/features/materials/SupplementPermissionHint', () => ({ SupplementPermissionHint: () => null }))

const sent: Array<Record<string, unknown>> = []
vi.mock('@/lib/api', async (orig) => {
  const real = await orig<typeof import('@/lib/api')>()
  return {
    ...real,
    api: {
      ...real.api,
      ai: {
        ask: async (body: Record<string, unknown>, cb: (e: string, d: unknown) => void) => {
          sent.push(body)
          cb('error', { code: 'rate_limited', message: '请求太频繁' })
        }
      },
      chains: { list: async () => ({ items: [] }) },
      conversations: { list: async () => ({ items: [] }) }
    }
  }
})

import type { InsightCard } from '@/lib/types'
import { CardView } from './CardView'
import { lastCardId, useInsightStore } from './insightStore'

const card = (id: string, over: Record<string, unknown> = {}): InsightCard =>
  ({
    id,
    chain_id: 'k1',
    paper_id: 'p1',
    conversation_id: 'v1',
    task_type: 'free',
    intent: 'free',
    object_type: 'free',
    object_label: '',
    question: '这是什么',
    selection: null,
    model: 'm',
    created_at: new Date().toISOString(),
    saved: true,
    status: 'complete',
    answer: { direct: '回答', detail: null, inferences: [], follow_ups: [], evidence_notes: [] },
    evidence: [],
    verification: { status: 'verified', notes: [], invalid_citations: [], invalid_refs: [] },
    ...over
  }) as unknown as InsightCard

const view = (c: InsightCard, editable: boolean) =>
  render(
    <MemoryRouter>
      <CardView card={c} editable={editable} />
    </MemoryRouter>
  )

beforeEach(() => {
  sent.length = 0
  useInsightStore.setState({ paperId: 'p1', cards: [], pending: null, conversations: [], activeConversation: 'v1', activeChain: null })
})
afterEach(cleanup)

describe('修改上一次提问', () => {
  it('只有对话里最后一张卡片算“可修改”，别的对话、不属于对话的卡片不算', () => {
    const cards = [card('a'), card('b'), card('c', { conversation_id: 'v2' })]
    expect(lastCardId(cards, 'v1')).toBe('b')
    expect(lastCardId(cards, 'v2')).toBe('c')
    expect(lastCardId(cards, null)).toBeNull()
    expect(lastCardId([card('x', { conversation_id: null })], 'v1')).toBeNull()
  })

  it('可修改时问题旁有“修改”按钮，不可修改时没有', () => {
    const a = view(card('a'), true)
    expect(a.container.querySelector('.pd-card-qedit-btn')).not.toBeNull()
    cleanup()
    const b = view(card('a'), false)
    expect(b.container.querySelector('.pd-card-qedit-btn')).toBeNull()
  })

  it('点修改：原样带出原来的提问；改完点“重新回答”，请求带上 edit_card_id 和新的问题', async () => {
    view(card('a', { question: '第一次的问题' }), true)
    fireEvent.click(screen.getByLabelText('修改这条提问'))
    const box = screen.getByLabelText('修改我的提问', { selector: 'textarea' }) as HTMLTextAreaElement
    expect(box.value).toBe('第一次的问题') // 带出的是原文，不是压缩过空白的显示文字
    fireEvent.change(box, { target: { value: '第一次的问题，补充：我只关心实验部分' } })
    fireEvent.click(screen.getByRole('button', { name: '重新回答' }))
    await new Promise((r) => setTimeout(r, 20))
    expect(sent).toHaveLength(1)
    expect(sent[0]).toMatchObject({ edit_card_id: 'a', question: '第一次的问题，补充：我只关心实验部分', conversation_id: 'v1' })
    // 重答期间被改写的那张卡片让位给进行中的卡片；失败后 pending 还带着它的 id，“保留原来的”才能让原卡片回来
    expect(useInsightStore.getState().pending?.editingCardId).toBe('a')
  })

  it('取消不发任何请求；问题清空后不能提交', () => {
    view(card('a'), true)
    fireEvent.click(screen.getByLabelText('修改这条提问'))
    const box = screen.getByLabelText('修改我的提问', { selector: 'textarea' })
    fireEvent.change(box, { target: { value: '   ' } })
    expect((screen.getByRole('button', { name: '重新回答' }) as HTMLButtonElement).disabled).toBe(true)
    fireEvent.click(screen.getByRole('button', { name: '取消' }))
    expect(sent).toHaveLength(0)
    expect(screen.queryByLabelText('修改我的提问', { selector: 'textarea' })).toBeNull()
  })

  it('原来带过附件：说明不会再发送，需要重新添加', () => {
    view(card('a', { attachments: [{ name: '图1.png', kind: 'image' }] }), true)
    fireEvent.click(screen.getByLabelText('修改这条提问'))
    expect(screen.getByText(/图1\.png 不会再发送/)).toBeTruthy()
  })
})
