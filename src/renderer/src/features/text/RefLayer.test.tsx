// @vitest-environment jsdom
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import type { RefItem } from '@/lib/types.references'

const mocks = vi.hoisted(() => ({ stream: vi.fn(), resolve: vi.fn(), contexts: vi.fn(), summarize: vi.fn(), jump: vi.fn(), open: vi.fn() }))

vi.mock('@/lib/api.references', () => ({
  referencesApi: { list: vi.fn(), citations: vi.fn(), contexts: mocks.contexts, resolve: mocks.resolve, summarize: mocks.summarize, importMetadata: vi.fn() }
}))
vi.mock('@/lib/translateText', async () => ({ ...(await vi.importActual<object>('@/lib/translateText')), streamTranslate: mocks.stream }))
vi.mock('@/lib/native', () => ({ openExternal: mocks.open }))
vi.mock('@/features/reader/readerStore', () => ({ useReaderStore: Object.assign(() => null, { getState: () => ({ jumpToOrd: mocks.jump }) }) }))
vi.mock('@/components/Markdown', () => ({ Markdown: ({ text }: { text: string }) => <div data-testid="md">{text}</div> }))

import { RefLayer } from './RefLayer'
import { RefMarker } from './RefMarker'
import { useRefStore } from './refStore'

const item: RefItem = {
  index: 1, ord: 5, page: 7, ords: [5], raw: 'Rivera, A. & Chen, L. Cascade failure in layered networks. Phys. Rev. E 80, 41 (2009).',
  authors: ['Rivera, A.', 'Chen, L.'], title: 'Cascade failure in layered networks', year: 2009, venue: 'Phys. Rev. E 80, 41', doi: '', arxiv: '', url: '',
  merged: false, messy: false
}

function setup() {
  useRefStore.setState({
    paperId: 'p1', status: 'ready', style: 'numeric', refs: [item], byIndex: new Map([[1, item]]), ordToIndex: new Map([[5, 1]]),
    valid: new Set([1]), paraRefs: new Map(), popover: null, menu: null, drawer: null, resolutions: {}, summaries: {}, contexts: {}, abstractTr: {}
  })
  return render(
    <MemoryRouter>
      <p>
        as shown before{' '}
        <RefMarker refs={[1]} sup>
          1
        </RefMarker>
      </p>
      <RefLayer />
    </MemoryRouter>
  )
}

beforeEach(() => {
  mocks.contexts.mockResolvedValue({ items: [{ ord: 12, page: 2, sentence: 'Failures spread across layered networks.' }] })
  mocks.resolve.mockResolvedValue({ index: 1, found: true, title: 'Cascade Failure in Layered Networks', authors: ['Alex Rivera'], year: 2009, venue: 'Physical Review E', doi: '10.1103/x', abstract_reason: '查到了题录，但 OpenAlex、Crossref、Semantic Scholar 都没有这篇的摘要。', match: { score: 0.97, level: 'high', reason: '标题相似度 0.97' } })
})
afterEach(() => {
  cleanup()
  vi.clearAllMocks()
})

describe('reference layer', () => {
  it('click opens a popover with the original text, the citing sentence and the three actions', async () => {
    setup()
    fireEvent.click(screen.getByRole('button', { name: '参考文献 1' }))
    const dialog = await screen.findByRole('dialog', { name: '参考文献 1' })
    expect(dialog.textContent).toContain('Cascade failure in layered networks')
    await waitFor(() => expect(dialog.textContent).toContain('Failures spread across layered networks.'))
    for (const name of ['查看摘要', 'AI 概括', '更多']) expect(screen.getByRole('button', { name })).toBeTruthy()
    fireEvent.keyDown(document, { key: 'Escape' })
    await waitFor(() => expect(screen.queryByRole('dialog', { name: '参考文献 1' })).toBeNull())
  })

  it('right click (and the keyboard menu key) opens the menu; entries that cannot work are disabled', () => {
    setup()
    fireEvent.contextMenu(screen.getByRole('button', { name: '参考文献 1' }))
    expect(screen.getByRole('menu')).toBeTruthy()
    expect(screen.getByRole('menuitem', { name: '读摘要' })).toBeTruthy()
    expect(screen.getByRole('menuitem', { name: '让 AI 概括它做了什么' })).toBeTruthy()
    expect((screen.getByRole('menuitem', { name: /在浏览器打开/ }) as HTMLButtonElement).disabled).toBe(true) // 没有链接
    expect((screen.getByRole('menuitem', { name: /加入文库/ }) as HTMLButtonElement).disabled).toBe(true) // 没有 DOI
    fireEvent.keyDown(document, { key: 'Escape' })
    expect(screen.queryByRole('menu')).toBeNull()
    fireEvent.keyDown(screen.getByRole('button', { name: '参考文献 1' }), { key: 'F10', shiftKey: true })
    expect(screen.getByRole('menu')).toBeTruthy()
  })

  it('the abstract view looks it up once, shows the match level and says plainly when there is no abstract', async () => {
    let release!: (v: unknown) => void
    mocks.resolve.mockReturnValueOnce(new Promise((r) => (release = r)))
    setup()
    fireEvent.contextMenu(screen.getByRole('button', { name: '参考文献 1' }))
    fireEvent.click(screen.getByRole('menuitem', { name: '读摘要' }))
    const drawer = await screen.findByRole('complementary', { name: '参考文献 1 详情' })
    // 查询期间：说明联网查询的对象和发送的内容
    expect(drawer.textContent).toContain('正在查询 OpenAlex / Crossref')
    expect(drawer.textContent).toContain('不发送论文正文')
    release({ index: 1, found: true, title: 'Cascade Failure in Layered Networks', authors: ['Alex Rivera'], year: 2009, venue: 'Physical Review E', doi: '10.1103/x', abstract_reason: '查到了题录，但 OpenAlex、Crossref、Semantic Scholar 都没有这篇的摘要。', match: { score: 0.97, level: 'high', reason: '标题相似度 0.97' } })
    await waitFor(() => expect(drawer.textContent).toContain('没有这篇的摘要'))
    expect(drawer.textContent).toContain('匹配可信度高')
    expect(mocks.resolve).toHaveBeenCalledTimes(1)
    // 没有摘要：AI 概括按钮不可用，不会发出概括请求
    expect((screen.getByRole('button', { name: 'AI 概括它做了什么' }) as HTMLButtonElement).disabled).toBe(true)
    expect(mocks.summarize).not.toHaveBeenCalled()
  })

  it('low confidence matches are called out and summaries stream in with an AI label', async () => {
    mocks.resolve.mockResolvedValue({ index: 1, found: true, title: 'Quantum dots', authors: ['W Zhang'], year: 2010, abstract: 'Dots absorb light.', abstract_source: 'openalex', match: { score: 0.3, level: 'low', reason: '标题相似度 0.30' } })
    mocks.summarize.mockImplementation(async (_p: string, _i: number, onEvent: (e: string, d: unknown) => void) => {
      onEvent('meta', { notes: ['查到的题录与原文参考文献相似度不高，可能不是同一篇，请对照原文确认后再参考下面的概括。'] })
      onEvent('delta', { text: '## 这篇参考文献做了什么\n量子点吸收光。（摘要）' })
      onEvent('done', { summary: '## 这篇参考文献做了什么\n量子点吸收光。（摘要）', model: 'fast-m', label: 'AI 概括，依据：摘要 + 本文引用处', match: { level: 'low' }, notes: ['查到的题录与原文参考文献相似度不高，可能不是同一篇，请对照原文确认后再参考下面的概括。'] })
    })
    setup()
    fireEvent.click(screen.getByRole('button', { name: '参考文献 1' }))
    fireEvent.click(await screen.findByRole('button', { name: 'AI 概括' }))
    const drawer = await screen.findByRole('complementary', { name: '参考文献 1 详情' })
    await waitFor(() => expect(screen.getByTestId('md').textContent).toContain('量子点吸收光'))
    expect(drawer.textContent).toContain('可能不是同一篇')
    expect(drawer.textContent).toContain('AI 生成')
    expect(mocks.summarize).toHaveBeenCalledWith('p1', 1, expect.any(Function), expect.anything())
  })

  it('a reference without an abstract is refused by the backend and the drawer offers the original instead', async () => {
    mocks.summarize.mockImplementation(async (_p: string, _i: number, onEvent: (e: string, d: unknown) => void) => {
      onEvent('done', { summary: null, reason: 'no_abstract', message: '没有可用摘要，无法概括。可以去查看原文。', link: 'https://doi.org/10.1/x', model: null, match: null })
    })
    setup()
    fireEvent.contextMenu(screen.getByRole('button', { name: '参考文献 1' }))
    fireEvent.click(screen.getByRole('menuitem', { name: '让 AI 概括它做了什么' }))
    const drawer = await screen.findByRole('complementary', { name: '参考文献 1 详情' })
    await waitFor(() => expect(drawer.textContent).toContain('没有可用摘要，无法概括'))
    fireEvent.click(screen.getByRole('button', { name: '去查看原文' }))
    expect(mocks.open).toHaveBeenCalledWith('https://doi.org/10.1/x')
  })
})

describe('abstract that does not match the title', () => {
  it('shows why a replaced abstract was swapped, with the source of the one in use', async () => {
    mocks.resolve.mockResolvedValue({
      index: 1, found: true, title: 'Mastering the game of go without human knowledge', abstract: 'A program that masters a game from scratch.', abstract_source: 'semantic_scholar',
      abstract_notes: ['OpenAlex 的摘要与这篇的标题不相符，已忽略，改用 Semantic Scholar 的摘要。'], match: { score: 1, level: 'high', reason: '标题相似度 1.00' }
    })
    setup()
    fireEvent.contextMenu(screen.getByRole('button', { name: '参考文献 1' }))
    fireEvent.click(screen.getByRole('menuitem', { name: '读摘要' }))
    const drawer = await screen.findByRole('complementary', { name: '参考文献 1 详情' })
    await waitFor(() => expect(drawer.textContent).toContain('A program that masters a game from scratch.'))
    expect(drawer.textContent).toContain('摘要来源：semantic_scholar')
    expect(drawer.textContent).toContain('OpenAlex 的摘要与这篇的标题不相符，已忽略，改用 Semantic Scholar 的摘要。')
  })

  it('when every abstract is ignored it says so and does not offer a summary', async () => {
    mocks.resolve.mockResolvedValue({
      index: 1, found: true, title: 'Mastering the game of go without human knowledge', abstract: null, abstract_mismatch: true,
      abstract_reason: '检索到的摘要与这篇的标题不相符，已忽略；请去原文查看。', abstract_notes: ['OpenAlex 返回的摘要与标题不相符，已忽略。'],
      match: { score: 1, level: 'high', reason: '标题相似度 1.00' }
    })
    setup()
    fireEvent.contextMenu(screen.getByRole('button', { name: '参考文献 1' }))
    fireEvent.click(screen.getByRole('menuitem', { name: '读摘要' }))
    const drawer = await screen.findByRole('complementary', { name: '参考文献 1 详情' })
    await waitFor(() => expect(drawer.textContent).toContain('与这篇的标题不相符，已忽略；请去原文查看'))
    expect(drawer.textContent).toContain('OpenAlex 返回的摘要与标题不相符，已忽略。')
    expect((screen.getByRole('button', { name: 'AI 概括它做了什么' }) as HTMLButtonElement).disabled).toBe(true)
    expect(mocks.summarize).not.toHaveBeenCalled()
  })
})

describe('translate the abstract', () => {
  const abs = { index: 1, found: true, title: 'Cascade Failure in Layered Networks', abstract: 'Failures spread across layered networks in surprising ways.', abstract_source: 'openalex', match: { score: 0.97, level: 'high', reason: 'ok' } }
  async function open() {
    setup()
    fireEvent.contextMenu(screen.getByRole('button', { name: '参考文献 1' }))
    fireEvent.click(screen.getByRole('menuitem', { name: '读摘要' }))
    return screen.findByRole('complementary', { name: '参考文献 1 详情' })
  }

  it('shows the button, streams the translation under the English and does not translate twice', async () => {
    mocks.resolve.mockResolvedValue(abs)
    mocks.stream.mockImplementation(async (_p: string, _t: string, h: { onDelta: (d: string) => void; onDone: (r: unknown) => void }) => {
      h.onDelta('故障')
      h.onDone({ translation: '故障在层叠网络中扩散。', model: 'm1', cached: true })
    })
    const drawer = await open()
    fireEvent.click(await screen.findByRole('button', { name: '翻译摘要' }))
    await waitFor(() => expect(drawer.textContent).toContain('故障在层叠网络中扩散。'))
    expect(drawer.textContent).toContain('已缓存')
    expect(screen.queryByRole('button', { name: '翻译摘要' })).toBeNull()
    await useRefStore.getState().translateAbstract(1)
    expect(mocks.stream).toHaveBeenCalledTimes(1)
    expect(mocks.stream).toHaveBeenCalledWith('p1', abs.abstract, expect.anything(), expect.anything(), false)
    expect(drawer.querySelector('.pd-refdrawer-abs')?.hasAttribute('data-translatable')).toBe(true)
  })

  it('failure shows the reason and a retry', async () => {
    mocks.resolve.mockResolvedValue(abs)
    mocks.stream.mockImplementationOnce(async (_p: string, _t: string, h: { onError: (e: unknown) => void }) => h.onError({ code: 'upstream', message: '模型服务暂时不可用' }))
    mocks.stream.mockImplementationOnce(async (_p: string, _t: string, h: { onDone: (r: unknown) => void }) => h.onDone({ translation: '好了。', model: 'm1', cached: false }))
    const drawer = await open()
    fireEvent.click(await screen.findByRole('button', { name: '翻译摘要' }))
    await waitFor(() => expect(drawer.textContent).toContain('模型服务暂时不可用'))
    fireEvent.click(screen.getByRole('button', { name: '重试' }))
    await waitFor(() => expect(drawer.textContent).toContain('好了。'))
  })

  it('no button without a trusted abstract (none, or ignored as mismatched)', async () => {
    mocks.resolve.mockResolvedValue({ ...abs, abstract: null, abstract_mismatch: true, abstract_reason: '检索到的摘要与这篇的标题不相符，已忽略；请去原文查看。' })
    const drawer = await open()
    await waitFor(() => expect(drawer.textContent).toContain('已忽略'))
    expect(screen.queryByRole('button', { name: '翻译摘要' })).toBeNull()
    expect(mocks.stream).not.toHaveBeenCalled()
  })
})

describe('menu and the select-translate overlay', () => {
  it('clicking the overlay keeps the menu open, and Esc closes the overlay before the menu', async () => {
    mocks.stream.mockImplementation(async () => new Promise(() => {}))
    setup()
    const ui = document.createElement('div')
    ui.setAttribute('data-selecttranslate-ui', '')
    document.body.appendChild(ui)
    fireEvent.contextMenu(screen.getByRole('button', { name: '参考文献 1' }))
    expect(screen.getByRole('menu')).toBeTruthy()
    fireEvent.pointerDown(ui)
    expect(screen.queryByRole('menu')).toBeTruthy()
    // 真实的译文浮层：选中英文并按 T，再按 Esc，菜单应还在
    const p = document.createElement('p')
    p.setAttribute('data-translatable', '')
    p.textContent = 'Failures spread across layered networks.'
    document.body.appendChild(p)
    Range.prototype.getBoundingClientRect = () => ({ left: 1, right: 2, top: 1, bottom: 2, width: 1, height: 1, x: 1, y: 1, toJSON: () => ({}) })
    ui.remove()
    const s = window.getSelection()!
    const r = document.createRange()
    r.selectNodeContents(p)
    s.removeAllRanges()
    s.addRange(r)
    document.dispatchEvent(new Event('selectionchange'))
    await waitFor(() => expect(document.querySelector('[data-selecttranslate-ui]')).toBeTruthy())
    fireEvent.keyDown(document, { key: 't' })
    await waitFor(() => expect(screen.getByRole('dialog', { name: '译文' })).toBeTruthy())
    fireEvent.keyDown(document, { key: 'Escape' })
    expect(screen.queryByRole('dialog', { name: '译文' })).toBeNull()
    expect(screen.queryByRole('menu')).toBeTruthy()
    fireEvent.keyDown(document, { key: 'Escape' })
    expect(screen.queryByRole('menu')).toBeNull()
    p.remove()
  })
})
