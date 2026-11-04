import { useState } from 'react'
import { useNavigate } from 'react-router-dom'
import {
  File,
  FileArchive,
  FileCode,
  FileSpreadsheet,
  FileText,
  FolderOpen,
  Link2,
  ListOrdered,
  MoreHorizontal,
  Plus,
  Search,
  Trash2
} from 'lucide-react'
import { Button, Dialog, EmptyState, Field, IconButton, Input, Menu, ProgressBar, Skeleton, Switch, Tag, type MenuItem } from '@/components/ui'
import { formatBytes } from '@/lib/format'
import { pickSupplementFiles } from '@/lib/native'
import { SUPPLEMENT_CATEGORIES, type SupplementAsset } from '@/lib/types.materials'
import { DiscoverDialog } from './DiscoverDialog'
import { assetFacts } from './helpers'
import { useMaterialsStore } from './materialsStore'
import './materials.css'

const STATUS_TEXT: Record<string, string> = {
  pending: '排队中',
  waiting_approval: '等待你批准',
  downloading: '下载中',
  processing: '解析中',
  failed: '处理失败',
  rejected: '已拒绝下载'
}

const SOURCE_TEXT: Record<string, string> = { local: '本地文件', url: '链接', auto: '自动发现' }

function KindIcon({ kind }: { kind: SupplementAsset['kind'] }) {
  const p = { size: 15, className: 'pd-mat-icon' }
  if (kind === 'table') return <FileSpreadsheet {...p} />
  if (kind === 'archive') return <FileArchive {...p} />
  if (kind === 'code') return <FileCode {...p} />
  if (kind === 'pdf' || kind === 'document') return <FileText {...p} />
  return <File {...p} />
}

function canOpen(a: SupplementAsset): boolean {
  if (a.download_status !== 'done') return false
  return true
}

function AddUrlDialog({ open, onClose }: { open: boolean; onClose: () => void }) {
  const addUrl = useMaterialsStore((s) => s.addUrl)
  const [url, setUrl] = useState('')
  const [busy, setBusy] = useState(false)
  const valid = /^https?:\/\/\S+$/i.test(url.trim())

  const submit = async (): Promise<void> => {
    if (!valid || busy) return
    setBusy(true)
    const ok = await addUrl(url.trim())
    setBusy(false)
    if (ok) {
      setUrl('')
      onClose()
    }
  }
  return (
    <Dialog
      open={open}
      onClose={onClose}
      title="通过链接添加补充材料"
      width={480}
      footer={
        <>
          <Button variant="ghost" onClick={onClose}>
            取消
          </Button>
          <Button variant="primary" disabled={!valid} loading={busy} onClick={() => void submit()}>
            添加
          </Button>
        </>
      }
    >
      <Field label="文件链接" hint="只下载你给出的这一个文件；不会登录或绕过付费墙。超过 20 MB 的文件会先请你批准。">
        {(id) => <Input id={id} value={url} placeholder="https://…/supplementary.pdf" onChange={setUrl} onEnter={() => void submit()} />}
      </Field>
    </Dialog>
  )
}

function AssetRow({ a }: { a: SupplementAsset }) {
  const navigate = useNavigate()
  const { approve, reject, retry, remove, setCategory, confirmMatch, analyze } = useMaterialsStore.getState()
  const busy = ['pending', 'downloading', 'processing'].includes(a.download_status)
  const facts = assetFacts(a)
  const open = (): void => {
    if (!canOpen(a)) return
    navigate(a.child_paper_id ? `/reader/${a.child_paper_id}` : `/materials/${a.id}`)
  }
  const items: MenuItem[] = [
    { key: 'open', label: a.child_paper_id ? '在阅读器中打开' : '查看详情', disabled: !canOpen(a), onSelect: open },
    ...(a.download_status === 'done'
      ? [{ key: 'ai', label: 'AI 分类与概述', hint: '会调用云端模型', onSelect: () => void analyze(a.id) }]
      : []),
    { key: 'sep', separator: true },
    { key: 'del', label: '移除', icon: <Trash2 size={14} />, danger: true, onSelect: () => void remove(a.id) }
  ]
  return (
    <div className="pd-mat-row" data-status={a.download_status}>
      <div className="pd-mat-line">
        <KindIcon kind={a.kind} />
        <div className="pd-mat-main">
          <button type="button" className="pd-mat-name" disabled={!canOpen(a)} onClick={open} title={a.filename}>
            {a.filename}
          </button>
          <div className="pd-mat-meta">
            {facts.map((f, i) => (
              <span key={i} className={i > 0 ? 'pd-sep' : undefined}>
                {f}
              </span>
            ))}
            <span className={facts.length ? 'pd-sep' : undefined}>{SOURCE_TEXT[a.source] ?? a.source}</span>
            <span className="pd-sep">
              <select
                className="pd-mat-select"
                aria-label="材料类别"
                value={a.category}
                onChange={(e) => void setCategory(a.id, e.target.value)}
              >
                {SUPPLEMENT_CATEGORIES.map((c) => (
                  <option key={c} value={c}>
                    {c}
                  </option>
                ))}
              </select>
              {a.category_source === 'ai' ? ' · AI' : ''}
            </span>
            {a.download_status !== 'done' && (
              <span className="pd-sep">
                <Tag tone={a.download_status === 'failed' ? 'danger' : a.download_status === 'waiting_approval' ? 'warning' : 'outline'}>
                  {STATUS_TEXT[a.download_status] ?? a.download_status}
                </Tag>
              </span>
            )}
            {a.match_status === '待确认' && (
              <span className="pd-sep">
                <Tag tone="warning">待确认</Tag>{' '}
                <button type="button" className="pd-mat-select" onClick={() => void confirmMatch(a.id)}>
                  确认属于这篇论文
                </button>
              </span>
            )}
          </div>
          {a.summary && <p className="pd-mat-summary">{a.summary}</p>}
          {a.match_basis && <p className="pd-mat-basis">{a.match_basis}</p>}
          {busy && (
            <div style={{ marginTop: 8, maxWidth: 220 }}>
              <ProgressBar />
            </div>
          )}
        </div>
        <div className="pd-mat-actions">
          <Menu
            align="end"
            width={190}
            items={items}
            trigger={({ toggle }) => <IconButton icon={<MoreHorizontal size={15} />} label="更多" size="sm" onClick={toggle} />}
          />
        </div>
      </div>

      {a.download_status === 'waiting_approval' && (
        <div className="pd-mat-note">
          这个文件{a.size ? `有 ${formatBytes(a.size)}` : '大小未知，可能较大'}，超过了自动下载的 20 MB 上限。是否下载？
          <div className="pd-row">
            <Button size="sm" variant="primary" onClick={() => void approve(a.id)}>
              批准下载
            </Button>
            <Button size="sm" variant="ghost" onClick={() => void reject(a.id)}>
              拒绝
            </Button>
          </div>
        </div>
      )}
      {a.download_status === 'rejected' && (
        <div className="pd-mat-note">
          你拒绝了这次下载。
          <div className="pd-row">
            <Button size="sm" variant="ghost" onClick={() => void retry(a.id)}>
              重新询问
            </Button>
          </div>
        </div>
      )}
      {a.download_status === 'failed' && (
        <div className="pd-mat-note pd-mat-note--error">
          {a.download_error ?? '处理失败'}
          <div className="pd-row">
            <Button size="sm" variant="ghost" onClick={() => void retry(a.id)}>
              重试
            </Button>
          </div>
        </div>
      )}
      {a.download_status === 'done' && a.analysis?.kind === 'error' && (
        <div className="pd-mat-note pd-mat-note--error">解析时遇到问题：{a.analysis.error ?? '未知错误'}。原文件已保留，可以重新添加。</div>
      )}
    </div>
  )
}

export function MaterialsPanel() {
  const items = useMaterialsStore((s) => s.items)
  const loaded = useMaterialsStore((s) => s.loaded)
  const error = useMaterialsStore((s) => s.error)
  const authorization = useMaterialsStore((s) => s.authorization)
  const recommendation = useMaterialsStore((s) => s.recommendation)
  const recommending = useMaterialsStore((s) => s.recommending)
  const setAuthorization = useMaterialsStore((s) => s.setAuthorization)
  const addFiles = useMaterialsStore((s) => s.addFiles)
  const recommend = useMaterialsStore((s) => s.recommend)
  const refresh = useMaterialsStore((s) => s.refresh)
  const navigate = useNavigate()
  const [urlOpen, setUrlOpen] = useState(false)
  const [discoverOpen, setDiscoverOpen] = useState(false)
  const [goal, setGoal] = useState('')

  const pickFiles = async (): Promise<void> => {
    const paths = await pickSupplementFiles()
    if (paths.length) await addFiles(paths)
  }

  const addItems: MenuItem[] = [
    { key: 'file', label: '本地文件…', icon: <FolderOpen size={14} />, hint: 'PDF / 表格 / 文档 / 压缩包', onSelect: () => void pickFiles() },
    { key: 'url', label: '通过链接…', icon: <Link2 size={14} />, onSelect: () => setUrlOpen(true) },
    { key: 'discover', label: '自动查找…', icon: <Search size={14} />, hint: '在论文页面和出版商站点里找', onSelect: () => setDiscoverOpen(true) }
  ]

  const granted = authorization?.granted ?? false
  const byCategory = SUPPLEMENT_CATEGORIES.map((c) => ({ c, list: items.filter((a) => a.category === c) })).filter((g) => g.list.length > 0)
  const doneCount = items.filter((a) => a.download_status === 'done').length

  return (
    <div className="pd-rpanel-scroll">
      <div className="pd-psec-head pd-mat-head">
        <h3 className="pd-psec-title">补充材料{items.length > 0 ? ` · ${items.length}` : ''}</h3>
        <Menu
          align="end"
          width={230}
          items={addItems}
          trigger={({ toggle }) => (
            <Button size="sm" variant="primary" icon={<Plus size={13} />} onClick={toggle}>
              添加
            </Button>
          )}
        />
      </div>

      <div className="pd-mat-auth">
        <div className="pd-mat-auth-text">
          <b>{granted ? '本次允许 AI 读取补充材料' : '默认不让 AI 读取补充材料'}</b>
          {granted
            ? '提问时会检索补充材料正文，用到的片段会随问题发送给你配置的模型，回答里以 [S1P12] 这样的标记引用。关闭这篇论文后恢复为默认。'
            : authorization?.decision === 'deny'
              ? '你选择了不使用。AI 只会基于论文正文回答。'
              : '开启前，AI 只基于论文正文回答；材料不会被读取，也不会被发送出去。每次打开论文时都会重新询问。'}
        </div>
        <Switch checked={granted} onChange={(v) => void setAuthorization(v)} label={granted ? '已允许' : '未允许'} disabled={!loaded} />
      </div>

      {!loaded && (
        <div style={{ marginTop: 16 }}>
          <Skeleton height={56} />
        </div>
      )}
      {loaded && error && items.length === 0 && (
        <div className="pd-field-error" style={{ marginTop: 12 }}>
          读取补充材料失败：{error}{' '}
          <button type="button" className="pd-mat-select" onClick={() => void refresh()}>
            重试
          </button>
        </div>
      )}

      {loaded && !error && items.length === 0 && (
        <EmptyState
          icon={<FileText size={20} />}
          title="还没有补充材料"
          description="附录 PDF、数据集、代码包、实验配置都可以放在这里。AI 只在你允许之后才会读取它们。"
          action={
            <div className="pd-row" style={{ justifyContent: 'center' }}>
              <Button variant="primary" icon={<Search size={14} />} onClick={() => setDiscoverOpen(true)}>
                自动查找
              </Button>
              <Button icon={<FolderOpen size={14} />} onClick={() => void pickFiles()}>
                选择本地文件
              </Button>
            </div>
          }
        />
      )}

      {items.length > 0 && doneCount > 0 && (
        <section style={{ marginTop: 'var(--sp-lg)' }}>
          <div className="pd-row" style={{ gap: 8 }}>
            <Input
              value={goal}
              placeholder="你想弄清什么？可选，例如：时间复杂度的证明"
              onChange={setGoal}
              onEnter={() => void recommend(goal)}
            />
            <Button size="sm" icon={<ListOrdered size={13} />} loading={recommending} onClick={() => void recommend(goal)}>
              推荐阅读顺序
            </Button>
          </div>
          {recommendation && recommendation.items.length > 0 && (
            <>
              <ol className="pd-mat-rec">
                {recommendation.items.map((r) => {
                  const a = items.find((x) => x.id === r.asset_id)
                  if (!a) return null
                  return (
                    <li key={r.asset_id}>
                      <div>
                        <button
                          type="button"
                          className="pd-mat-name"
                          disabled={!canOpen(a)}
                          style={{ fontSize: 13 }}
                          onClick={() => navigate(a.child_paper_id ? `/reader/${a.child_paper_id}` : `/materials/${a.id}`)}
                        >
                          {a.filename}
                        </button>
                        <span className="pd-mat-rec-reason">{r.reason}</span>
                      </div>
                    </li>
                  )
                })}
              </ol>
              <p className="pd-aux pd-weak" style={{ marginTop: 6 }}>
                {recommendation.source === 'ai' ? `由 ${recommendation.model ?? 'AI'} 排序` : '按规则排序，未使用 AI'}
                {recommendation.note ? ` · ${recommendation.note}` : ''}
              </p>
            </>
          )}
        </section>
      )}

      {byCategory.map(({ c, list }) => (
        <section key={c}>
          <div className="pd-mat-group">
            {c} · {list.length}
          </div>
          {list.map((a) => (
            <AssetRow key={a.id} a={a} />
          ))}
        </section>
      ))}

      <AddUrlDialog open={urlOpen} onClose={() => setUrlOpen(false)} />
      <DiscoverDialog open={discoverOpen} onClose={() => setDiscoverOpen(false)} />
    </div>
  )
}
