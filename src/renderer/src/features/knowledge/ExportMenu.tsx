import { useState } from 'react'
import { Download } from 'lucide-react'
import { Button, Menu, toast } from '@/components/ui'
import { errorMessage } from '@/lib/api'
import { exportApi } from '@/lib/api.discovery'
import { downloadText } from '@/features/shared/download'

type Kind = 'topic' | 'notes' | 'cards' | 'viewpoints'

/** Markdown 导出：内容由后端生成（含来源与证据位置），前端保存为文件（Electron 弹出“另存为”）。 */
export function ExportMenu({ topicId, paperIds, align = 'end' }: { topicId?: string | null; paperIds?: string[]; align?: 'start' | 'end' }) {
  const [busy, setBusy] = useState(false)

  const run = async (kind: Kind): Promise<void> => {
    setBusy(true)
    try {
      const r = await exportApi.markdown({ kind, topic_id: kind === 'topic' || kind === 'viewpoints' ? (topicId ?? undefined) : undefined, paper_ids: paperIds, include_evidence: true })
      if (r.count === 0) {
        toast.info('没有可导出的内容', '这一类内容目前是空的。')
        return
      }
      downloadText(r.filename, r.content)
      toast.success('已导出 Markdown', `${r.filename} · ${r.count} 条`)
    } catch (err) {
      toast.error('导出失败', errorMessage(err))
    } finally {
      setBusy(false)
    }
  }

  return (
    <Menu
      align={align}
      trigger={({ toggle, open }) => (
        <Button size="sm" variant="secondary" icon={<Download size={14} />} loading={busy} onClick={toggle} aria-haspopup="menu" aria-expanded={open}>
          导出 Markdown
        </Button>
      )}
      items={[
        { key: 'topic', label: '当前主题的知识条目', hint: topicId ? undefined : '先选择一个主题', disabled: !topicId, onSelect: () => void run('topic') },
        { key: 'viewpoints', label: topicId ? '当前主题的观点与结论' : '全部观点与结论', onSelect: () => void run('viewpoints') },
        { key: 'cards', label: '学习卡片', onSelect: () => void run('cards') },
        { key: 'notes', label: '阅读笔记', onSelect: () => void run('notes') }
      ]}
    />
  )
}
