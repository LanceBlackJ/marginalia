import { useEffect, useState } from 'react'
import { Copy } from 'lucide-react'
import { Button, Dialog, Segmented, Skeleton, toast } from '@/components/ui'
import { errorMessage } from '@/lib/api'
import { CITATION_FORMATS, exportApi2, type CitationFormat } from '@/lib/api.export'

const KEY = 'pd.cite.format'
const ORDER: CitationFormat[] = ['gbt7714', 'apa', 'mla', 'ieee', 'bibtex', 'ris']

function lastFormat(): CitationFormat {
  try {
    const v = localStorage.getItem(KEY)
    if (v && ORDER.includes(v as CitationFormat)) return v as CitationFormat
  } catch {
    /* 存储不可用时用默认格式 */
  }
  return 'gbt7714'
}

/** 一篇论文的引用：选一个格式，直接看到生成好的引用文字，一键复制。只用论文已有的题录信息，缺的项不会编造。 */
export function CiteDialog({ paperId, title, onClose }: { paperId: string | null; title?: string; onClose: () => void }) {
  const [format, setFormat] = useState<CitationFormat>(lastFormat)
  const [text, setText] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    if (!paperId) return
    let alive = true
    setText(null)
    setError(null)
    exportApi2
      .citations({ format, paper_ids: [paperId] })
      .then((r) => {
        if (alive) setText(r.content.trim())
      })
      .catch((err) => {
        if (alive) setError(errorMessage(err))
      })
    return () => {
      alive = false
    }
  }, [paperId, format])

  const choose = (f: CitationFormat): void => {
    setFormat(f)
    try {
      localStorage.setItem(KEY, f)
    } catch {
      /* 只是不记住 */
    }
  }

  const copy = async (): Promise<void> => {
    if (!text) return
    try {
      await navigator.clipboard.writeText(text)
      toast.success('已复制引用')
    } catch {
      toast.error('复制失败', '请手动选中文字复制')
    }
  }

  return (
    <Dialog
      open={paperId !== null}
      onClose={onClose}
      title="引用这篇论文"
      width={620}
      footer={
        <>
          <Button onClick={onClose}>关闭</Button>
          <Button variant="primary" icon={<Copy size={14} />} disabled={!text} onClick={() => void copy()}>
            复制
          </Button>
        </>
      }
    >
      {title && <p className="pd-aux pd-muted pd-ellipsis" title={title} style={{ marginBottom: 12 }}>{title}</p>}
      <Segmented
        value={format}
        options={ORDER.map((f) => ({ value: f, label: CITATION_FORMATS.find((c) => c.value === f)?.label.replace(' 第 7 版', '').replace(' 第 9 版', '') ?? f }))}
        onChange={choose}
        aria-label="引用格式"
      />
      <div style={{ marginTop: 12 }}>
        {error ? (
          <div className="pd-field-error">{error}</div>
        ) : text === null ? (
          <Skeleton height={64} />
        ) : (
          <pre
            aria-label="引用文字"
            style={{ margin: 0, padding: 12, whiteSpace: 'pre-wrap', wordBreak: 'break-word', userSelect: 'text', border: '1px solid var(--border)', borderRadius: 'var(--r-card)', font: 'inherit', fontSize: 'var(--fs-small)', lineHeight: 1.7 }}
          >
            {text}
          </pre>
        )}
      </div>
    </Dialog>
  )
}
