// @vitest-environment jsdom
import { useState } from 'react'
import { afterEach, describe, expect, it } from 'vitest'
import { cleanup, fireEvent, render } from '@testing-library/react'
import { isCompleteDisplayMath, isOpenBlock, joinBlocks, LiveNoteEditor, splitBlocks } from './LiveNote'

afterEach(cleanup)

describe('分块', () => {
  it('空行分块；围栏代码和没写完的 $$ 公式里的空行不分；拼回去不变', () => {
    expect(splitBlocks('第一段\n\n第二段')).toEqual(['第一段', '第二段'])
    expect(splitBlocks('a\n\n```\nx\n\ny\n```\n\nb')).toEqual(['a', '```\nx\n\ny\n```', 'b'])
    expect(splitBlocks('$$\na\n\nb\n$$')).toEqual(['$$\na\n\nb\n$$'])
    expect(splitBlocks('')).toEqual([''])
    expect(joinBlocks(splitBlocks('甲\n\n乙\n\n'))).toBe('甲\n\n乙\n\n')
    expect(isOpenBlock('```js\ncode')).toBe(true)
    expect(isCompleteDisplayMath('$$E=mc^2$$')).toBe(true)
    expect(isCompleteDisplayMath('$$E=mc^2')).toBe(false)
  })
})

function Host() {
  const [v, setV] = useState('')
  return (
    <div>
      <LiveNoteEditor value={v} onChange={setV} placeholder="写点什么…" />
      <output data-testid="out">{v}</output>
    </div>
  )
}

describe('边写边渲染', () => {
  it('空一行：上一段马上渲染成最终样子（没有预览按钮），光标去下一块', () => {
    const { container, getByTestId } = render(<Host />)
    let ta = container.querySelector('textarea')!
    fireEvent.change(ta, { target: { value: '这是**加粗**的一段' } })
    expect(container.querySelector('textarea')!.value).toBe('这是**加粗**的一段') // 还在写：显示源码
    fireEvent.change(container.querySelector('textarea')!, { target: { value: '这是**加粗**的一段\n\n' } })
    expect(container.querySelector('.pd-live-block strong')!.textContent).toBe('加粗') // 渲染好了
    ta = container.querySelector('textarea')!
    expect(ta.value).toBe('') // 下一块是空的、正在写
    fireEvent.change(ta, { target: { value: '第二段' } })
    expect(getByTestId('out').textContent).toBe('这是**加粗**的一段\n\n第二段')
    expect(container.textContent).not.toContain('预览')
  })

  it('写完 $$公式$$ 就渲染，并接着写下一块；点已渲染的块回到它的源码', () => {
    const { container } = render(<Host />)
    fireEvent.change(container.querySelector('textarea')!, { target: { value: '$$E=mc^2$$' } })
    expect(container.querySelector('.katex')).toBeTruthy()
    expect(container.querySelectorAll('textarea').length).toBe(1) // 正在写的是后面那个空块
    fireEvent.mouseDown(container.querySelector('.pd-live-block')!)
    expect(container.querySelector('textarea')!.value).toBe('$$E=mc^2$$')
  })
})
