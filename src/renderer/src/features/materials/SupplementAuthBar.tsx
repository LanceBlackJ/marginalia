import { Paperclip } from 'lucide-react'
import { Button } from '@/components/ui'
import { useReaderStore } from '@/features/reader/readerStore'
import { useMaterialsStore } from './materialsStore'
import './materials.css'

/**
 * 打开一篇带有补充材料的论文时，询问一次是否允许 AI 在回答时读取它们。
 * 不是模态弹窗：可以直接忽略继续阅读，此时 AI 只基于正文回答。
 */
export function SupplementAuthBar() {
  const loaded = useMaterialsStore((s) => s.loaded)
  const items = useMaterialsStore((s) => s.items)
  const authorization = useMaterialsStore((s) => s.authorization)
  const setAuthorization = useMaterialsStore((s) => s.setAuthorization)
  const setRight = useReaderStore((s) => s.setRight)

  const readable = items.filter((a) => a.download_status === 'done')
  if (!loaded || readable.length === 0 || !authorization || authorization.decision !== null) return null

  return (
    <div className="pd-supp-bar" role="note">
      <Paperclip size={14} />
      <span className="pd-supp-bar-text" title="不允许时 AI 只基于论文正文回答，材料内容不会被发送。">
        有 <strong>{readable.length}</strong> 份补充材料 · 允许 AI 在回答时读取吗？
      </span>
      <Button size="sm" variant="primary" onClick={() => void setAuthorization(true)}>
        本次允许
      </Button>
      <Button size="sm" variant="ghost" onClick={() => void setAuthorization(false)}>
        不使用
      </Button>
      <Button size="sm" variant="ghost" onClick={() => setRight(true, 'materials')}>
        查看材料
      </Button>
    </div>
  )
}
