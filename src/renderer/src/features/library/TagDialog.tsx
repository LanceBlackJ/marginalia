import { useEffect, useMemo, useState } from 'react'
import { X } from 'lucide-react'
import { Button, Dialog, Input, toast } from '@/components/ui'
import { api, errorMessage } from '@/lib/api'
import { kbApi } from '@/lib/api.discovery'
import type { KbTopic } from '@/lib/types.discovery'
import { hasTag, normalizeTag, TAG_MAX } from '@/lib/tags'
import type { PaperSummary, PaperTag } from '@/lib/types'

/** 标签编辑：输入名字回车添加（没有就新建），已有标签自动补全；标签就是知识库里的主题。 */
export function TagDialog({
  paper,
  onClose,
  onChange
}: {
  paper: PaperSummary | null
  onClose: () => void
  onChange: (paperId: string, tags: PaperTag[]) => void
}) {
  const [tags, setTags] = useState<PaperTag[]>([])
  const [all, setAll] = useState<KbTopic[]>([])
  const [text, setText] = useState('')
  const [busy, setBusy] = useState(false)

  useEffect(() => {
    if (!paper) return
    setTags(paper.tags ?? [])
    setText('')
    let alive = true
    kbApi
      .topics()
      .then((r) => alive && setAll(r.items))
      .catch(() => undefined) // 补全拿不到不影响添加
    return () => {
      alive = false
    }
  }, [paper])

  const suggestions = useMemo(() => {
    const q = text.trim().toLowerCase()
    const have = new Set(tags.map((t) => t.id))
    return all.filter((t) => !have.has(t.id) && (!q || t.name.toLowerCase().includes(q))).slice(0, 8)
  }, [all, tags, text])

  if (!paper) return null

  const commit = (next: PaperTag[]): void => {
    setTags(next)
    onChange(paper.id, next)
  }

  const add = async (raw: string): Promise<void> => {
    if (!raw.trim()) return
    const name = normalizeTag(raw)
    if (!name) {
      toast.error('标签太长', `最多 ${TAG_MAX} 个字符。`)
      return
    }
    if (hasTag(tags, name)) {
      setText('')
      return
    }
    setBusy(true)
    try {
      const tag = await api.papers.addTag(paper.id, name)
      commit([...tags, tag].sort((a, b) => a.name.localeCompare(b.name)))
      if (!all.some((t) => t.id === tag.id)) setAll((a) => [...a, { id: tag.id, name: tag.name } as KbTopic])
      setText('')
    } catch (err) {
      toast.error('添加标签失败', errorMessage(err))
    } finally {
      setBusy(false)
    }
  }

  const remove = async (tag: PaperTag): Promise<void> => {
    try {
      await api.papers.removeTag(paper.id, tag.id)
      commit(tags.filter((t) => t.id !== tag.id))
    } catch (err) {
      toast.error('移除标签失败', errorMessage(err))
    }
  }

  return (
    <Dialog open onClose={onClose} title="标签" width={460} footer={<Button onClick={onClose}>完成</Button>}>
      <div className="pd-tagedit">
        <div className="pd-tagedit-paper" title={paper.title}>
          {paper.title}
        </div>
        <div className="pd-taglist" aria-label="已有标签">
          {tags.length === 0 && <span className="pd-weak-text">还没有标签</span>}
          {tags.map((t) => (
            <span key={t.id} className="pd-ptag">
              {t.name}
              <button type="button" className="pd-ptag-x" aria-label={`移除标签 ${t.name}`} onClick={() => void remove(t)}>
                <X size={11} />
              </button>
            </span>
          ))}
        </div>
        <Input
          value={text}
          maxLength={TAG_MAX}
          placeholder="输入标签，回车添加"
          aria-label="添加标签"
          disabled={busy}
          onChange={setText}
          onEnter={() => void add(text)}
        />
        {suggestions.length > 0 && (
          <div className="pd-taglist" aria-label="已有的标签">
            {suggestions.map((t) => (
              <button key={t.id} type="button" className="pd-ptag pd-ptag--btn" onClick={() => void add(t.name)}>
                + {t.name}
              </button>
            ))}
          </div>
        )}
        <p className="pd-aux pd-weak-text">标签同时是知识库里的“主题”，在知识库中也能看到。</p>
      </div>
    </Dialog>
  )
}
