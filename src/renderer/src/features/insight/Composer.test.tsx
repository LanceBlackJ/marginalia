// @vitest-environment jsdom
import { afterEach, describe, expect, it } from 'vitest'
import { cleanup, fireEvent, render, waitFor } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { useReaderStore } from '@/features/reader/readerStore'
import { Composer } from './Composer'

afterEach(() => {
  cleanup()
  useReaderStore.setState({ contextSelection: null, selection: null })
})

const view = () =>
  render(
    <MemoryRouter>
      <Composer />
    </MemoryRouter>
  )

describe('Composer 紧凑输入区', () => {
  it('没有选中内容时，不渲染选中标签，也不占快捷操作那一行', () => {
    const { container } = view()
    expect(container.querySelector('.pd-quick')).toBeNull()
    expect(container.querySelector('.pd-chip')).toBeNull()
    expect(container.querySelector('.pd-composer-ctx')).toBeNull()
    expect((container.querySelector('textarea') as HTMLTextAreaElement).rows).toBe(2)
  })

  it('选中的文字只出现一次，快捷操作和它在同一行', () => {
    useReaderStore.setState({ contextSelection: { text: 'giant connected component', page: 1 } })
    const { container } = view()
    expect(container.textContent!.split('giant connected component').length - 1).toBe(1)
    const row = container.querySelector('.pd-composer-ctx')!
    expect(row.querySelector('.pd-chip--sel')).toBeTruthy()
    expect(row.querySelectorAll('.pd-quick-btn').length).toBe(4)
    expect(Array.from(row.querySelectorAll('.pd-quick-btn')).map((b) => b.textContent)).toEqual(['解释', '总结', '术语', '设计动机'])
  })

  it('论文里刚选中的文字也能直接移除（点 × 同时清掉选区）', () => {
    useReaderStore.setState({ contextSelection: null, selection: { text: '刚选中的文字', page: 2 } as never })
    const { container } = view()
    const x = container.querySelector('.pd-chip--sel button') as HTMLButtonElement
    expect(x).toBeTruthy()
    x.click()
    expect(useReaderStore.getState().selection).toBeNull()
  })

  it('@、联网、附件和发送在输入框盒子内部的同一排', () => {
    const { container } = view()
    const bar = container.querySelector('.pd-composer-box .pd-composer-bar')!
    expect(bar.querySelectorAll('button').length).toBe(4)
    expect(bar.querySelector('.pd-send')).toBeTruthy()
  })

  it('粘贴截图：加成附件（有缩略图），可以移除；只有附件没写字也能发送', async () => {
    const { container } = view()
    const ta = container.querySelector('textarea') as HTMLTextAreaElement
    const shot = new File([new Uint8Array([137, 80, 78, 71])], 'image.png', { type: 'image/png' })
    fireEvent.paste(ta, { clipboardData: { files: [shot], getData: () => '' } })
    await waitFor(() => expect(container.querySelector('.pd-attach img')).toBeTruthy())
    expect((container.querySelector('.pd-send') as HTMLButtonElement).disabled).toBe(false) // 没写字，有附件就能发
    fireEvent.click(container.querySelector('.pd-attach button')!)
    expect(container.querySelector('.pd-attach')).toBeNull()
    expect((container.querySelector('.pd-send') as HTMLButtonElement).disabled).toBe(true)
  })

  it('粘贴普通文字照常，不当成附件', () => {
    const { container } = view()
    const ta = container.querySelector('textarea') as HTMLTextAreaElement
    fireEvent.paste(ta, { clipboardData: { files: [], getData: () => '一段文字' } })
    expect(container.querySelector('.pd-attach')).toBeNull()
  })
})
