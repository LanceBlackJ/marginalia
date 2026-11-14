import { useCallback, useEffect, useState } from 'react'
import { AlertCircle, CheckCircle2, KeyRound } from 'lucide-react'
import { Button, Field, Input, Segmented, Select, Skeleton, Switch, Tag, toast } from '@/components/ui'
import { api, errorMessage } from '@/lib/api'
import { hasBridge } from '@/lib/backend'
import { setApiKey } from '@/lib/native'
import type { AISettings, AITestResult, ModelMode, ModelTestResult, ProviderKind } from '@/lib/types'
import { AI_SETTINGS_CHANGED } from './events'
import { SettingRow, SettingsBlock, SettingsPanel } from './SettingRow'

/** 表单草稿：数值字段用字符串，避免输入过程中被强制转换。 */
interface Draft {
  provider_kind: ProviderKind
  base_url: string
  fast_model: string
  strong_model: string
  vision_model: string
  vision_model_2: string
  mode: ModelMode
  timeout_s: string
  max_retries: string
  temperature: string
  max_output_tokens: string
  thinking: 'auto' | 'on' | 'off'
  context_chars: string
  history_turns: string
  auto_analyze: boolean
  prices: Record<string, { input: string; output: string }>
  monthly_budget: string
}

const DEFAULT_URLS: Record<ProviderKind, string> = {
  openai_compatible: 'https://api.openai.com/v1',
  anthropic: 'https://api.anthropic.com'
}

function toDraft(s: AISettings): Draft {
  const prices: Draft['prices'] = {}
  for (const [model, p] of Object.entries(s.price_per_mtok ?? {})) prices[model] = { input: String(p.input), output: String(p.output) }
  return {
    provider_kind: s.provider_kind,
    base_url: s.base_url,
    fast_model: s.fast_model,
    strong_model: s.strong_model,
    vision_model: s.vision_model,
    vision_model_2: s.vision_model_2 ?? '',
    mode: s.mode,
    timeout_s: String(s.timeout_s),
    max_retries: String(s.max_retries),
    temperature: String(s.temperature),
    max_output_tokens: String(s.max_output_tokens),
    thinking: s.thinking ?? 'auto',
    context_chars: String(s.context_chars ?? 24000),
    history_turns: String(s.history_turns ?? 4),
    auto_analyze: s.auto_analyze,
    prices,
    monthly_budget: s.monthly_budget === null ? '' : String(s.monthly_budget)
  }
}

function num(text: string, label: string, opts: { min?: number; max?: number; int?: boolean } = {}): number {
  const v = Number(text)
  if (text.trim() === '' || !Number.isFinite(v)) throw new Error(`${label}需要填写数字`)
  if (opts.int && !Number.isInteger(v)) throw new Error(`${label}需要是整数`)
  if (opts.min !== undefined && v < opts.min) throw new Error(`${label}不能小于 ${opts.min}`)
  if (opts.max !== undefined && v > opts.max) throw new Error(`${label}不能大于 ${opts.max}`)
  return v
}

function toPayload(d: Draft): Partial<AISettings> {
  const prices: AISettings['price_per_mtok'] = {}
  const models = [d.fast_model, d.strong_model, d.vision_model, d.vision_model_2].map((m) => m.trim()).filter(Boolean)
  for (const model of models) {
    const p = d.prices[model]
    if (!p || (p.input.trim() === '' && p.output.trim() === '')) continue
    prices[model] = { input: num(p.input || '0', `${model} 输入价格`, { min: 0 }), output: num(p.output || '0', `${model} 输出价格`, { min: 0 }) }
  }
  return {
    provider_kind: d.provider_kind,
    base_url: d.base_url.trim(),
    fast_model: d.fast_model.trim(),
    strong_model: d.strong_model.trim(),
    vision_model: d.vision_model.trim(),
    vision_model_2: d.vision_model_2.trim(),
    mode: d.mode,
    timeout_s: num(d.timeout_s, '请求超时', { min: 5, max: 600 }),
    max_retries: num(d.max_retries, '重试次数', { min: 0, max: 6, int: true }),
    temperature: num(d.temperature, '温度', { min: 0, max: 2 }),
    max_output_tokens: num(d.max_output_tokens, '最大输出', { min: 256, max: 64000, int: true }),
    thinking: d.thinking,
    context_chars: num(d.context_chars, '上下文字数', { min: 4000, max: 400000, int: true }),
    history_turns: num(d.history_turns, '带入的对话轮数', { min: 0, max: 20, int: true }),
    auto_analyze: d.auto_analyze,
    price_per_mtok: prices,
    monthly_budget: d.monthly_budget.trim() === '' ? null : num(d.monthly_budget, '月度预算', { min: 0 })
  }
}

function TestRow({ label, r }: { label: string; r: ModelTestResult | null }) {
  if (!r) return <div className="pd-test-result pd-muted">{label}：未配置模型，已跳过</div>
  return (
    <div className="pd-test-result">
      {r.ok ? <CheckCircle2 size={15} color="var(--text-2)" /> : <AlertCircle size={15} color="var(--danger)" />}
      <span>
        {label} {r.model}：{r.ok ? `连接正常${r.latency_ms !== null ? `，${r.latency_ms} ms` : ''}` : (r.error ?? '连接失败')}
      </span>
    </div>
  )
}

export function AIModelSection() {
  const [settings, setSettings] = useState<AISettings | null>(null)
  const [draft, setDraft] = useState<Draft | null>(null)
  const [loadError, setLoadError] = useState<string | null>(null)
  const [saving, setSaving] = useState(false)
  const [saveError, setSaveError] = useState<string | null>(null)
  const [keyInput, setKeyInput] = useState('')
  const [keyBusy, setKeyBusy] = useState(false)
  const [testing, setTesting] = useState(false)
  const [test, setTest] = useState<AITestResult | null>(null)
  const [testError, setTestError] = useState<string | null>(null)

  const load = useCallback(async () => {
    try {
      const s = await api.settings.getAI()
      setSettings(s)
      setDraft(toDraft(s))
      setLoadError(null)
    } catch (err) {
      setLoadError(errorMessage(err))
    }
  }, [])

  useEffect(() => {
    void load()
    const onChanged = (): void => void load()
    window.addEventListener(AI_SETTINGS_CHANGED, onChanged)
    return () => window.removeEventListener(AI_SETTINGS_CHANGED, onChanged)
  }, [load])

  if (loadError || !settings || !draft) {
    // 面板外壳先出现，内容读到后再填
    const state = loadError ? '设置加载失败' : '正在读取…'
    const body = loadError ? (
      <div className="pd-notice pd-notice--danger">设置加载失败：{loadError}</div>
    ) : (
      <div style={{ display: 'flex', flexDirection: 'column', gap: 12 }}>
        <Skeleton height={32} />
        <Skeleton height={32} />
      </div>
    )
    return (
      <>
        <SettingsPanel id="models" title="连接与模型" summary={state}>
          {body}
        </SettingsPanel>
        <SettingsPanel id="cost" title="费用与预算" summary={state}>
          {body}
        </SettingsPanel>
      </>
    )
  }

  const set = (patch: Partial<Draft>): void => setDraft({ ...draft, ...patch })
  const dirty = JSON.stringify(draft) !== JSON.stringify(toDraft(settings))
  const modelNames = Array.from(new Set([draft.fast_model, draft.strong_model, draft.vision_model, draft.vision_model_2].map((m) => m.trim()).filter(Boolean)))

  const save = async (): Promise<void> => {
    setSaveError(null)
    setSaving(true)
    try {
      const next = await api.settings.putAI(toPayload(draft))
      setSettings(next)
      setDraft(toDraft(next))
      toast.success('AI 设置已保存')
    } catch (err) {
      setSaveError(errorMessage(err))
    } finally {
      setSaving(false)
    }
  }

  const saveKey = async (): Promise<void> => {
    if (!keyInput.trim()) return
    setKeyBusy(true)
    try {
      await setApiKey(keyInput.trim())
      setKeyInput('')
      toast.success('API Key 已保存')
      window.dispatchEvent(new Event(AI_SETTINGS_CHANGED))
      await load()
    } catch (err) {
      toast.error('保存 API Key 失败', errorMessage(err))
    } finally {
      setKeyBusy(false)
    }
  }

  const runTest = async (): Promise<void> => {
    setTesting(true)
    setTest(null)
    setTestError(null)
    try {
      setTest(await api.settings.testAI())
    } catch (err) {
      setTestError(errorMessage(err))
    } finally {
      setTesting(false)
    }
  }

  const saveBar = (
      <div className="pd-savebar">
        <Button variant="primary" loading={saving} disabled={!dirty} onClick={() => void save()}>
          保存设置
        </Button>
        <Button disabled={!dirty || saving} onClick={() => setDraft(toDraft(settings))}>
          还原
        </Button>
        <Button loading={testing} disabled={dirty || !settings.configured} disabledReason={dirty ? '请先保存设置再测试' : '请先设置 API Key 并填写至少一个模型'} onClick={() => void runTest()}>
          连接测试
        </Button>
        {saveError && (
          <span style={{ color: 'var(--danger)' }} className="pd-aux">
            {saveError}
          </span>
        )}
      </div>
  )
  const nPrices = Object.keys(draft.prices ?? {}).length
  const modelSummary = !settings.has_api_key
    ? '未保存 API Key'
    : !(settings.fast_model || settings.strong_model)
      ? '已保存密钥，还没有填写模型'
      : `已连接 · 快速 ${settings.fast_model || '未设置'} · 强 ${settings.strong_model || '未设置'}`
  const costSummary = `已设 ${nPrices} 个模型的价格 · 月预算 ${settings.monthly_budget == null ? '未设置' : `${settings.monthly_budget} 元`}`

  return (
    <>
      <SettingsPanel id="models" title="连接与模型" summary={modelSummary} defaultOpen={!settings.has_api_key}>
      <SettingsBlock id="connection" title="连接" description="模型服务的接口格式、地址和访问密钥。">
      <SettingRow title="接口类型" description="选择模型提供商使用的接口格式。">
        <Select
          aria-label="接口类型"
          value={draft.provider_kind}
          onChange={(v) => {
            const kind = v as ProviderKind
            const wasDefault = draft.base_url === DEFAULT_URLS[draft.provider_kind]
            set({ provider_kind: kind, base_url: wasDefault ? DEFAULT_URLS[kind] : draft.base_url }) // 留空就一直留空，不替用户填地址
          }}
          options={[
            { value: 'openai_compatible', label: 'OpenAI 兼容接口' },
            { value: 'anthropic', label: 'Anthropic Messages' }
          ]}
        />
      </SettingRow>

      <SettingRow title="API 地址" description={`服务的 Base URL，需要自己填写（默认留空），例如 ${DEFAULT_URLS[draft.provider_kind]}`}>
        <Input value={draft.base_url} onChange={(v) => set({ base_url: v })} placeholder={DEFAULT_URLS[draft.provider_kind]} aria-label="API Base URL" />
      </SettingRow>

      <SettingRow
        title={
          <span className="pd-row">
            API Key
            {settings.has_api_key ? <Tag>已保存</Tag> : <Tag tone="warning">未设置</Tag>}
          </span>
        }
        description={
          hasBridge()
            ? 'Key 由系统安全存储加密保存，界面不会显示明文；本地服务只在内存中持有，不会写入磁盘或日志。'
            : 'Web 调试模式：Key 仅保存在本地服务的内存中，重启后需重新填写。'
        }
      >
        <div className="pd-row">
          <Input
            type="password"
            autoComplete="off"
            value={keyInput}
            onChange={setKeyInput}
            onEnter={() => void saveKey()}
            icon={<KeyRound size={14} />}
            placeholder={settings.has_api_key ? '输入新 Key 以替换' : 'sk-…'}
            aria-label="API Key"
          />
          <Button variant="primary" loading={keyBusy} disabled={!keyInput.trim()} onClick={() => void saveKey()}>
            保存
          </Button>
        </div>
      </SettingRow>
      </SettingsBlock>

      <SettingsBlock id="models" title="模型" description="翻译、解释、分析各用哪个模型，以及如何调度。">
      <SettingRow title="模型" description="快速模型处理翻译、基础解释和简单摘要；强模型处理方法分析、公式和全文逻辑。视觉模型仅在提供商需要单独配置时填写。默认值是实测中表现稳定的组合；换用其它服务商时请改成对应的模型名。">
        <div className="pd-fields">
          <Field label="快速模型">{(id) => <Input id={id} value={draft.fast_model} onChange={(v) => set({ fast_model: v })} placeholder="模型名称" />}</Field>
          <Field label="强模型">{(id) => <Input id={id} value={draft.strong_model} onChange={(v) => set({ strong_model: v })} placeholder="模型名称" />}</Field>
          <Field label="视觉模型">{(id) => <Input id={id} value={draft.vision_model} onChange={(v) => set({ vision_model: v })} placeholder="可留空" />}</Field>
          <Field label="第二视觉模型">{(id) => <Input id={id} value={draft.vision_model_2} onChange={(v) => set({ vision_model_2: v })} placeholder="可留空：行内公式用两个模型互相对照" />}</Field>
        </div>
      </SettingRow>

      <SettingRow title="调度模式" description="自动：简单任务用快速模型，复杂任务用强模型，快速模型不可靠时自动升级。">
        <div>
          <Segmented<ModelMode>
            aria-label="调度模式"
            value={draft.mode}
            onChange={(v) => set({ mode: v })}
            options={[
              { value: 'auto', label: '自动' },
              { value: 'fast', label: '始终快速模型' },
              { value: 'strong', label: '始终强模型' }
            ]}
          />
        </div>
      </SettingRow>

      </SettingsBlock>

      <SettingsBlock id="advanced" title="高级" description="自动分析、超时、重试、温度和输出长度。一般不需要改。" advanced>
      <SettingRow title="自动分析" description="开启后，导入论文会自动生成整体分析，每篇都会产生模型调用费用。">
        <Switch checked={draft.auto_analyze} onChange={(v) => set({ auto_analyze: v })} label={draft.auto_analyze ? '已开启' : '已关闭'} />
      </SettingRow>

      <SettingRow title="请求参数" description="超时、重试、温度与单次回答的最大输出长度。最大输出默认 64000，只按实际生成的字数计费；模型或网关不支持这么大时，会自动降到它允许的上限并记住。">
        <div className="pd-fields pd-fields--2">
          <Field label="请求超时（秒）">{(id) => <Input id={id} type="number" min={5} value={draft.timeout_s} onChange={(v) => set({ timeout_s: v })} />}</Field>
          <Field label="失败重试次数">{(id) => <Input id={id} type="number" min={0} max={6} value={draft.max_retries} onChange={(v) => set({ max_retries: v })} />}</Field>
          <Field label="温度">{(id) => <Input id={id} type="number" step={0.1} min={0} max={2} value={draft.temperature} onChange={(v) => set({ temperature: v })} />}</Field>
          <Field label="最大输出 Token">{(id) => <Input id={id} type="number" min={256} value={draft.max_output_tokens} onChange={(v) => set({ max_output_tokens: v })} />}</Field>
        </div>
      </SettingRow>
      <SettingRow title="思考模式" description="回答前是否先让模型“想一想”。开启回答更稳但更慢、更费；关闭更快更省。“按模型默认”不发送任何相关参数。提问框里的小按钮可以单次覆盖这个设置。部分服务商不支持这个开关，会被自动忽略。">
        <Select
          aria-label="思考模式"
          value={draft.thinking}
          onChange={(v) => set({ thinking: v as 'auto' | 'on' | 'off' })}
          options={[
            { value: 'auto', label: '按模型默认' },
            { value: 'on', label: '开启思考' },
            { value: 'off', label: '关闭思考' }
          ]}
        />
      </SettingRow>

      <SettingRow title="上下文" description="每次提问带给模型多少内容。字数越大，回答越能参考全文，但更慢、更费；要小于所用模型的上下文窗口（约 1 个汉字 = 1～2 个 Token）。分析、思维导图、出题的预算同比缩放。">
        <div className="pd-fields pd-fields--2">
          <Field label="带入的论文原文（字）">{(id) => <Input id={id} type="number" min={4000} step={1000} value={draft.context_chars} onChange={(v) => set({ context_chars: v })} />}</Field>
          <Field label="带入的前几轮问答">{(id) => <Input id={id} type="number" min={0} max={20} value={draft.history_turns} onChange={(v) => set({ history_turns: v })} />}</Field>
        </div>
      </SettingRow>
      </SettingsBlock>

        {saveBar}

      {(test || testError) && (
        <div style={{ display: 'flex', flexDirection: 'column', gap: 6, marginTop: 'var(--sp-md)' }}>
          {testError && <div className="pd-notice pd-notice--danger">{testError}</div>}
          {test && (
            <>
              <TestRow label="快速模型" r={test.fast} />
              <TestRow label="强模型" r={test.strong} />
            </>
          )}
        </div>
      )}
      </SettingsPanel>

      <SettingsPanel id="cost" title="费用与预算" summary={costSummary}>
      <SettingsBlock id="prices" title="费用" description="按每百万 Token 的价格估算成本，并可设月度预算提醒。">
      <SettingRow title="价格" description="每百万 Token 的价格，用来估算成本。默认模型的价格已按校园网关价目表填好，单位为元；改用其它服务商时请改成对应的价格和币种。没有价格的模型只统计调用次数和 Token 用量。">
        {modelNames.length === 0 ? (
          <p className="pd-muted pd-aux">先填写模型名称，再设置价格。</p>
        ) : (
          <div className="pd-fields">
            {modelNames.map((m) => (
              <div key={m} className="pd-price-row">
                <span className="pd-ellipsis" title={m}>
                  {m}
                </span>
                <Input
                  type="number"
                  min={0}
                  aria-label={`${m} 输入价格`}
                  placeholder="输入 / 百万"
                  value={draft.prices[m]?.input ?? ''}
                  onChange={(v) => set({ prices: { ...draft.prices, [m]: { input: v, output: draft.prices[m]?.output ?? '' } } })}
                />
                <Input
                  type="number"
                  min={0}
                  aria-label={`${m} 输出价格`}
                  placeholder="输出 / 百万"
                  value={draft.prices[m]?.output ?? ''}
                  onChange={(v) => set({ prices: { ...draft.prices, [m]: { input: draft.prices[m]?.input ?? '', output: v } } })}
                />
              </div>
            ))}
          </div>
        )}
      </SettingRow>

      <SettingRow title="月度预算提醒" description="与价格同币种。用量估算超过预算时，会在“API 管理”中提醒；留空表示不设预算。">
        <div style={{ maxWidth: 220 }}>
          <Input type="number" min={0} value={draft.monthly_budget} onChange={(v) => set({ monthly_budget: v })} placeholder="不设预算" aria-label="月度预算" />
        </div>
      </SettingRow>
      </SettingsBlock>

        {saveBar}
      </SettingsPanel>
    </>
  )
}
