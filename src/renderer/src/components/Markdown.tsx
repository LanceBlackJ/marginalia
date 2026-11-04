import { Component, memo, useMemo, type ReactNode } from 'react'
import ReactMarkdown, { defaultUrlTransform } from 'react-markdown'
import remarkGfm from 'remark-gfm'
import remarkMath from 'remark-math'
import rehypeKatex from 'rehype-katex'
import 'katex/dist/katex.min.css'
import { citationsToMarkdownLinks, parseCiteHrefRefs } from '@/lib/citations'
import { normalizeMath } from '@/features/formula/math'
import { openExternal } from '@/lib/native'
import { buildEvidenceMap, buildInvalidSet, CiteGroup } from './CiteChip'
import type { Evidence } from '@/lib/types'

interface MarkdownProps {
  text: string
  evidence?: Evidence[]
  /** 模型引用了但在论文中找不到的段落序号（正文） */
  invalid?: number[]
  /** 全部无法核实的引用标记（含补充材料，如 "S1P99"） */
  invalidRefs?: string[]
}

/** KaTeX：公式写错时不抛异常，原样显示源码（不用红色，保持黑白）。 */
const KATEX_OPTIONS = { throwOnError: false, strict: 'ignore', errorColor: 'currentColor', trust: false } as const

/** 万一公式渲染抛出了未预期的异常，退回显示原文，而不是让整张卡片崩掉。 */
class MathBoundary extends Component<{ fallback: ReactNode; children: ReactNode }, { failed: boolean }> {
  state = { failed: false }
  static getDerivedStateFromError(): { failed: boolean } {
    return { failed: true }
  }
  render(): ReactNode {
    return this.state.failed ? this.props.fallback : this.props.children
  }
}

/** 渲染带 [P12] / [S1P12] 引用标记的 Markdown：引用变成可点击的原文定位 chip；数学公式用 KaTeX 渲染。 */
export const Markdown = memo(function Markdown({ text, evidence, invalid, invalidRefs }: MarkdownProps) {
  const source = useMemo(() => citationsToMarkdownLinks(normalizeMath(text)), [text])
  const evMap = useMemo(() => buildEvidenceMap(evidence), [evidence])
  const invalidSet = useMemo(() => buildInvalidSet(invalid, invalidRefs), [invalid, invalidRefs])

  return (
    <div className="pd-md">
      <MathBoundary fallback={<pre className="pd-md-raw">{text}</pre>}>
      <ReactMarkdown
        remarkPlugins={[remarkGfm, remarkMath]}
        rehypePlugins={[[rehypeKatex, KATEX_OPTIONS]]}
        urlTransform={(url) => (url.startsWith('cite:') ? url : defaultUrlTransform(url))}
        components={{
          a({ href, children }) {
            const refs = parseCiteHrefRefs(href)
            if (refs) return <CiteGroup refs={refs} evidence={evMap} invalid={invalidSet} />
            return (
              <a
                href={href}
                onClick={(e) => {
                  e.preventDefault()
                  if (href) void openExternal(href)
                }}
              >
                {children}
              </a>
            )
          }
        }}
      >
        {source}
      </ReactMarkdown>
      </MathBoundary>
    </div>
  )
})
