// @vitest-environment jsdom
import { afterEach, describe, expect, it } from 'vitest'
import katex from 'katex'
import { latexFromRange } from './copyLatex'

afterEach(() => {
  document.body.innerHTML = ''
})

function mount(html: string): HTMLElement {
  const root = document.createElement('div')
  root.innerHTML = html
  document.body.appendChild(root)
  return root
}
const tex = (s: string, display = false): string => katex.renderToString(s, { displayMode: display, throwOnError: false })

describe('复制公式得到 LaTeX 源码', () => {
  it('只选中一个公式：得到不带 $ 的 LaTeX 代码', () => {
    const root = mount(`<p>where <span id="f">${tex('R_{(i,i+n)}=\sum_{k=i}^{i+n}R_k')}</span> is the return</p>`)
    const r = document.createRange()
    r.selectNode(root.querySelector('#f')!)
    expect(latexFromRange(r)).toBe('R_{(i,i+n)}=\sum_{k=i}^{i+n}R_k')
  })

  it('文字里夹着公式：公式变成 $…$，文字不变', () => {
    const root = mount(`<p id="p">Here <span>${tex('N')}</span> nodes and <span>${tex('v_i\in\mathcal{V}')}</span> denotes it.</p>`)
    const r = document.createRange()
    r.selectNodeContents(root.querySelector('#p')!)
    expect(latexFromRange(r)).toBe('Here $N$ nodes and $v_i\in\mathcal{V}$ denotes it.')
  })

  it('选区只落在公式的一部分里：按整个公式处理', () => {
    const root = mount(`<p>x <span id="f">${tex('\sigma(G)')}</span> y</p>`)
    const inner = root.querySelector('.katex-html')!.firstElementChild!
    const r = document.createRange()
    r.selectNodeContents(inner)
    expect(latexFromRange(r)).toBe('\sigma(G)')
  })

  it('独占一行的公式在混合文字里用 $$…$$', () => {
    const root = mount(`<div id="d">Eq: ${tex('a^2+b^2=c^2', true)} done</div>`)
    const r = document.createRange()
    r.selectNodeContents(root.querySelector('#d')!)
    expect(latexFromRange(r)).toBe('Eq: $$a^2+b^2=c^2$$ done')
  })

  it('没有公式的选区不拦截', () => {
    const root = mount('<p id="p">plain text only</p>')
    const r = document.createRange()
    r.selectNodeContents(root.querySelector('#p')!)
    expect(latexFromRange(r)).toBeNull()
    expect(latexFromRange(document.createRange())).toBeNull()
  })
})
