import { Button, Dialog } from '@/components/ui'

/** 重新解析前的说明与确认（文库和阅读器共用）：讲清楚会保留什么、对不上的怎么处理、可以撤销。 */
export function ReparseDialog({ open, count, onClose, onConfirm }: { open: boolean; count: number; onClose: () => void; onConfirm: () => void }) {
  return (
    <Dialog
      open={open}
      onClose={onClose}
      title={`重新解析${count > 1 ? `这 ${count} 篇论文` : '这篇论文'}？`}
      width={480}
      footer={
        <>
          <Button onClick={onClose}>取消</Button>
          <Button variant="primary" onClick={onConfirm}>
            重新解析
          </Button>
        </>
      }
    >
      <p>用最新的解析器重新读一遍 PDF，更新论文的文字、章节、图表和公式的位置。</p>
      <p className="pd-muted" style={{ marginTop: 8 }}>
        你的笔记、批注、书签、AI 卡片、思维导图和标签都会保留，并重新对应到新的段落；对应不上的笔记不会被删除，只会标为“位置已失效”，原文引用和页码仍在。
        补充材料会一起重新解析。公式、图和表格的识别结果会尽量保留，对不上的会重新识别。完成后可以撤销，保留 7 天。
      </p>
    </Dialog>
  )
}
