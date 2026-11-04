import { useEffect, useState } from 'react'
import { useParams } from 'react-router-dom'
import { AIModelSection } from '@/features/settings/AIModelSection'
import { DangerZone } from '@/features/settings/DangerZone'
import { AboutPanel, AppearancePanel, DataPanel, PrivacyNotePanel, RetrievalPanel, ShortcutsPanel, UsagePanel } from '@/features/settings/panels'
import { PrivacySection } from '@/features/settings/PrivacySection'
import '@/features/settings/settings.css'
import '@/features/settings/panels.css'

/** 路径里的分类 → 要展开的面板，以及面板里要滚动到的小块（没有就滚动到面板本身）。 */
const TARGETS: Record<string, { panel: string; anchor?: string }> = {
  // 现在的面板
  models: { panel: 'models' },
  cost: { panel: 'cost' },
  appearance: { panel: 'appearance' },
  retrieval: { panel: 'retrieval' },
  usage: { panel: 'usage' },
  sendlog: { panel: 'sendlog' },
  data: { panel: 'data' },
  shortcuts: { panel: 'shortcuts' },
  about: { panel: 'about' },
  danger: { panel: 'danger' },
  // 更早版本里的路径，应用里还有按钮链接过来
  general: { panel: 'about' },
  ai: { panel: 'models' },
  api: { panel: 'usage', anchor: 'api' },
  files: { panel: 'data', anchor: 'files' },
  code: { panel: 'data', anchor: 'code' },
  academic: { panel: 'retrieval', anchor: 'academic' },
  profile: { panel: 'retrieval', anchor: 'profile' },
  privacy: { panel: 'sendlog' }
}

export function resolveSettingsTarget(section: string | undefined): { panel: string; anchor?: string } | undefined {
  return section ? TARGETS[section] : undefined
}

export type SettingsTab = 'models' | 'general' | 'data'

export const SETTINGS_TABS: Array<{ value: SettingsTab; label: string }> = [
  { value: 'models', label: '模型' },
  { value: 'general', label: '通用' },
  { value: 'data', label: '数据与隐私' }
]

const TAB_OF_PANEL: Record<string, SettingsTab> = {
  models: 'models',
  cost: 'models',
  retrieval: 'models',
  usage: 'models',
  appearance: 'general',
  shortcuts: 'general',
  about: 'general',
  sendlog: 'data',
  privacynote: 'data',
  data: 'data',
  danger: 'data'
}

export function tabOfPanel(panel: string): SettingsTab {
  return TAB_OF_PANEL[panel] ?? 'models'
}

/**
 * 设置页分成“模型、通用、数据与隐私”三个标签，一次显示一个，标签里是可折叠的小面板（点击在原地展开，默认折叠）。
 * 没有保存 API Key 时“连接与模型”默认展开。旧链接 /settings/ai 等：切到对应标签，展开面板并滚动过去，标题闪一下。
 */
export function SettingsPage() {
  const { section } = useParams()
  const target = resolveSettingsTarget(section)
  const [tab, setTab] = useState<SettingsTab>(target ? tabOfPanel(target.panel) : 'models')

  useEffect(() => {
    if (!target) return
    setTab(tabOfPanel(target.panel))
    const t = window.setTimeout(() => {
      const el = document.getElementById(target.anchor ?? target.panel)
      if (!el) return
      el.scrollIntoView({ block: 'start', behavior: 'smooth' })
      const title = el.matches('.pd-panel') ? el.querySelector('.pd-panel-title') : el.querySelector('.pd-sblock-title')
      title?.setAttribute('data-flash', 'true')
      window.setTimeout(() => title?.removeAttribute('data-flash'), 1600)
    }, 200)
    return () => window.clearTimeout(t)
  }, [target?.panel, target?.anchor]) // eslint-disable-line react-hooks/exhaustive-deps

  const onKey = (e: React.KeyboardEvent, i: number): void => {
    if (e.key !== 'ArrowRight' && e.key !== 'ArrowLeft') return
    e.preventDefault()
    const n = (i + (e.key === 'ArrowRight' ? 1 : SETTINGS_TABS.length - 1)) % SETTINGS_TABS.length
    setTab(SETTINGS_TABS[n].value)
    document.getElementById(`settings-tab-${SETTINGS_TABS[n].value}`)?.focus()
  }

  return (
    <div className="pd-page">
      <div className="pd-page-inner pd-settings-page">
        <h1 className="pd-page-title">设置</h1>

        <div className="pd-settings-tabs" role="tablist" aria-label="设置分类">
          {SETTINGS_TABS.map((t, i) => (
            <button
              key={t.value}
              id={`settings-tab-${t.value}`}
              type="button"
              role="tab"
              aria-selected={tab === t.value}
              aria-controls="settings-tabpanel"
              tabIndex={tab === t.value ? 0 : -1}
              className="pd-settings-tab"
              onClick={() => setTab(t.value)}
              onKeyDown={(e) => onKey(e, i)}
            >
              {t.label}
            </button>
          ))}
        </div>

        <div id="settings-tabpanel" role="tabpanel" aria-labelledby={`settings-tab-${tab}`} className="pd-panels">
          {tab === 'models' && (
            <>
              <AIModelSection />
              <RetrievalPanel />
              <UsagePanel />
            </>
          )}
          {tab === 'general' && (
            <>
              <AppearancePanel />
              <ShortcutsPanel />
              <AboutPanel />
            </>
          )}
          {tab === 'data' && (
            <>
              <PrivacySection />
              <PrivacyNotePanel />
              <DataPanel />
              <DangerZone />
            </>
          )}
        </div>
      </div>
    </div>
  )
}
