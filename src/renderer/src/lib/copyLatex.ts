/**
 * 复制公式：在文字里选中一段包含公式的内容再复制，得到的是 LaTeX 源码，而不是 KaTeX 渲染出来的重复乱码。
 *  · 只选中了一个公式：复制它的 LaTeX 代码，不带 $ 符号，可以直接粘贴到 LaTeX、Word 公式框、Markdown 公式里；
 *  · 选中的文字里夹着公式：公式换成 $…$（独占一行的公式用 $$…$$），其余文字照旧，粘贴到 Markdown 或 LaTeX 里仍然是合法的。
 * 选区只落在公式的一部分里时，按整个公式处理。没有公式时返回 null，浏览器照常复制。
 */

const TEX = 'annotation[encoding="application/x-tex"]'

function texOf(el: Element): string | null {
  const t = el.querySelector(TEX)?.textContent
  return t && t.trim() ? t.trim() : null
}

function formulaAround(node: Node | null): Element | null {
  const el = node instanceof Element ? node : (node?.parentElement ?? null)
  return el?.closest('.katex') ?? null
}

export function latexFromRange(range: Range): string | null {
  if (range.collapsed) return null
  const r = range.cloneRange()
  // 选区的起点或终点落在某个公式里：把整个公式包进来
  const a = formulaAround(r.startContainer)
  const b = formulaAround(r.endContainer)
  if (a) r.setStartBefore(a.closest('.katex-display') ?? a)
  if (b) r.setEndAfter(b.closest('.katex-display') ?? b)
  const frag = r.cloneContents()
  const formulas = Array.from(frag.querySelectorAll('.katex'))
  if (formulas.length === 0) return null
  const rest = (frag.textContent ?? '').replace(/\s+/g, '')
  if (formulas.length === 1) {
    const only = formulas[0]
    const own = (only.textContent ?? '').replace(/\s+/g, '')
    const tex = texOf(only)
    if (tex && rest === own) return tex // 整个选区就是这一个公式
  }
  for (const f of formulas) {
    const tex = texOf(f)
    if (!tex) return null // 取不到源码就不拦截，免得复制出空的
    const block = f.closest('.katex-display')
    const target = block && frag.contains(block) ? block : f
    target.replaceWith(document.createTextNode(block ? `$$${tex}$$` : `$${tex}$`))
  }
  return frag.textContent
}

let installed = false

/** 全站只装一次：复制时如果选区里有公式，就把剪贴板的文字换成带 LaTeX 的版本。 */
export function installLatexCopy(): void {
  if (installed || typeof document === 'undefined') return
  installed = true
  document.addEventListener('copy', (e) => {
    const sel = window.getSelection()
    if (!sel || sel.rangeCount === 0 || !e.clipboardData) return
    const text = latexFromRange(sel.getRangeAt(0))
    if (text === null) return
    e.clipboardData.setData('text/plain', text)
    e.preventDefault()
  })
}
