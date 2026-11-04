import { useEffect, useMemo, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { Check, MoreHorizontal, Plus, Sparkles } from 'lucide-react'
import { Button, Dialog, EmptyState, IconButton, Input, Menu, Select, Spinner, toast } from '@/components/ui'
import { errorMessage } from '@/lib/api'
import { codeApi } from '@/lib/api.code'
import type { CodeSymbol, PaperCodeLink, PaperModule } from '@/lib/types.code'
import { CONFIDENCE_LABEL, groupLinksByModule, LINK_STATUS_LABEL, lineRange, METHOD_LABEL, parseLoc } from './codeUtils'
import { openPaperAt, useCodeStore } from './codeStore'
import { ViewFrame, ViewHead } from './ViewFrame'

function PaperSide({ name, description, pages, section, ords }: { name: string; description: string; pages: number[]; section: string | null; ords: number[] }) {
  const navigate = useNavigate()
  const paperId = useCodeStore((s) => s.paperId)
  return (
    <div className="pd-map-paper">
      <div className="pd-map-name">{name}</div>
      <div className="pd-aux pd-muted">
        {section ? `${section} · ` : ''}
        {pages.length > 0 && paperId ? (
          <button type="button" className="pd-link" onClick={() => void openPaperAt(navigate, paperId, { page: pages[0], ord: ords[0] })}>
            论文第 {pages.join('、')} 页
          </button>
        ) : (
          '位置未知'
        )}
      </div>
      {description && <p className="pd-map-desc">{description}</p>}
    </div>
  )
}

function LinkRow({ link }: { link: PaperCodeLink }) {
  const setLinkStatus = useCodeStore((s) => s.setLinkStatus)
  const goToLines = useCodeStore((s) => s.goToLines)
  const goToSymbolId = useCodeStore((s) => s.goToSymbolId)
  const [more, setMore] = useState(false)
  const also = link.basis.also_in ?? []
  const open = (): void => {
    if (link.symbol_id !== null) void goToSymbolId(link.symbol_id)
    else void goToLines(link.path, link.line_start, link.line_end)
  }
  return (
    <div className="pd-map-link" data-status={link.status}>
      <div className="pd-map-link-top">
        <button type="button" className="pd-map-sym pd-ellipsis" onClick={open} title="在代码中查看">
          {link.symbol?.qualname ?? '未关联到符号'}
        </button>
        <span className="pd-map-loc pd-aux pd-muted pd-ellipsis">
          {link.path}:{lineRange(link.line_start, link.line_end)}
        </span>
      </div>
      <div className="pd-aux pd-map-tags">
        <span className="pd-map-conf" data-likely={link.confidence === 'likely' || undefined}>
          {CONFIDENCE_LABEL[link.confidence]}
        </span>
        <span className="pd-muted">
          {METHOD_LABEL[link.method]} · {LINK_STATUS_LABEL[link.status]}
        </span>
      </div>
      {link.basis.text && <p className="pd-aux pd-map-basis">{link.basis.text}</p>}
      {link.basis.matched_terms.length > 0 && link.method === 'lexical' && <p className="pd-aux pd-muted">匹配词：{link.basis.matched_terms.slice(0, 8).join('、')}</p>}
      {also.length > 0 && (
        <div className="pd-aux pd-muted">
          另有 {also.length} 处相同名称的实现，仓库里有多份拷贝或变体：
          <button type="button" className="pd-link" onClick={() => setMore(!more)}>
            {more ? '收起' : '展开'}
          </button>
          {more && (
            <ul className="pd-map-also">
              {also.map((loc) => {
                const p = parseLoc(loc)
                return (
                  <li key={loc}>
                    {p ? (
                      <button type="button" className="pd-link" onClick={() => void goToLines(p.path, p.a, p.b)}>
                        {loc}
                      </button>
                    ) : (
                      loc
                    )}
                  </li>
                )
              })}
            </ul>
          )}
        </div>
      )}
      <div className="pd-map-actions">
        {link.status !== 'confirmed' && (
          <Button size="sm" icon={<Check size={13} />} onClick={() => void setLinkStatus(link.id, 'confirmed')}>
            确认对应
          </Button>
        )}
        {link.status !== 'rejected' ? (
          <Button size="sm" variant="ghost" onClick={() => void setLinkStatus(link.id, 'rejected')}>
            不对应
          </Button>
        ) : (
          <Button size="sm" variant="ghost" onClick={() => void setLinkStatus(link.id, 'suggested')}>
            恢复
          </Button>
        )}
      </div>
    </div>
  )
}

function ManualDialog({ open, onClose, modules }: { open: boolean; onClose: () => void; modules: PaperModule[] }) {
  const repoId = useCodeStore((s) => s.repoId)
  const loadMapping = useCodeStore((s) => s.loadMapping)
  const [moduleName, setModuleName] = useState('')
  const [q, setQ] = useState('')
  const [results, setResults] = useState<CodeSymbol[]>([])
  const [picked, setPicked] = useState<CodeSymbol | null>(null)
  const [busy, setBusy] = useState(false)

  useEffect(() => {
    if (open) {
      setModuleName(modules[0]?.name ?? '')
      setQ('')
      setResults([])
      setPicked(null)
    }
  }, [open, modules])

  useEffect(() => {
    if (!open || !repoId || !q.trim()) {
      setResults([])
      return
    }
    const t = window.setTimeout(() => {
      codeApi
        .symbols(repoId, q.trim(), undefined, 30)
        .then((r) => setResults(r.items.filter((s) => s.kind !== 'module')))
        .catch(() => setResults([]))
    }, 200)
    return () => window.clearTimeout(t)
  }, [q, open, repoId])

  const submit = async (): Promise<void> => {
    if (!repoId || !picked || !moduleName.trim()) return
    const mod = modules.find((m) => m.name === moduleName)
    setBusy(true)
    try {
      await codeApi.addLink(repoId, moduleName.trim(), picked.id, mod?.passage_ords)
      await loadMapping()
      toast.success('已手动关联', `${moduleName} ↔ ${picked.qualname}`)
      onClose()
    } catch (err) {
      toast.error('关联失败', errorMessage(err))
    } finally {
      setBusy(false)
    }
  }

  return (
    <Dialog
      open={open}
      onClose={onClose}
      title="手动关联论文方法与代码"
      width={520}
      footer={
        <>
          <Button onClick={onClose}>取消</Button>
          <Button variant="primary" disabled={!picked || !moduleName.trim()} loading={busy} onClick={() => void submit()}>
            创建关联
          </Button>
        </>
      }
    >
      <div className="pd-field">
        <label className="pd-field-label" htmlFor="pd-map-mod">
          论文里的方法/模块
        </label>
        {modules.length > 0 ? (
          <Select id="pd-map-mod" value={moduleName} onChange={setModuleName} options={modules.map((m) => ({ value: m.name, label: m.name }))} />
        ) : (
          <Input value={moduleName} onChange={setModuleName} placeholder="例如：编码器" aria-label="模块名称" />
        )}
      </div>
      <div className="pd-field">
        <label className="pd-field-label" htmlFor="pd-map-sym">
          代码里的函数或类
        </label>
        <Input id="pd-map-sym" value={q} onChange={setQ} placeholder="输入名称搜索，例如 Train" clearable />
        <ul className="pd-map-results">
          {results.map((s) => (
            <li key={s.id}>
              <button type="button" className="pd-map-result" data-active={picked?.id === s.id || undefined} onClick={() => setPicked(s)}>
                <span className="pd-ellipsis">{s.qualname}</span>
                <span className="pd-aux pd-muted pd-ellipsis">
                  {s.path}:{s.line_start}
                </span>
              </button>
            </li>
          ))}
          {q.trim() && results.length === 0 && <li className="pd-aux pd-muted" style={{ padding: '6px 0' }}>没有匹配的符号</li>}
        </ul>
      </div>
    </Dialog>
  )
}

/** 已经自动生成过一次的仓库（只试一次，免得一直重复）。 */
const autoTried = new Set<string>()

export function MappingView() {
  const modules = useCodeStore((s) => s.modules)
  const links = useCodeStore((s) => s.links)
  const loaded = useCodeStore((s) => s.mappingLoaded)
  const associating = useCodeStore((s) => s.associating)
  const loadMapping = useCodeStore((s) => s.loadMapping)
  const associate = useCodeStore((s) => s.associate)
  const [showRejected, setShowRejected] = useState(false)
  const [manual, setManual] = useState(false)

  useEffect(() => {
    if (!loaded) void loadMapping()
  }, [loaded, loadMapping])

  // 论文模块已经有了、还没有任何关联：自动做一次本地词法匹配，打开就能看到候选，不用先去找按钮。只在这个仓库上试一次。
  const repoId = useCodeStore((s) => s.repoId)
  useEffect(() => {
    if (!loaded || !repoId || associating || links.length > 0 || modules.length === 0 || autoTried.has(repoId)) return
    autoTried.add(repoId)
    void associate({ auto: true })
  }, [loaded, repoId, associating, links.length, modules.length, associate])

  const groups = useMemo(() => groupLinksByModule(links, showRejected), [links, showRejected])
  const linked = new Set(groups.map((g) => g.name))
  const unlinked = modules.filter((m) => !linked.has(m.name))
  const confirmed = links.filter((l) => l.status === 'confirmed').length
  const pending = links.filter((l) => l.status === 'suggested').length

  return (
    <ViewFrame>
      <ViewHead
        title="论文方法 ↔ 代码"
        desc={`${modules.length} 个论文模块 · ${links.length} 条关联 · 已确认 ${confirmed} · 待确认 ${pending}。关联只是建议，不是结论：“很可能对应”也需要你核对，确认后才算数；没有找到的方法不会被硬凑一个答案，重新生成时你确认或拒绝过的关联会保留。`}
        actions={
          <>
            <Menu
              align="end"
              trigger={({ toggle, open }) => <IconButton icon={<MoreHorizontal size={17} />} label="更多" selected={open} onClick={toggle} />}
              items={[
                { key: 'manual', label: '手动关联…', icon: <Plus size={15} />, onSelect: () => setManual(true) },
                { key: 'rejected', label: showRejected ? '隐藏已拒绝的关联' : '显示已拒绝的关联', onSelect: () => setShowRejected(!showRejected) }
              ]}
            />
            <Button variant="primary" icon={<Sparkles size={14} />} loading={associating} onClick={() => void associate()}>
              {links.length > 0 ? '重新生成关联' : '用 AI 生成关联'}
            </Button>
          </>
        }
      />

      {!loaded ? (
        <div className="pd-map-loading">
          <Spinner size={18} />
        </div>
      ) : groups.length === 0 && unlinked.length === 0 ? (
        <EmptyState
          title="还没有关联"
          description="点击“生成关联”，让 AI 从论文的方法描述里找出模块，并到代码里找对应的实现。没有配置 AI 时只做词汇匹配，且都标为“可能相关”。"
          action={
            <Button variant="primary" icon={<Sparkles size={14} />} loading={associating} onClick={() => void associate()}>
              生成关联
            </Button>
          }
        />
      ) : (
        <div className="pd-map-list">
          <div className="pd-map-cols pd-aux pd-muted">
            <span>论文里的方法</span>
            <span>代码里的实现</span>
          </div>
          {groups.map((g) => (
            <section key={g.key} className="pd-map-row">
              <PaperSide name={g.name} description={g.description} pages={g.pages} section={g.section} ords={g.links[0]?.module.passage_ords ?? []} />
              <div className="pd-map-targets">
                {g.links.map((l) => (
                  <LinkRow key={l.id} link={l} />
                ))}
              </div>
            </section>
          ))}
          {unlinked.map((m) => (
            <section key={m.name} className="pd-map-row">
              <PaperSide name={m.name} description={m.description} pages={m.pages} section={m.section} ords={m.passage_ords} />
              <div className="pd-map-targets">
                <p className="pd-muted pd-map-none">
                  {associating ? '正在匹配…' : links.length === 0 ? '还没有匹配到。点右上角“用 AI 生成关联”可以让 AI 再找一遍，也可以手动关联。' : '没有找到对应的代码。可能这部分没有开源，或者命名差异太大；也可以手动关联。'}
                </p>
              </div>
            </section>
          ))}
        </div>
      )}
      <ManualDialog open={manual} onClose={() => setManual(false)} modules={modules} />
    </ViewFrame>
  )
}
