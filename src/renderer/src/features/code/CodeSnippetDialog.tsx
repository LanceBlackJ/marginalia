import { useEffect, useState } from 'react'
import { Copy } from 'lucide-react'
import { Button, Dialog, Spinner, toast } from '@/components/ui'
import { codeApi } from '@/lib/api.code'
import { lineRange } from './codeUtils'
import { useCodeStore } from './codeStore'
import { highlightLines, languageForPath } from './highlight'

const MAX_LINES = 200
const fileCache = new Map<string, Promise<string | null>>()

/** 小窗：只显示对应的那一段代码（运行流程的节点、论文↔代码的关联）。要看上下文再点“在代码页打开”。 */
export function CodeSnippetDialog() {
  const snippet = useCodeStore((s) => s.snippet)
  const repoId = useCodeStore((s) => s.repoId)
  const close = useCodeStore((s) => s.closeSnippet)
  const openInCode = useCodeStore((s) => s.openSnippetInCode)
  const [lines, setLines] = useState<{ html: string[]; raw: string; total: number } | null>(null)
  const [state, setState] = useState<'loading' | 'ok' | 'none'>('loading')

  useEffect(() => {
    if (!snippet || !repoId) return
    let alive = true
    setState('loading')
    setLines(null)
    const key = `${repoId}:${snippet.path}`
    if (!fileCache.has(key)) fileCache.set(key, codeApi.file(repoId, snippet.path).then((f) => f.content, () => null))
    void fileCache.get(key)!.then((content) => {
      if (!alive) return
      if (content == null) return setState('none')
      const all = content.split('\n')
      const total = Math.max(1, snippet.b - snippet.a + 1)
      const part = all.slice(snippet.a - 1, Math.min(snippet.b, snippet.a - 1 + MAX_LINES))
      setLines({ html: highlightLines(part.join('\n'), languageForPath(snippet.path)), raw: part.join('\n'), total })
      setState('ok')
    })
    return () => {
      alive = false
    }
  }, [snippet, repoId])

  if (!snippet) return null
  const copy = (): void => {
    if (!lines) return
    void navigator.clipboard.writeText(lines.raw).then(() => toast.success('已复制这段代码'))
  }
  return (
    <Dialog
      open
      onClose={close}
      width={760}
      title={
        <span className="pd-snip-title">
          {snippet.title}
          <span className="pd-aux pd-muted">
            {snippet.path}:{lineRange(snippet.a, snippet.b)}
          </span>
        </span>
      }
      footer={
        <>
          <Button size="sm" variant="ghost" icon={<Copy size={13} />} disabled={!lines} onClick={copy}>
            复制
          </Button>
          <Button size="sm" onClick={() => void openInCode()}>
            在代码页打开
          </Button>
          <Button size="sm" variant="primary" onClick={close}>
            关闭
          </Button>
        </>
      }
    >
      {state === 'loading' && (
        <p className="pd-aux pd-muted">
          <Spinner size={14} /> 正在读取代码…
        </p>
      )}
      {state === 'none' && <p className="pd-aux pd-muted">这个文件的内容没有保存（二进制或过大），无法显示代码。</p>}
      {state === 'ok' && lines && (
        <div className="pd-snip-code">
          {lines.html.map((h, i) => (
            <div key={i} className="pd-code-line">
              <span className="pd-code-no">{snippet.a + i}</span>
              <span className="pd-code-text" dangerouslySetInnerHTML={{ __html: h === '' ? ' ' : h }} />
            </div>
          ))}
          {lines.total > MAX_LINES && <div className="pd-aux pd-muted pd-snip-more">… 还有 {lines.total - MAX_LINES} 行，点“在代码页打开”查看全部</div>}
        </div>
      )}
    </Dialog>
  )
}
