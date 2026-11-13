import { Dialog } from '@/components/ui'
import { useReaderStore } from './readerStore'

export const SHORTCUTS: Array<{ keys: string; what: string }> = [
  { keys: 'J / K', what: '向下 / 向上翻一屏的 80%' },
  { keys: '[ / ]', what: '上一节 / 下一节' },
  { keys: '/ 或 Ctrl+F', what: '在论文中搜索' },
  { keys: 'Alt + ←', what: '回到跳转之前的位置（点了目录、引用、书签之后）' },
  { keys: 'T', what: '显示 / 隐藏译文' },
  { keys: 'S', what: '切换 文字 / 并排 / PDF' },
  { keys: 'F', what: '专注模式，Esc 退出，鼠标移到顶部边缘可呼出顶栏' },
  { keys: 'A', what: '阅读外观' },
  { keys: 'N', what: '为当前页写一条笔记' },
  { keys: '?', what: '显示这份快捷键说明' },
  { keys: 'Esc', what: '关闭面板 / 退出专注模式' }
]

export function ShortcutHelp() {
  const open = useReaderStore((s) => s.helpOpen)
  const setOpen = useReaderStore((s) => s.setHelpOpen)
  return (
    <Dialog open={open} onClose={() => setOpen(false)} title="阅读快捷键" width={460}>
      <table className="pd-keytable">
        <tbody>
          {SHORTCUTS.map((s) => (
            <tr key={s.keys}>
              <th scope="row">
                <kbd>{s.keys}</kbd>
              </th>
              <td>{s.what}</td>
            </tr>
          ))}
        </tbody>
      </table>
      <p className="pd-aux pd-muted" style={{ marginTop: 10 }}>
        在输入框里打字时快捷键不会触发。
      </p>
    </Dialog>
  )
}
