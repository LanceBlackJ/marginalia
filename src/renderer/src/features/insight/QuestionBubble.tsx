import { Paperclip } from 'lucide-react'
import type { questionLine } from './insightModel'

/** 我发的问题：选中的原文以引用的样子放在上面，下面才是我问的话。 */
export function QuestionBubble({ line, attachments }: { line: ReturnType<typeof questionLine>; attachments?: string[] }) {
  if (!line.text && !(attachments && attachments.length)) return null
  return (
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
  )
}
