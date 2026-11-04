import { useEffect, useState } from 'react'
import { AlertTriangle } from 'lucide-react'
import { textApi } from '@/lib/api.text'
import type { ProcessingSummary } from '@/lib/types'
import { isProcessingActive, processingNote } from '@/features/library/processingModel'

/**
 * 概览顶部的一行处理进度：公式、表格还没处理好时说明“部分内容可能还是截图”，不让用户以为已经全部分析好了。
 * 只有还在处理时才每 4 秒看一次进度；全部处理好后什么也不显示。
 */
export function ProcessingNote({ paperId }: { paperId: string }) {
  const [p, setP] = useState<ProcessingSummary | undefined>(undefined)
  const active = isProcessingActive(p)
  useEffect(() => {
    let alive = true
    const load = (): void => {
      textApi
        .processing(paperId)
        .then((r) => alive && setP(r))
        .catch(() => undefined)
    }
    load()
    if (!active) return () => void (alive = false)
    const timer = window.setInterval(load, 4000)
    return () => {
      alive = false
      window.clearInterval(timer)
    }
  }, [paperId, active])
  const note = processingNote(p)
  if (!note) return null
  return (
    <div className="pd-card-hint" role="status" style={{ marginBottom: 12 }}>
      <AlertTriangle size={13} />
      <span>{note}</span>
    </div>
  )
}
