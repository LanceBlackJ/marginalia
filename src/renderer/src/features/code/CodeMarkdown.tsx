import { memo, useMemo } from 'react'
import ReactMarkdown, { defaultUrlTransform } from 'react-markdown'
import remarkGfm from 'remark-gfm'
import { openExternal } from '@/lib/native'
import { citationsToLinks, dimRawCitations } from './codeUtils'

interface Props {
  text: string
  /** 流式输出中：模型原始的引用标记还没被核实改写，先弱化显示 */
  streaming?: boolean
  /** 点击 [L#] 证据标记 */
  onCite?: (ref: number) => void
  /** 没通过核实的证据序号，显示成划线 */
  invalid?: number[]
}

/** 渲染带 [L#] 代码证据标记的 Markdown。证据标记是可点击的小标签，点击后定位到真实的文件与行号。 */
export const CodeMarkdown = memo(function CodeMarkdown({ text, streaming, onCite, invalid }: Props) {
  const source = useMemo(() => (streaming ? dimRawCitations(text) : citationsToLinks(text)), [text, streaming])
  const bad = useMemo(() => new Set(invalid ?? []), [invalid])
  return (
    <div className="pd-md pd-cmd">
      <ReactMarkdown
        remarkPlugins={[remarkGfm]}
        urlTransform={(url) => (url.startsWith('lref:') ? url : defaultUrlTransform(url))}
        components={{
          a({ href, children }) {
            if (href?.startsWith('lref:')) {
              const refs = href.slice(5).split(',').map(Number).filter((n) => n > 0)
              return (
                <span className="pd-lrefs">
                  {refs.map((r) => (
                    <button key={r} type="button" className="pd-lref" data-invalid={bad.has(r) || undefined} title={bad.has(r) ? `证据 L${r} 没有通过核实` : `查看证据 L${r}`} onClick={() => onCite?.(r)}>
                      L{r}
                    </button>
                  ))}
                </span>
              )
            }
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
    </div>
  )
})
