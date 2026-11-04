import { useEffect, useState } from 'react'
import { ShieldCheck } from 'lucide-react'
import { api } from '@/lib/api'
import { formatBytes, formatNumber } from '@/lib/format'
import type { SystemInfo, UsageSummary } from '@/lib/types'
import { usePrefs, THEMES } from '@/stores/prefsStore'
import { FONT_PRESETS, matchPreset } from '@/lib/fontPlan'
import { SHORTCUTS as READER_SHORTCUTS } from '@/features/reader/ShortcutHelp'
import { AcademicSection } from './AcademicSection'
import { ProfileSection } from './ProfileSection'
import { SettingsBlock, SettingsPanel } from './SettingRow'
import { AppearanceSection, CodeExecutionSection, FilesSection, GeneralSection, ShortcutsSection } from './SimpleSections'
import { UsageSection } from './UsageSection'

let infoPromise: Promise<SystemInfo> | null = null
/** 系统信息只读一次，“数据与存储”和“关于”两个面板的摘要共用。 */
function useSystemInfo(): SystemInfo | null {
  const [info, setInfo] = useState<SystemInfo | null>(null)
  useEffect(() => {
    infoPromise ??= api.systemInfo()
    let alive = true
    infoPromise.then((i) => alive && setInfo(i)).catch(() => (infoPromise = null))
    return () => {
      alive = false
    }
  }, [])
  return info
}

function currentMonth(): string {
  const d = new Date()
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}`
}

export function AppearancePanel() {
  const prefs = usePrefs()
  const theme = THEMES.find((t) => t.value === prefs.theme)?.label ?? prefs.theme
  const id = matchPreset(prefs.fontPlan)
  const font = id === 'custom' ? '自定义' : (FONT_PRESETS.find((p) => p.id === id)?.label ?? '自定义')
  return (
    <SettingsPanel id="appearance" title="阅读外观" summary={`主题：${theme} · 字体：${font}`}>
      <AppearanceSection />
    </SettingsPanel>
  )
}

export function RetrievalPanel() {
  return (
    <SettingsPanel id="retrieval" title="翻译与检索" summary="学术数据来源 · 语义排序 · 知识画像">
      <SettingsBlock id="academic" title="学术搜索" description="学术数据来源、连通性测试与语义向量排序。">
        <AcademicSection />
      </SettingsBlock>
      <SettingsBlock id="profile" title="知识画像" description="控制知识画像是否参与 AI 解读，以及解释详略。">
        <ProfileSection />
      </SettingsBlock>
    </SettingsPanel>
  )
}

export function UsagePanel() {
  const [data, setData] = useState<UsageSummary | null>(null)
  useEffect(() => {
    let alive = true
    api
      .usage(currentMonth())
      .then((d) => alive && setData(d))
      .catch(() => undefined)
    return () => {
      alive = false
    }
  }, [])
  const summary = !data
    ? '正在读取…'
    : `本月 ${formatNumber(data.calls)} 次调用 · ${data.estimated_cost === null ? '未配置价格' : `约 ${data.estimated_cost.toFixed(2)} 元`}`
  return (
    <SettingsPanel id="usage" title="用量" summary={summary}>
      <SettingsBlock id="api" title="API 用量与成本" description="按模型和任务类型统计，成本按你填写的价格估算。">
        <UsageSection />
      </SettingsBlock>
    </SettingsPanel>
  )
}

export function PrivacyNotePanel() {
  return (
    <SettingsPanel id="privacynote" title="隐私说明" summary="只发送完成当前任务所需的内容">
      <div className="pd-notice" style={{ marginBottom: 'var(--sp-md)' }}>
        <ShieldCheck size={14} style={{ flex: 'none', marginTop: 2 }} />
        <span>
          Marginalia 只会把完成当前任务所需的论文片段、你的问题和选中的文字发送给你配置的云端模型，不会默认上传整篇论文、全部补充材料或整个代码仓库。
        </span>
      </div>
      <dl className="pd-kv">
        <dt>可能发送</dt>
        <dd>你的问题、选中的文字、检索到的论文片段；你勾选后才会带上笔记、补充材料或代码。</dd>
        <dt>公式、图、表</dt>
        <dd>识别时会把对应区域的截图发给视觉模型。</dd>
        <dt>保存位置</dt>
        <dd>全部数据都在本机；每次发送了什么，会记在“发送记录”里。</dd>
        <dt>API Key</dt>
        <dd>保存在系统的加密存储里，不写入数据库，也不会出现在日志里。</dd>
      </dl>
    </SettingsPanel>
  )
}

export function DataPanel() {
  const info = useSystemInfo()
  const summary = info ? `${formatNumber(info.paper_count)} 篇论文 · 占用 ${formatBytes(info.disk_usage_bytes)}` : '正在读取…'
  return (
    <SettingsPanel id="data" title="数据与存储" summary={summary}>
      <SettingsBlock id="files" title="数据位置与占用" description="全部数据都在本机，不会上传。">
        <FilesSection />
      </SettingsBlock>
      <SettingsBlock id="code" title="代码与复现" description="复现功能的安全边界。">
        <CodeExecutionSection />
      </SettingsBlock>
    </SettingsPanel>
  )
}

export function ShortcutsPanel() {
  return (
    <SettingsPanel id="shortcuts" title="快捷键" summary={`${READER_SHORTCUTS.length + 2} 个已生效的快捷键`}>
      <ShortcutsSection />
    </SettingsPanel>
  )
}

export function AboutPanel() {
  const info = useSystemInfo()
  return (
    <SettingsPanel id="about" title="关于" summary={`Marginalia · 版本 ${info?.version ?? '—'}`}>
      <GeneralSection />
    </SettingsPanel>
  )
}

