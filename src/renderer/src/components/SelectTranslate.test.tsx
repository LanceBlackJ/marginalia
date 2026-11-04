// @vitest-environment jsdom
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { act, cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'

const mocks = vi.hoisted(() => ({ stream: vi.fn() }))
vi.mock('@/lib/translateText', async () => ({ ...(await vi.importActual<object>('@/lib/translateText')), streamTranslate: mocks.stream }))
vi.mock('@/components/ui', () => ({ Skeleton: () => <div data-testid="skeleton" />, toast: { success: vi.fn(), error: vi.fn() } }))

import { SelectTranslate } from './SelectTranslate'

function select(el: HTMLElement): void {
  const s = window.getSelection()!
  s.removeAllRanges()
  const r = document.createRange()
  r.selectNodeContents(el)
  s.addRange(r)
  act(() => {
    document.dispatchEvent(new Event('selectionchange'))
  })
}

function setup(paperId: string | null = 'p1') {
  return render(
    <>
      <p data-translatable data-testid="en">Failures spread across layered networks.</p>
      <p data-translatable data-testid="zh">这是一段中文内容，没有必要翻译。</p>
      <p data-testid="plain">Plain text not translatable here.</p>
      <SelectTranslate paperId={paperId} />
    </>
  )
}

beforeEach(() => {
  Range.prototype.getBoundingClientRect = () => ({ left: 100, right: 300, top: 200, bottom: 220, width: 200, height: 20, x: 100, y: 200, toJSON: () => ({}) })
  mocks.stream.mockImplementation(async (_p: string, _t: string, h: { onMeta?: (m: unknown) => void; onDelta: (d: string) => void; onDone: (r: unknown) => void }) => {
    h.onMeta?.({ model: 'm1', cached: false })
    h.onDelta('层叠')
    h.onDone({ translation: '层叠网络中的故障扩散。', model: 'm1', cached: false })
  })
})
afterEach(() => {
  cleanup()
  window.getSelection()?.removeAllRanges()
  vi.clearAllMocks()
})

describe('SelectTranslate', () => {
  it('standalone：论文不在文献库里（没有 paperId）也能翻译，带上论文标题，没有就关闭', async () => {
    render(
      <>
        <p data-translatable data-testid="en">Failures spread across layered networks.</p>
        <SelectTranslate paperId={null} standalone contextTitle="Layered networks" />
      </>
    )
    select(screen.getByTestId('en'))
    fireEvent.click(screen.getByRole('button', { name: /翻译/ }))
    await waitFor(() => expect(screen.getByRole('dialog', { name: '译文' }).textContent).toContain('层叠网络中的故障扩散。'))
    expect(mocks.stream).toHaveBeenCalledWith(null, 'Failures spread across layered networks.', expect.anything(), expect.anything(), false, 'Layered networks')
    cleanup()
    render(
      <>
        <p data-translatable data-testid="en">Failures spread across layered networks.</p>
        <SelectTranslate paperId={null} />
      </>
    )
    select(screen.getByTestId('en'))
    expect(screen.queryByRole('toolbar')).toBeNull()
  })

  it('shows translate + copy for an English selection and streams the translation below it', async () => {
    setup()
    select(screen.getByTestId('en'))
    const bar = screen.getByRole('toolbar')
    expect(bar.textContent).toContain('翻译')
    expect(bar.textContent).toContain('复制')
    fireEvent.click(screen.getByRole('button', { name: /翻译/ }))
    await waitFor(() => expect(screen.getByRole('dialog', { name: '译文' }).textContent).toContain('层叠网络中的故障扩散。'))
    expect(mocks.stream).toHaveBeenCalledWith('p1', 'Failures spread across layered networks.', expect.anything(), expect.anything(), false, undefined)
    expect(screen.getByRole('dialog').textContent).toContain('AI 翻译 · m1')
  })

  it('offers no translate button for Chinese text and nothing outside data-translatable', () => {
    setup()
    select(screen.getByTestId('zh'))
    expect(screen.getByRole('toolbar').textContent).not.toContain('翻译')
    expect(screen.getByRole('toolbar').textContent).toContain('复制')
    select(screen.getByTestId('plain'))
    expect(screen.queryByRole('toolbar')).toBeNull()
  })

  it('does nothing without a paper id', () => {
    setup(null)
    select(screen.getByTestId('en'))
    expect(screen.queryByRole('toolbar')).toBeNull()
  })

  it('T translates the selection and Esc closes it', async () => {
    setup()
    select(screen.getByTestId('en'))
    fireEvent.keyDown(document, { key: 't' })
    await waitFor(() => expect(screen.getByRole('dialog')).toBeTruthy())
    expect(mocks.stream).toHaveBeenCalledTimes(1)
    fireEvent.keyDown(document, { key: 'Escape' })
    expect(screen.queryByRole('dialog')).toBeNull()
  })

  it('clicking elsewhere closes the panel but clicking inside it does not', async () => {
    setup()
    select(screen.getByTestId('en'))
    fireEvent.click(screen.getByRole('button', { name: /翻译/ }))
    const dialog = await screen.findByRole('dialog')
    fireEvent.pointerDown(dialog)
    expect(screen.queryByRole('dialog')).toBeTruthy()
    fireEvent.pointerDown(document.body)
    expect(screen.queryByRole('dialog')).toBeNull()
  })

  it('shows a readable error with retry, and a key hint without retry when there is no API key', async () => {
    mocks.stream.mockImplementationOnce(async (_p: string, _t: string, h: { onError: (e: unknown) => void }) => h.onError({ code: 'upstream', message: '模型服务暂时不可用' }))
    setup()
    select(screen.getByTestId('en'))
    fireEvent.click(screen.getByRole('button', { name: /翻译/ }))
    await waitFor(() => expect(screen.getByRole('dialog').textContent).toContain('模型服务暂时不可用'))
    fireEvent.click(screen.getByRole('button', { name: '重试' }))
    await waitFor(() => expect(screen.getByRole('dialog').textContent).toContain('层叠网络中的故障扩散。'))
    expect(mocks.stream).toHaveBeenLastCalledWith('p1', expect.any(String), expect.anything(), expect.anything(), true, undefined)

    cleanup()
    mocks.stream.mockImplementationOnce(async (_p: string, _t: string, h: { onError: (e: unknown) => void }) => h.onError({ code: 'no_api_key', message: 'x' }))
    setup()
    select(screen.getByTestId('en'))
    fireEvent.click(screen.getByRole('button', { name: /翻译/ }))
    await waitFor(() => expect(screen.getByRole('dialog').textContent).toContain('尚未保存 API Key'))
    expect(screen.queryByRole('button', { name: '重试' })).toBeNull()
  })
})
