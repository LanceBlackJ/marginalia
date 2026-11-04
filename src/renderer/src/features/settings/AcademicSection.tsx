import { useEffect, useState } from 'react'
import { KeyRound } from 'lucide-react'
import { Button, Input, Skeleton, Switch, Tag, toast } from '@/components/ui'
import { errorMessage } from '@/lib/api'
import { discoveryApi } from '@/lib/api.discovery'
import { clearS2Key, hasS2Key, openExternal, setS2Key } from '@/lib/native'
import { hasBridge } from '@/lib/backend'
import type { DiscoverySettings, SourceInfo, SourceReport } from '@/lib/types.discovery'
import '@/features/discovery/discovery.css'
import { SettingRow } from './SettingRow'

/** 学术搜索：数据来源说明、真实连通性测试、语义向量设置。 */
export function AcademicSection() {
  const [sources, setSources] = useState<SourceInfo[] | null>(null)
  const [settings, setSettings] = useState<DiscoverySettings | null>(null)
  const [model, setModel] = useState('')
  const [testing, setTesting] = useState(false)
  const [report, setReport] = useState<SourceReport[] | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [s2Saved, setS2Saved] = useState(false)
  const [s2Input, setS2Input] = useState('')
  const [s2Busy, setS2Busy] = useState(false)

  useEffect(() => {
    hasS2Key()
      .then(setS2Saved)
      .catch(() => undefined)
    discoveryApi
      .sources()
      .then((r) => setSources(r.items))
      .catch((err) => setError(errorMessage(err)))
    discoveryApi
      .settings()
      .then((s) => {
        setSettings(s)
        setModel(s.embedding_model)
      })
      .catch(() => undefined)
  }, [])

  const test = async (): Promise<void> => {
    setTesting(true)
    setReport(null)
    try {
      const r = await discoveryApi.search({ query: 'network science', mode: 'comprehensive', limit: 3, ai: false })
      setReport(r.sources)
    } catch (err) {
      toast.error('连通性测试失败', errorMessage(err))
    } finally {
      setTesting(false)
    }
  }

  const saveS2 = async (): Promise<void> => {
    if (!s2Input.trim()) return
    setS2Busy(true)
    try {
      await setS2Key(s2Input.trim())
      setS2Input('')
      setS2Saved(true)
      toast.success('Semantic Scholar API Key 已保存')
    } catch (err) {
      toast.error('保存失败', errorMessage(err))
    } finally {
      setS2Busy(false)
    }
  }

  const removeS2 = async (): Promise<void> => {
    setS2Busy(true)
    try {
      await clearS2Key()
      setS2Saved(false)
      toast.success('已删除 Semantic Scholar API Key')
    } catch (err) {
      toast.error('删除失败', errorMessage(err))
    } finally {
      setS2Busy(false)
    }
  }

  const save = async (patch: Partial<DiscoverySettings>): Promise<void> => {
    try {
      setSettings(await discoveryApi.putSettings(patch))
    } catch (err) {
      toast.error('保存失败', errorMessage(err))
    }
  }

  return (
    <div>
      <SettingRow title="检索来源" description="发现论文时会同时查询这些公开学术数据源，去重后合并。不需要账号；若某个来源暂时不可用，结果页会如实标出，不会因此失败。">
        {error && <p className="pd-status-text pd-status-text--danger">{error}</p>}
        {!sources && !error && <Skeleton height={80} />}
        <ul className="pd-dc-list">
          {sources?.map((s) => (
            <li key={s.id}>
              <div>
                <strong>{s.name}</strong> <span className="pd-dc-sub" style={{ display: 'inline' }}>{s.scope}</span>
              </div>
              <div className="pd-dc-sub">提供：{s.gives}</div>
              {s.note && <div className="pd-dc-sub">{s.note}</div>}
            </li>
          ))}
        </ul>
      </SettingRow>

      <SettingRow
        title="Semantic Scholar API Key（可选）"
        description={
          '不填也能用，但 Semantic Scholar 对没有 Key 的请求限流很严，经常被跳过。可以免费申请一个：申请后粘贴在这里，之后检索会更稳定。' +
          (hasBridge() ? 'Key 由系统安全存储加密保存，界面不会显示明文，只会发给 api.semanticscholar.org。' : 'Web 调试模式：Key 只保存在本地服务的内存里，重启后要重新填。')
        }
      >
        <div>
          <div className="pd-row">
            <Input
              type="password"
              autoComplete="off"
              value={s2Input}
              onChange={setS2Input}
              onEnter={() => void saveS2()}
              icon={<KeyRound size={14} />}
              placeholder={s2Saved ? '输入新 Key 以替换' : '粘贴你的 Semantic Scholar API Key'}
              aria-label="Semantic Scholar API Key"
            />
            <Button variant="primary" loading={s2Busy} disabled={!s2Input.trim()} onClick={() => void saveS2()}>
              保存
            </Button>
            {s2Saved && (
              <Button variant="ghost" disabled={s2Busy} onClick={() => void removeS2()}>
                删除
              </Button>
            )}
          </div>
          <p className="pd-aux pd-muted" style={{ marginTop: 'var(--sp-sm)' }}>
            {s2Saved ? <Tag tone="success">已保存</Tag> : <Tag>未保存</Tag>}{' '}
            <button type="button" className="pd-link" onClick={() => void openExternal('https://www.semanticscholar.org/product/api#api-key-form')}>
              去 Semantic Scholar 申请免费 API Key
            </button>
          </p>
        </div>
      </SettingRow>

      <SettingRow title="连通性测试" description="用一次真实的小检索访问各来源，不使用 AI，显示每个来源是否可达及耗时。部分来源可能受网络影响不可用。">
        <div>
          <Button variant="secondary" loading={testing} onClick={() => void test()}>
            测试各来源
          </Button>
          {report && (
            <ul className="pd-dc-list" style={{ marginTop: 'var(--sp-md)' }}>
              {report.map((r) => (
                <li key={r.name}>
                  <Tag tone={r.ok ? 'success' : 'warning'}>{r.ok ? '可用' : '不可用'}</Tag> {r.name}
                  <span className="pd-dc-sub" style={{ display: 'inline', marginLeft: 8 }}>
                    {r.ok ? `返回 ${r.count} 条，${(r.ms / 1000).toFixed(1)} 秒` : (r.error ?? '未知原因')}
                  </span>
                </li>
              ))}
            </ul>
          )}
        </div>
      </SettingRow>

      <SettingRow
        title="语义向量排序"
        description="用嵌入模型计算查询与论文摘要的语义相似度，作为排序依据之一。只发送公开论文的标题与摘要，不包含你的论文内容；需要先在“AI 模型”中配置提供商。"
      >
        {settings ? (
          <div className="pd-fields">
            <Switch checked={settings.use_embeddings} onChange={(v) => void save({ use_embeddings: v })} label="启用语义向量" />
            <div style={{ display: 'flex', gap: 'var(--sp-sm)', alignItems: 'center' }}>
              <Input value={model} onChange={setModel} aria-label="嵌入模型名称" placeholder="例如 bge-m3" />
              <Button variant="secondary" disabled={!model.trim() || model.trim() === settings.embedding_model} onClick={() => void save({ embedding_model: model.trim() })}>
                保存
              </Button>
            </div>
          </div>
        ) : (
          <Skeleton height={60} />
        )}
      </SettingRow>
    </div>
  )
}
