// @vitest-environment jsdom
import { afterEach, describe, expect, it, vi } from 'vitest'
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'

const advanced = vi.fn()
vi.mock('@/lib/api.discovery', () => ({ discoveryApi: { advanced: (...a: unknown[]) => advanced(...a), matchLibrary: async () => ({ matches: {} }) } }))
vi.mock('@/features/shared/useLibraryPapers', () => ({ useLibraryPapers: () => ({ items: [], byId: new Map() }) }))
vi.mock('./useWorkActions', () => ({ useLibraryMatchRefresh: () => undefined, useWorkActions: () => ({}) }))

import { AdvancedSearch } from './AdvancedSearch'

afterEach(() => {
  cleanup()
  advanced.mockReset()
})

describe('高级检索', () => {
  it('逐行写检索式：加一行、选“非”，提交的就是这些行（空行不提交），标题可精确匹配', async () => {
    advanced.mockResolvedValue({ rows: [], branches: 1, results: [], total_candidates: 0, after_dedupe: 0, warnings: [], exact_matches: 0 })
    render(<AdvancedSearch />)
    fireEvent.change(screen.getByLabelText('第 1 行检索词'), { target: { value: 'attention is all you need' } })
    fireEvent.click(screen.getByRole('checkbox', { name: /精确匹配/ }))
    fireEvent.click(screen.getByRole('button', { name: /添加一行/ }))
    fireEvent.change(screen.getByLabelText('第 2 行检索词'), { target: { value: 'survey' } })
    fireEvent.click(screen.getByRole('button', { name: '检索' }))
    await waitFor(() => expect(advanced).toHaveBeenCalled())
    const body = advanced.mock.calls[0][0]
    expect(body.rows).toEqual([
      { field: 'title', term: 'attention is all you need', op: 'and', exact: true },
      { field: 'tak', term: 'survey', op: 'and', exact: false }
    ])
    expect(await screen.findByText(/共 0 条/)).toBeTruthy()
  })

  it('没有检索词时不能检索', () => {
    render(<AdvancedSearch />)
    expect((screen.getByRole('button', { name: '检索' }) as HTMLButtonElement).disabled).toBe(true)
  })
})
