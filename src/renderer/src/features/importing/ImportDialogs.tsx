import { useEffect, useMemo, useState } from 'react'
import { Link2, Quote } from 'lucide-react'
import { Button, Dialog, Field, Input, Switch, TextArea } from '@/components/ui'
import { errorMessage } from '@/lib/api'
import { toast } from '@/components/ui'
import { importDois, importUrl, parseDois, useImportUi } from './importActions'
import { usePathPrompt } from '@/stores/pathPromptStore'
import { ManualDownloadDialog } from './ManualDownloadDialog'

const DOI_RE = /^(?:https?:\/\/(?:dx\.)?doi\.org\/|doi:\s*)?10\.\d{4,9}\/\S+$/i

export function ImportDialogs() {
  const { doiOpen, urlOpen, close } = useImportUi()
  return (
    <>
      <DoiDialog open={doiOpen} onClose={close} />
      <UrlDialog open={urlOpen} onClose={close} />
      <PathPromptDialog />
      <ManualDownloadDialog />
    </>
  )
}

function DoiDialog({ open, onClose }: { open: boolean; onClose: () => void }) {
  const [text, setText] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [download, setDownload] = useState(true)

  useEffect(() => {
    if (open) {
      setText('')
      setError(null)
      setDownload(true)
    }
  }, [open])

  const dois = useMemo(() => parseDois(text), [text])
  const invalid = dois.filter((d) => !DOI_RE.test(d))

  const submit = async (): Promise<void> => {
    if (dois.length === 0 || invalid.length > 0) return
    setBusy(true)
    setError(null)
    try {
      await importDois(dois, !download)
      onClose()
    } catch (err) {
      setError(errorMessage(err))
    } finally {
      setBusy(false)
    }
  }

  return (
    <Dialog
      open={open}
      onClose={onClose}
      title="DOI 导入"
      width={520}
      footer={
        <>
          <Button onClick={onClose}>取消</Button>
          <Button variant="primary" loading={busy} disabled={dois.length === 0 || invalid.length > 0} onClick={() => void submit()}>
            导入 {dois.length > 0 ? `${dois.length} 篇` : ''}
          </Button>
        </>
      }
    >
      <Field
        label="DOI"
        hint="每行一个，也可用逗号或空格分隔。"
        error={error ?? (invalid.length > 0 ? `无法识别的 DOI：${invalid.slice(0, 3).join('、')}${invalid.length > 3 ? ' 等' : ''}` : undefined)}
      >
        {(id) => <TextArea id={id} rows={6} value={text} onChange={setText} placeholder={'10.1038/nature14539\nhttps://doi.org/10.1145/3292500.3330701'} />}
      </Field>
      <div style={{ marginTop: 12 }}>
        <Switch checked={download} onChange={setDownload} label="同时下载 PDF" />
        <p className="pd-aux pd-muted" style={{ marginTop: 4 }}>
          {download ? '有开放获取全文就下载并解析，没有就只保存元数据。需要机构权限的文献请自行下载后导入。' : '只保存元数据，之后可以在论文页面附加 PDF。'}
        </p>
      </div>
    </Dialog>
  )
}

function UrlDialog({ open, onClose }: { open: boolean; onClose: () => void }) {
  const [url, setUrl] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    if (open) {
      setUrl('')
      setError(null)
    }
  }, [open])

  const valid = /^https?:\/\/\S+$/i.test(url.trim())

  const submit = async (): Promise<void> => {
    if (!valid) return
    setBusy(true)
    setError(null)
    try {
      await importUrl(url)
      onClose()
    } catch (err) {
      setError(errorMessage(err))
      toast.error('链接导入失败', errorMessage(err))
    } finally {
      setBusy(false)
    }
  }

  return (
    <Dialog
      open={open}
      onClose={onClose}
      title="论文链接导入"
      width={520}
      footer={
        <>
          <Button onClick={onClose}>取消</Button>
          <Button variant="primary" loading={busy} disabled={!valid} onClick={() => void submit()}>
            导入
          </Button>
        </>
      }
    >
      <Field label="论文链接" hint="支持 arXiv、doi.org 链接和 PDF 直链；其他网站请先下载 PDF 再导入。" error={error ?? undefined}>
        {(id) => <Input id={id} icon={<Link2 size={14} />} value={url} onChange={setUrl} onEnter={() => void submit()} placeholder="https://arxiv.org/abs/1706.03762" clearable />}
      </Field>
    </Dialog>
  )
}

/** Web 调试模式下替代原生文件对话框的路径输入框。 */
function PathPromptDialog() {
  const request = usePathPrompt((s) => s.request)
  const answer = usePathPrompt((s) => s.answer)
  const [value, setValue] = useState('')

  useEffect(() => {
    if (request) setValue('')
  }, [request])

  if (!request) return null
  return (
    <Dialog
      open
      onClose={() => answer(null)}
      title={request.title}
      width={560}
      footer={
        <>
          <Button onClick={() => answer(null)}>取消</Button>
          <Button variant="primary" disabled={!value.trim()} onClick={() => answer(value)}>
            确定
          </Button>
        </>
      }
    >
      <Field label={request.label}>
        {(id) =>
          request.multiline ? (
            <TextArea id={id} rows={5} value={value} onChange={setValue} placeholder={request.placeholder} />
          ) : (
            <Input id={id} icon={<Quote size={14} />} value={value} onChange={setValue} onEnter={() => value.trim() && answer(value)} placeholder={request.placeholder} />
          )
        }
      </Field>
    </Dialog>
  )
}
