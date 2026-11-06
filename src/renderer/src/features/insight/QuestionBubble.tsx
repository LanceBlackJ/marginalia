import { Paperclip, Pencil } from 'lucide-react'
import type { questionLine } from './insightModel'

/**
 * 我发的问题：选中的原文以引用的样子放在上面，下面才是我问的话。
 * 给了 onEdit（这是对话里最后一次提问）时，鼠标放上去会出现“修改”按钮。
 */
export function QuestionBubble({ line, attachments, onEdit }: { line: ReturnType<typeof questionLine>; attachments?: string[]; onEdit?: () => void }) {
  if (!line.text && !(attachments && attachments.length)) return null
  return (
    <div className="pd-card-question-row">
      {onEdit && (
        <button type="button" className="pd-card-qedit-btn" aria-label="修改这条提问" title="修改这条提问，并重新回答" onClick={onEdit}>
          <Pencil size={13} />
        </button>
      )}
      <div className="pd-card-question" title={line.full}>
        {line.quote && (
          <blockquote className="pd-card-quote" data-translatable>
            {line.quote}
          </blockquote>
        )}
        {line.ask && <p data-translatable>{line.ask}</p>}
        {attachments && attachments.length > 0 && (
          <div className="pd-card-attach">
            {attachments.map((n) => (
              <span key={n} title={n}>
                <Paperclip size={11} /> {n}
              </span>
            ))}
          </div>
        )}
      </div>
    </div>
  )
}
