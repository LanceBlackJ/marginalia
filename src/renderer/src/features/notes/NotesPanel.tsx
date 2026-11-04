import { useState } from 'react'
import { NotebookPen, Pencil, Plus, Trash2 } from 'lucide-react'
import { Button, EmptyState, IconButton, Skeleton } from '@/components/ui'
import { formatRelativeTime, truncate } from '@/lib/format'
import type { Note } from '@/lib/types'
import { noteIsStale } from '@/features/library/reparseModel'
import { useReaderStore } from '@/features/reader/readerStore'
import { useNotesStore } from './notesStore'
import { NoteBody } from './NoteBody'
import { StickyEditor } from './StickyEditor'
import { Clamp } from './Clamp'
import { getDraft, setDraft } from '@/lib/drafts'

/** 右侧笔记页先显示最近的几条，其余点“显示全部”，笔记再多也不会把面板越拉越长 */
const NOTES_SHOWN = 8

function NoteItem({ note }: { note: Note }) {
  const update = useNotesStore((s) => s.update)
  const remove = useNotesStore((s) => s.remove)
  const jumpToOrd = useReaderStore((s) => s.jumpToOrd)
  const jumpToPage = useReaderStore((s) => s.jumpToPage)
  // 正在改、还没保存的内容存在本机：切走再回来，这条还是编辑状态、改了一半的字都在
  const saved = getDraft<string>('edit', note.id)
  const [editing, setEditing] = useState(saved !== undefined && saved !== note.text)
  const [text, setTextRaw] = useState(saved ?? note.text)
  const setText = (v: string): void => {
    setTextRaw(v)
    setDraft('edit', note.id, v === note.text ? null : v)
  }
  const [saving, setSaving] = useState(false)

  const goto = (): void => {
    if (note.passage_ord !== null) void jumpToOrd(note.passage_ord)
    else if (note.page) jumpToPage(note.page)
  }

  if (editing) {
    return (
      <StickyEditor
        value={text}
        onChange={setText}
        saving={saving}
        header={note.quote ? <blockquote className="pd-note-quote">{truncate(note.quote, 220)}</blockquote> : undefined}
        onCancel={() => {
          setText(note.text)
          setEditing(false)
        }}
        onSave={() => {
          setSaving(true)
          void update(note.id, text.trim())
            .then(() => {
              setDraft('edit', note.id, null)
              setEditing(false)
            })
            .finally(() => setSaving(false))
        }}
      />
    )
  }

  return (
    <article className="pd-note pd-sticky">
      <div className="pd-row" style={{ marginBottom: 4 }}>
        {note.page && (
          <button type="button" className="pd-cite" onClick={goto} title="跳转到原文">
            p.{note.page}
          </button>
        )}
        {noteIsStale(note) && (
          <span className="pd-note-stale" title="重新解析后，这条笔记引用的位置对不上了。原文引用和页码仍然保留，可以按页码跳转。">
            位置已失效
          </span>
        )}
        <span className="pd-aux pd-weak">{formatRelativeTime(note.updated_at)}</span>
        <span className="pd-spacer" />
        <IconButton icon={<Pencil size={13} />} label="编辑" size="sm" onClick={() => setEditing(true)} />
        <IconButton icon={<Trash2 size={13} />} label="删除笔记" size="sm" onClick={() => void remove(note.id)} />
      </div>
      {note.quote && (
        <Clamp lines={2}>
          <blockquote className="pd-note-quote">{truncate(note.quote, 220)}</blockquote>
        </Clamp>
      )}
      {note.text && (
        <Clamp lines={6}>
          <NoteBody text={note.text} />
        </Clamp>
      )}
    </article>
  )
}

export function NotesPanel() {
  const { notes, loading, error, draft, startDraft, setDraftText, cancelDraft, saveDraft } = useNotesStore()
  const currentPage = useReaderStore((s) => s.currentPage)
  const [saving, setSaving] = useState(false)
  const [all, setAll] = useState(false)
  const shown = all ? notes : notes.slice(0, NOTES_SHOWN)

  return (
    <div className="pd-rpanel-scroll">
      <div className="pd-psec-head">
        <h3 className="pd-psec-title">
          笔记{notes.length > 0 && <span className="pd-weak"> · {notes.length}</span>}
        </h3>
        {!draft && (
          <Button size="sm" variant="primary" icon={<Plus size={13} />} onClick={() => startDraft({ page: currentPage })}>
            新建笔记
          </Button>
        )}
      </div>
      {draft && (
        <StickyEditor
          value={draft.text}
          onChange={setDraftText}
          saving={saving}
          saveLabel="贴上"
          canSaveEmpty={!!draft.quote}
          header={
            <>
              新便签{draft.page ? ` · 第 ${draft.page} 页` : ''}
              {draft.quote && <blockquote className="pd-note-quote">{truncate(draft.quote, 260)}</blockquote>}
            </>
          }
          onCancel={cancelDraft}
          onSave={() => {
            setSaving(true)
            void saveDraft().finally(() => setSaving(false))
          }}
        />
      )}

      {loading ? (
        <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
          <Skeleton height={56} />
          <Skeleton height={56} />
        </div>
      ) : error ? (
        <div className="pd-field-error">笔记加载失败：{error}</div>
      ) : notes.length === 0 && !draft ? (
        <EmptyState icon={<NotebookPen size={20} />} title="还没有笔记" />
      ) : (
        <>
          {shown.map((n) => (
            <NoteItem key={n.id} note={n} />
          ))}
          {notes.length > NOTES_SHOWN && (
            <button type="button" className="pd-dc-more" onClick={() => setAll((v) => !v)}>
              {all ? '收起' : `显示全部 ${notes.length} 条`}
            </button>
          )}
        </>
      )}
    </div>
  )
}
