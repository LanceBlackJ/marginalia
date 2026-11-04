import type { ReactNode } from 'react'
import { Eye, Sparkles } from 'lucide-react'
import { Menu } from '@/components/ui'
import { useTextStore } from './textStore'
import { useFigViewer, type FigTarget } from './figViewerStore'
import { aiParseTable } from './figAi'
import { labelKey } from './figRefs'

/** 点图/表的标签（正文里的“Fig. 3”、图注开头的“图 3”）：选“查看”还是“让 AI 解析”。 */
export function FigLabelMenu({ target, className, title, children }: { target: FigTarget; className?: string; title?: string; children: ReactNode }) {
  const open = useFigViewer((s) => s.open)
  const openAi = useFigViewer((s) => s.openAi)
  return (
    <Menu
      align="start"
      width={170}
      trigger={({ toggle }) => (
        <button type="button" className={className} title={title ?? '点击：查看，或让 AI 解析'} onClick={toggle}>
          {children}
        </button>
      )}
      items={[
        { key: 'view', label: target.kind === 'table' ? '查看这张表' : '查看这张图', icon: <Eye size={14} />, onSelect: () => open(target) },
        {
          key: 'ai',
          label: '让 AI 解析',
          icon: <Sparkles size={14} />,
          onSelect: () => {
            if (target.kind === 'table') {
              const t = [...useTextStore.getState().tablesByCaption.values()].find((x) => x.kind !== 'figure_data' && labelKey(x.label) === target.key)
              if (t) aiParseTable(t)
            } else openAi(target)
          }
        }
      ]}
    />
  )
}
