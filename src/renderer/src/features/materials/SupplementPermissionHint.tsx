import { FileText } from 'lucide-react'
import { Button } from '@/components/ui'
import type { AskIntent, SelectionRef } from '@/lib/types'
import { useInsightStore } from '@/features/insight/insightStore'
import { useMaterialsStore } from './materialsStore'

interface Props {
  cardId: string
  question: string
  intent: AskIntent | string
  selection: SelectionRef | null
}

/**
 * 回答可能需要补充材料、但用户还没有授权时的一行轻提示（SSE needs_supplement_permission）。
 * 不擅自读取补充材料：点“允许”才会授权，并用同样的问题重新回答。
 */
export function SupplementPermissionHint({ cardId, question, intent, selection }: Props) {
  const needs = useMaterialsStore((s) => s.needs[cardId])
  const granted = useMaterialsStore((s) => s.authorization?.granted ?? false)
  const setAuthorization = useMaterialsStore((s) => s.setAuthorization)
  const ask = useInsightStore((s) => s.ask)
  const busy = useInsightStore((s) => s.pending?.status === 'streaming')

  if (!needs || granted) return null

  const allowAndAsk = async (): Promise<void> => {
    await setAuthorization(true)
    void ask({ question, intent: intent as AskIntent, selection })
  }

  return (
    <div className="pd-card-hint pd-supp-hint" role="note">
      <FileText size={13} />
      <span style={{ flex: 1, minWidth: 0 }}>
        {needs.reason}。要查阅补充材料吗？本次回答只用了正文。
      </span>
      <Button size="sm" disabled={busy} onClick={() => void allowAndAsk()}>
        允许使用并重新回答
      </Button>
    </div>
  )
}
