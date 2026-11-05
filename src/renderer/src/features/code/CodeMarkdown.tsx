import { memo, useMemo } from 'react'
import ReactMarkdown, { defaultUrlTransform } from 'react-markdown'
import remarkGfm from 'remark-gfm'
import { openExternal } from '@/lib/native'
import type { CodeEvidence } from '@/lib/types.code'
import { citationsToLinks, dimRawCitations } from './codeUtils'
import { useCodeStore } from './codeStore'

interface Props {
  text: string
  /** 流式输出中：模型原始的引用标记还没被核实改写，先弱化显示 */
  streaming?: boolean
  /** 点击 [L#] 证据标记 */
  onCite?: (ref: number) => void
  /** 没通过核实的证据序号，显示成划线 */
  invalid?: number[]
  /** 证据列表：用来区分代码依据和论文依据（标记的样子不同） */
  evidence?: CodeEvidence[]
}

/** 渲染带 [L#] 代码证据标记的 Markdown。证据标记是可点击的小标签，点击后定位到真实的文件与行号。 */
export const CodeMarkdown = memo(function CodeMarkdown({ text, streaming, onCite, invalid, evidence }: Props) {
  const files = useCodeStore((s) => s.files)
  const openFile = useCodeStore((s) => s.openFile)
  const known = useMemo(() => new Set(files.map((f) => f.path)), [files])
  const evOf = useMemo(() => new Map((evidence ?? []).map((e) => [e.ref, e])), [evidence])
  const source = useMemo(() => (streaming ? dimRawCitations(text) : citationsToLinks(text)), [text, streaming])
  const bad = useMemo(() => new Set(invalid ?? []), [invalid])
  return (
    <div className="pd-md pd-cmd">
      <ReactMarkdown
        remarkPlugins={[remarkGfm]}
        urlTransform={(url) => (url.startsWith('lref:') ? url : defaultUrlTransform(url))}
        components={{
          code({ className, children }) {
            const t = String(children ?? '')
            // 行内代码正好是仓库里的一个文件路径：点击在左边打开它（阅读顺序里的文件名可以直接点）
            if (!className && !t.includes('\n') && known.has(t)) {
              return (
                <button type="button" className="pd-filelink" title="在左侧打开这个文件" onClick={() => void openFile(t)}>
                  {t}
                </button>
              )
            }
            return <code className={className}>{children}</code>
          },
          a({ href, children }) {
            if (href?.startsWith('lref:')) {
              const refs = href.slice(5).split(',').map(Number).filter((n) => n > 0)
              return (
                <span className="pd-lrefs">
                  {refs.map((r) => (
                    <button
                      key={r}
                      type="button"
                      className="pd-lref"
                      data-kind={evOf.get(r)?.kind}
                      data-invalid={bad.has(r) || undefined}
                      title={bad.has(r) ? `依据 ${r} 没有通过核实` : evOf.get(r)?.kind === 'paper' ? '在论文里查看这一段' : `查看依据 L${r}`}
                      onClick={() => onCite?.(r)}
                    >
                      {evOf.get(r)?.kind === 'paper' ? `论文${evOf.get(r)?.page ? ` 第${evOf.get(r)?.page}页` : ''}` : `L${r}`}
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
