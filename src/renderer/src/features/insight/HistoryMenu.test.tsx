// @vitest-environment jsdom
import { afterEach, describe, expect, it, vi } from 'vitest'
import { cleanup, fireEvent, render, screen } from '@testing-library/react'

vi.mock('@/lib/api', () => ({ api: {} }))

import { HistoryMenu } from './HistoryMenu'
import { useInsightStore } from './insightStore'
import type { InsightCard } from '@/lib/types'

const card = (id: string, question: string, created: string): InsightCard =>
  ({ id, paper_id: 'p', chain_id: 'c1', object_type: 'free', object_label: '', selection: null, question, intent: 'free', task_type: '', model: '', status: 'complete', saved: true, created_at: created }) as InsightCard

afterEach(() => {
  cleanup()
  useInsightStore.setState({ cards: [], activeChain: null })
})

describe('AI 对话历史', () => {
  it('没有对话时写明还没有', () => {
    useInsightStore.setState({ cards: [] })
    render(<HistoryMenu />)
    fireEvent.click(screen.getByRole('button', { name: /历史/ }))
    expect(screen.getByText('还没有对话')).toBeTruthy()
  })

  it('最近的在上面；点一条回到“全部”并滚到那张卡片', () => {
    vi.useFakeTimers()
    const el = document.createElement('div')
    el.setAttribute('data-card-id', 'k1')
    el.scrollIntoView = vi.fn()
    document.body.appendChild(el)
    useInsightStore.setState({ cards: [card('k1', '第一个问题', '2026-01-01T00:00:00Z'), card('k2', '第二个问题', '2026-01-02T00:00:00Z')], activeChain: 'c1' })
    render(<HistoryMenu />)
    fireEvent.click(screen.getByRole('button', { name: /历史/ }))
    const labels = screen.getAllByRole('menuitem').map((m) => m.textContent ?? '')
    expect(labels[0]).toContain('第二个问题')
    expect(labels[1]).toContain('第一个问题')
    fireEvent.click(screen.getByText('第一个问题'))
    expect(useInsightStore.getState().activeChain).toBeNull()
    vi.advanceTimersByTime(100)
    expect(el.scrollIntoView).toHaveBeenCalled()
    expect(el.getAttribute('data-flash')).toBe('1')
    vi.useRealTimers()
    el.remove()
  })
})
