// @vitest-environment jsdom
import { beforeEach, describe, expect, it, vi } from 'vitest'

const load = vi.fn()
const textState: { paperId: string | null; load: typeof load } = { paperId: 'p1', load }

vi.mock('@/features/text/textStore', () => ({ useTextStore: { getState: () => textState } }))
vi.mock('@/components/ui', () => ({ toast: { info: vi.fn(), success: vi.fn(), warning: vi.fn(), error: vi.fn() } }))
vi.mock('@/lib/api', () => ({
  errorMessage: (e: unknown) => String(e),
  api: {
    papers: { reparse: vi.fn(async () => ({ job_id: 'j1' })), undoReparse: vi.fn(async () => ({ ok: true, restored: {} })) },
    jobs: { get: vi.fn(async () => ({ id: 'j1', status: 'completed', result: { items: [{ paper_id: 'p1', stats: {} }] } })) }
  }
}))

import { reparsePapers, undoReparse } from './reparseActions'

beforeEach(() => {
  load.mockClear()
  textState.paperId = 'p1'
})

describe('重新解析之后文字视图要重新读取', () => {
  it('正显示这一篇：重新解析完成后重新载入文字', async () => {
    await reparsePapers(['p1'])
    expect(load).toHaveBeenCalledWith('p1')
  })

  it('正显示的是别的论文：不去动它', async () => {
    textState.paperId = 'other'
    await reparsePapers(['p1'])
    expect(load).not.toHaveBeenCalled()
  })

  it('撤销之后同样重新载入', async () => {
    await undoReparse('p1')
    expect(load).toHaveBeenCalledWith('p1')
  })
})
