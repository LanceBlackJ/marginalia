/** 对话附件（截图粘贴、拖放、选文件）：读成 base64 随提问发出，只用于这一次提问。 */
export interface Attach {
  id: string
  name: string
  mime: string
  /** base64（不含 data: 前缀） */
  data: string
  /** 图片的预览地址；其它文件为 null */
  preview: string | null
}

export const MAX_FILES = 4
export const MAX_IMAGE_BYTES = 6 * 1024 * 1024
export const MAX_FILE_BYTES = 8 * 1024 * 1024
export const ACCEPT = 'image/*,.pdf,.txt,.md,.markdown,.csv,.tsv,.json,.yaml,.yml,.log,.tex,.bib,.py,.js,.ts,.tsx,.jsx,.java,.c,.cpp,.h,.go,.rs,.sh'

let seq = 0

function readDataUrl(file: File): Promise<string> {
  return new Promise((resolve, reject) => {
    const r = new FileReader()
    r.onload = () => resolve(String(r.result))
    r.onerror = () => reject(r.error ?? new Error('读取失败'))
    r.readAsDataURL(file)
  })
}

/** 逐个读入；超限或读不出的文件不加进去，并给出原因。 */
export async function readAttachments(files: File[], already: number): Promise<{ added: Attach[]; errors: string[] }> {
  const added: Attach[] = []
  const errors: string[] = []
  for (const f of files) {
    if (already + added.length >= MAX_FILES) {
      errors.push(`一次最多附 ${MAX_FILES} 个文件`)
      break
    }
    const isImage = f.type.startsWith('image/')
    if (f.size > (isImage ? MAX_IMAGE_BYTES : MAX_FILE_BYTES)) {
      errors.push(`《${f.name || '截图'}》太大（${isImage ? '图片最大 6 MB' : '文件最大 8 MB'}）`)
      continue
    }
    try {
      const url = await readDataUrl(f)
      const data = url.slice(url.indexOf(',') + 1)
      const name = f.name && f.name !== 'image.png' ? f.name : `截图 ${new Date().toLocaleTimeString('zh-CN', { hour12: false })}.png`
      added.push({ id: `a${++seq}`, name, mime: f.type || 'application/octet-stream', data, preview: isImage ? url : null })
    } catch {
      errors.push(`《${f.name || '截图'}》读不出来`)
    }
  }
  return { added, errors }
}
