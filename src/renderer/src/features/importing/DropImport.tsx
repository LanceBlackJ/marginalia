import { useEffect, useRef, useState } from 'react'
import { UploadCloud } from 'lucide-react'
import { toast } from '@/components/ui'
import { hasBridge } from '@/lib/backend'
import { importDropped } from './importActions'
import './drop-import.css'

const hasFiles = (e: DragEvent): boolean => !!e.dataTransfer && Array.from(e.dataTransfer.types).includes('Files')

/** 把文件或文件夹拖进窗口的任意位置就自动导入；库里拖动论文到文件夹（不带 Files）不受影响。 */
export function DropImport() {
  const [over, setOver] = useState(false)
  const depth = useRef(0)

  useEffect(() => {
    const enter = (e: DragEvent): void => {
      if (!hasFiles(e)) return
      e.preventDefault()
      depth.current += 1
      setOver(true)
    }
    const overEv = (e: DragEvent): void => {
      if (!hasFiles(e)) return
      e.preventDefault() // 不阻止默认的话，浏览器会直接把 PDF 在窗口里打开
      if (e.dataTransfer) e.dataTransfer.dropEffect = 'copy'
    }
    const leave = (e: DragEvent): void => {
      if (!hasFiles(e)) return
      depth.current = Math.max(0, depth.current - 1)
      if (depth.current === 0) setOver(false)
    }
    const drop = (e: DragEvent): void => {
      if (!hasFiles(e)) return
      e.preventDefault()
      depth.current = 0
      setOver(false)
      const dt = e.dataTransfer
      if (!dt) return
      if (!hasBridge()) {
        toast.error('网页调试模式不支持拖入', '请在桌面版里拖入文件。')
        return
      }
      // 目录要在 drop 事件里同步判断，事件结束后 dataTransfer 就清空了
      const files: File[] = []
      const folders: File[] = []
      for (const item of Array.from(dt.items)) {
        if (item.kind !== 'file') continue
        const f = item.getAsFile()
        if (!f) continue
        ;(item.webkitGetAsEntry?.()?.isDirectory ? folders : files).push(f)
      }
      const bridge = window.paperdesk!
      void importDropped(bridge.pathsForFiles(files), bridge.pathsForFiles(folders))
    }
    window.addEventListener('dragenter', enter)
    window.addEventListener('dragover', overEv)
    window.addEventListener('dragleave', leave)
    window.addEventListener('drop', drop)
    return () => {
      window.removeEventListener('dragenter', enter)
      window.removeEventListener('dragover', overEv)
      window.removeEventListener('dragleave', leave)
      window.removeEventListener('drop', drop)
    }
  }, [])

  if (!over) return null
  return (
    <div className="pd-dropimport" role="presentation">
      <div className="pd-dropimport-box">
        <UploadCloud size={28} />
        <div>松开鼠标，导入到文献库</div>
        <div className="pd-aux pd-muted">支持 PDF 文件和包含 PDF 的文件夹</div>
      </div>
    </div>
  )
}
