import { useState } from 'react'
import { Download, FolderOpen } from 'lucide-react'
import { Button, Dialog, Input, Tag, toast } from '@/components/ui'
import { errorMessage } from '@/lib/api'
import { codeApi } from '@/lib/api.code'
import { pickFolder } from '@/lib/native'
import type { CodeSource, RepoInfo } from '@/lib/types.code'
import { fmtBytes, shortSha } from './codeUtils'

const KIND_LABEL: Record<string, string> = { github: 'GitHub', gitlab: 'GitLab', zenodo: 'Zenodo', doi: 'DOI', codeocean: 'Code Ocean', figshare: 'Figshare', zip: 'zip 链接', other: '链接' }

interface Props {
  paperId: string
  sources: CodeSource[]
  /** 代码开始获取后调用（仓库已创建，后台任务已启动） */
  onStarted: (repoId: string) => void
  variant?: 'page' | 'dialog'
}

function isZipUrl(url: string): boolean {
  return /\.zip(\?.*)?$/i.test(url.trim())
}

/** 获取代码的向导：论文里声明的来源 / 手动链接 / 本地 zip 或文件夹。所有来源都只做下载与静态分析，不会运行其中的任何代码。 */
export function FetchWizardBody({ paperId, sources, onStarted, variant = 'page' }: Props) {
  const [url, setUrl] = useState('')
  const [info, setInfo] = useState<RepoInfo | null>(null)
  const [infoUrl, setInfoUrl] = useState('')
  const [busy, setBusy] = useState<'resolve' | 'fetch' | 'import' | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [localPath, setLocalPath] = useState('')

  const resolve = async (u: string): Promise<void> => {
    const target = u.trim()
    if (!target) return
    setError(null)
    setInfo(null)
    if (isZipUrl(target)) {
      setInfoUrl(target)
      return
    }
    setBusy('resolve')
    try {
      setInfo(await codeApi.resolve(paperId, target))
      setInfoUrl(target)
    } catch (err) {
      setError(errorMessage(err))
    } finally {
      setBusy(null)
    }
  }

  const start = async (): Promise<void> => {
    setBusy('fetch')
    setError(null)
    try {
      const r = await codeApi.fetch(paperId, infoUrl, info?.branch)
      // 较大的仓库需要明确确认：用户点击“确认下载”本身就是确认
      if (r.requires_approval) await codeApi.approveJob(r.job_id)
      onStarted(r.repo_id)
    } catch (err) {
      setError(errorMessage(err))
    } finally {
      setBusy(null)
    }
  }

  const importLocal = async (): Promise<void> => {
    const p = localPath.trim().replace(/^"|"$/g, '')
    if (!p) return
    setBusy('import')
    setError(null)
    try {
      const r = await codeApi.importLocal(paperId, p, 'auto', 'user')
      onStarted(r.repo_id)
    } catch (err) {
      setError(errorMessage(err))
    } finally {
      setBusy(null)
    }
  }

  const pick = async (): Promise<void> => {
    try {
      const dir = await pickFolder()
      if (dir) setLocalPath(dir)
    } catch (err) {
      toast.error('无法选择文件夹', errorMessage(err))
    }
  }

  const stale = info?.warnings ?? []
  const largeMb = info ? Math.round(info.size_bytes / 1024 / 1024) : 0

  return (
    <div className={`pd-wiz pd-wiz--${variant}`}>
      <p className="pd-wiz-safe">只会下载压缩包并做静态分析，不会运行其中的任何代码。</p>

      {error && (
        <p className="pd-wiz-error" role="alert">
          {error}
        </p>
      )}

      {infoUrl && (info || isZipUrl(infoUrl)) && (
        <section className="pd-wiz-sec pd-wiz-info">
          <h3 className="pd-wiz-h">将要获取</h3>
          {info ? (
            <dl className="pd-kv">
              <dt>仓库</dt>
              <dd>
                {info.owner}/{info.name}
                {info.archived && ' · 已归档'}
              </dd>
              <dt>分支</dt>
              <dd>
                {info.branch}
                {info.branch !== info.default_branch && ` · 默认分支 ${info.default_branch}`}
              </dd>
              <dt>版本</dt>
              <dd>
                commit {shortSha(info.commit_sha)}
                {info.commit_date ? `，${info.commit_date.slice(0, 10)}` : ''}
              </dd>
              <dt>大小</dt>
              <dd>约 {fmtBytes(info.size_bytes)}</dd>
              {info.description && (
                <>
                  <dt>简介</dt>
                  <dd>{info.description}</dd>
                </>
              )}
            </dl>
          ) : (
            <p className="pd-muted">zip 直链：{infoUrl}</p>
          )}
          {stale.map((w) => (
            <p key={w} className="pd-wiz-warn">
              {w}
            </p>
          ))}
          {info?.requires_approval && !info.too_large && <p className="pd-wiz-warn">这个仓库比较大，约 {largeMb} MB，下载会占用一些时间和磁盘空间。确认后才会开始下载。</p>}
          <div className="pd-wiz-actions">
            <Button
              variant="primary"
              icon={<Download size={14} />}
              loading={busy === 'fetch'}
              disabled={busy !== null || !!info?.too_large}
              disabledReason={info?.too_large ? '超过 300 MB 上限，不会下载' : undefined}
              onClick={() => void start()}
            >
              {info?.requires_approval ? `确认下载并分析，约 ${fmtBytes(info.size_bytes)}` : '下载并分析'}
            </Button>
          </div>
        </section>
      )}

      <section className="pd-wiz-sec">
        <h3 className="pd-wiz-h">论文里声明的代码来源</h3>
        {sources.length === 0 ? (
          <p className="pd-muted pd-wiz-empty">这篇论文的正文里没有找到代码或数据链接。你可以在下面手动提供。</p>
        ) : (
          <ul className="pd-wiz-sources">
            {sources.map((s) => (
              <li key={`${s.url}-${s.ord}`} className="pd-wiz-source">
                <div className="pd-wiz-source-main">
                  <div className="pd-wiz-source-top">
                    <Tag tone="outline">{KIND_LABEL[s.kind] ?? s.kind}</Tag>
                    <span className="pd-wiz-url pd-ellipsis" title={s.url}>
                      {s.url}
                    </span>
                  </div>
                  <div className="pd-aux pd-muted pd-wiz-ctx" title={s.context}>
                    论文第 {s.page} 页{s.is_code_statement ? '，声明了代码/数据的获取方式' : '，正文中出现的链接，不一定是代码'}：…{s.context}…
                  </div>
                </div>
                {s.supported ? (
                  <Button size="sm" variant={infoUrl === s.url ? 'primary' : 'secondary'} loading={busy === 'resolve' && url === s.url} disabled={busy !== null} onClick={() => { setUrl(s.url); void resolve(s.url) }}>
                    选择
                  </Button>
                ) : (
                  <span className="pd-aux pd-muted pd-wiz-unsupported">暂不支持直接获取，请手动下载后在下面导入</span>
                )}
              </li>
            ))}
          </ul>
        )}
      </section>

      <section className="pd-wiz-sec">
        <h3 className="pd-wiz-h">手动输入链接</h3>
        <div className="pd-wiz-row">
          <Input value={url} onChange={setUrl} onEnter={() => void resolve(url)} placeholder="https://github.com/owner/repo  或  zip 直链" aria-label="代码链接" clearable />
          <Button disabled={!url.trim() || busy !== null} loading={busy === 'resolve'} onClick={() => void resolve(url)}>
            解析
          </Button>
        </div>
      </section>

      <section className="pd-wiz-sec">
        <h3 className="pd-wiz-h">导入本地文件</h3>
        <div className="pd-wiz-row">
          <Input value={localPath} onChange={setLocalPath} onEnter={() => void importLocal()} placeholder="D:\code\project.zip  或  D:\code\project" aria-label="本地 zip 或文件夹路径" clearable />
          <Button icon={<FolderOpen size={14} />} onClick={() => void pick()} disabled={busy !== null}>
            选择文件夹
          </Button>
          <Button disabled={!localPath.trim() || busy !== null} loading={busy === 'import'} onClick={() => void importLocal()}>
            导入
          </Button>
        </div>
        <p className="pd-aux pd-muted">支持 .zip 或文件夹，只读取并复制一份，不会改动原文件。</p>
      </section>
    </div>
  )
}

export function FetchWizardDialog({ open, onClose, ...props }: Props & { open: boolean; onClose: () => void }) {
  return (
    <Dialog open={open} onClose={onClose} title="获取代码" width={680}>
      <FetchWizardBody {...props} variant="dialog" onStarted={(id) => { props.onStarted(id); onClose() }} />
    </Dialog>
  )
}
