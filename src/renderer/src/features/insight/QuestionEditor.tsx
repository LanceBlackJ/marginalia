import { useEffect, useRef, useState } from 'react'
import { Paperclip, X } from 'lucide-react'
import { Button, IconButton, toast } from '@/components/ui'
import type { AskIntent, InsightCard } from '@/lib/types'
import { ACCEPT, readAttachments, type Attach } from './attach'
import { useInsightStore } from './insightStore'

/**
 * 修改我上一次的提问：原地展开成输入框，改完点“重新回答”，同一张卡片换上新的问题和回答。
 * 选中的原文保持不变；原来附过的文件没有保存，需要的话在这里重新添加。
 */
export function QuestionEditor({ card, onClose }: { card: InsightCard; onClose: () => void }) {
  const ask = useInsightStore((s) => s.ask)
  const [text, setText] = useState(card.question)
  const [files, setFiles] = useState<Attach[]>([])
  const taRef = useRef<HTMLTextAreaElement>(null)
  const pickRef = useRef<HTMLInputElement>(null)
  const oldFiles = card.attachments?.map((a) => a.name) ?? []
  const canSend = text.trim().length > 0 || files.length > 0

  useEffect(() => {
    const ta = taRef.current
    if (!ta) return
    ta.focus()
    ta.setSelectionRange(ta.value.length, ta.value.length)
  }, [])

  useEffect(() => {
    const ta = taRef.current
    if (!ta) return
    ta.style.height = 'auto'
    ta.style.height = `${Math.min(ta.scrollHeight, 220)}px`
  }, [text])

  const submit = (): void => {
    if (!canSend) return
    void ask({
      question: text.trim() || '请看看我附上的文件，结合这篇论文回答。',
      intent: card.intent as AskIntent,
      selection: card.selection ? { text: card.selection.text, page: card.selection.page, passage_ords: card.selection.passage_ords } : null,
      attachments: files.length > 0 ? files.map(({ name, mime, data }) => ({ name, mime, data })) : undefined,
      editCardId: card.id
    })
    onClose()
  }

  const addFiles = async (list: File[]): Promise<void> => {
    if (list.length === 0) return
    const { added, errors } = await readAttachments(list, files.length)
    if (added.length > 0) setFiles((f) => [...f, ...added])
    if (errors.length > 0) toast.error('有文件没能加上', errors.join('；'))
  }

  return (
    <div className="pd-qedit" role="group" aria-label="修改我的提问">
      {card.selection?.text && (
        <blockquote className="pd-qedit-quote" title={card.selection.text}>
          {card.selection.text}
        </blockquote>
      )}
      <textarea
        ref={taRef}
        className="pd-qedit-input"
        rows={2}
        value={text}
        aria-label="修改我的提问"
        placeholder="补充或改正你的问题"
        onChange={(e) => setText(e.target.value)}
        onPaste={(e) => {
          const fs = [...e.clipboardData.files]
          if (fs.length > 0) {
            e.preventDefault()
            void addFiles(fs)
          }
        }}
        onKeyDown={(e) => {
          if (e.key === 'Enter' && !e.shiftKey && !e.nativeEvent.isComposing) {
            e.preventDefault()
            submit()
          } else if (e.key === 'Escape') {
            e.stopPropagation()
            onClose()
          }
        }}
      />
      {(files.length > 0 || oldFiles.length > 0) && (
        <div className="pd-attach-row pd-qedit-files">
          {files.map((f) => (
            <span key={f.id} className="pd-attach" title={f.name}>
              {f.preview ? <img src={f.preview} alt="" /> : <Paperclip size={12} />}
              <span className="pd-ellipsis">{f.name}</span>
              <button type="button" aria-label={`移除 ${f.name}`} onClick={() => setFiles((x) => x.filter((y) => y.id !== f.id))}>
                <X size={11} />
              </button>
            </span>
          ))}
          {files.length === 0 && oldFiles.length > 0 && <span className="pd-aux pd-muted">原来附的 {oldFiles.join('、')} 不会再发送，需要的话重新添加。</span>}
        </div>
      )}
      <div className="pd-qedit-bar">
        <IconButton icon={<Paperclip size={14} />} label="重新添加附件" size="sm" onClick={() => pickRef.current?.click()} />
        <input
          ref={pickRef}
          type="file"
          multiple
          hidden
          accept={ACCEPT}
          onChange={(e) => {
            void addFiles([...(e.target.files ?? [])])
            e.target.value = ''
          }}
        />
        <span className="pd-spacer" />
        <Button size="sm" variant="ghost" onClick={onClose}>
          取消
        </Button>
        <Button size="sm" variant="primary" disabled={!canSend} onClick={submit}>
          重新回答
        </Button>
      </div>
    </div>
  )
}
