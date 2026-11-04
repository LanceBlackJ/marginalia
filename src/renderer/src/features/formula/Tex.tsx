import { useMemo } from 'react'
import katex from 'katex'

const OPTIONS = { throwOnError: false, strict: 'ignore', errorColor: 'currentColor', trust: false } as const

/** 用 KaTeX 渲染一段 LaTeX；写错的公式原样显示源码，不会抛出。 */
export function Tex({ latex, display = false, className }: { latex: string; display?: boolean; className?: string }) {
  const html = useMemo(() => {
    try {
      return katex.renderToString(latex, { ...OPTIONS, displayMode: display })
    } catch {
      return null
    }
  }, [latex, display])
  if (html === null) return <code className={className}>{latex}</code>
  return <span className={className} dangerouslySetInnerHTML={{ __html: html }} />
}

/** 一段纯文字里夹着 `$…$` 行内公式（如“AI 推断”条目）：公式用 KaTeX 渲染，其余原样。 */
export function MathText({ text }: { text: string }) {
  const parts = text.split(/(\$[^$\n]+\$)/g)
  return (
    <>
      {parts.map((p, i) => (i % 2 === 1 ? <Tex key={i} latex={p.slice(1, -1).trim()} /> : p))}
    </>
  )
}
