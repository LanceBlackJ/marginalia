import { useEffect, useRef, useState } from 'react'
import { createPortal } from 'react-dom'
import { Pencil, Trash2, X } from 'lucide-react'
import { IconButton } from '@/components/ui'
import { formatRelativeTime, truncate } from '@/lib/format'
import { NoteBody } from './NoteBody'
import { StickyEditor } from './StickyEditor'
import { useNotePeek } from './notePeekStore'
import { useNotesStore } from './notesStore'

const WIDTH = 320

/** 点正文里的便签标记弹出的一张便签：纸色底、顶上一条胶带，能看、改、删这条笔记。Esc 或点别处关闭。 */
export function NotePeek() {
  const peek = useNotePeek((s) => s.peek)
  const close = useNotePeek((s) => s.close)
  const note = useNotesStore((s) => (peek ? s.notes.find((n) => String(n.id) === peek.id) : undefined))
  const update = useNotesStore((s) => s.update)
  const remove = useNotesStore((s) => s.remove)
  const ref = useRef<HTMLDivElement>(null)
  const [editing, setEditing] = useState(false)
  const [text, setText] = useState('')
  const [saving, setSaving] = useState(false)

  useEffect(() => {
    setEditing(false)
  }, [peek?.id])

  useEffect(() => {
    if (!peek) return
    const onKey = (e: KeyboardEvent): void => {
      if (e.key === 'Escape' && !editing) close()
    }
    const onDown = (e: MouseEvent): void => {
      const t = e.target as HTMLElement
      if (ref.current?.contains(t) || t.closest('[data-note]')) return
      close()
    }
    window.addEventListener('keydown', onKey)
    window.addEventListener('mousedown', onDown)
    return () => {
      window.removeEventListener('keydown', onKey)
      window.removeEventListener('mousedown', onDown)
    }
  }, [peek, editing, close])

  useEffect(() => {
    if (peek && !note) close() // 这条笔记已经被删了
  }, [peek, note, close])

  if (!peek || !note) return null
  const left = Math.max(12, Math.min(peek.x - WIDTH / 2, window.innerWidth - WIDTH - 12))
  const below = peek.y + 14
  const style = below + 260 < window.innerHeight ? { left, top: below, width: WIDTH } : { left, bottom: window.innerHeight - peek.y + 14, width: WIDTH }

  return createPortal(
    <div ref={ref} className="pd-notepeek" style={style} role="dialog" aria-label="笔记">
      {editing ? (
        <StickyEditor
          value={text}
          onChange={setText}
          saving={saving}
          header={note.quote ? <blockquote className="pd-note-quote">{truncate(note.quote, 160)}</blockquote> : undefined}
          onCancel={() => setEditing(false)}
          onSave={() => {
            setSaving(true)
            void update(note.id, text.trim())
              .then(() => setEditing(false))
              .finally(() => setSaving(false))
          }}
        />
      ) : (
        <article className="pd-sticky pd-sticky--peek">
          <div className="pd-sticky-meta" style={{ marginTop: 0, marginBottom: 6 }}>
            <span className="pd-muted">
              {note.page ? `第 ${note.page} 页 · ` : ''}
              {formatRelativeTime(note.updated_at)}
            </span>
            <span>
              <IconButton
                icon={<Pencil size={13} />}
                label="编辑"
                size="sm"
                onClick={() => {
                  setText(note.text)
                  setEditing(true)
                }}
              />
              <IconButton
                icon={<Trash2 size={13} />}
                label="删除"
                size="sm"
                onClick={() => {
                  void remove(note.id)
                  close()
                }}
              />
              <IconButton icon={<X size={13} />} label="关闭" size="sm" onClick={close} />
            </span>
          </div>
          {note.quote && <blockquote className="pd-note-quote">{truncate(note.quote, 160)}</blockquote>}
          {note.text ? <NoteBody text={note.text} /> : <span className="pd-muted">（只引用了原文，没有写内容）</span>}
        </article>
      )}
    </div>,
    document.body
  )
}
