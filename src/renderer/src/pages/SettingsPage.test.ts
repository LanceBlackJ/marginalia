import { describe, expect, it } from 'vitest'
import { resolveSettingsTarget } from './SettingsPage'

describe('设置页（单页小面板）的路径映射', () => {
  it('没有指定、或不认识的分类时停在页面顶部', () => {
    expect(resolveSettingsTarget(undefined)).toBeUndefined()
    expect(resolveSettingsTarget('不存在的分类')).toBeUndefined()
  })

  it('面板的路径展开对应面板', () => {
    for (const key of ['models', 'cost', 'appearance', 'retrieval', 'usage', 'sendlog', 'data', 'shortcuts', 'about', 'danger']) {
      expect(resolveSettingsTarget(key)).toEqual({ panel: key })
    }
  })

  it('应用里已有的旧链接仍然能到达对应内容', () => {
    // 多处“去设置 API Key”的按钮链接到 /settings/ai，知识画像页链接到 /settings/profile
    expect(resolveSettingsTarget('ai')).toEqual({ panel: 'models' })
    expect(resolveSettingsTarget('profile')).toEqual({ panel: 'retrieval', anchor: 'profile' })
    expect(resolveSettingsTarget('academic')).toEqual({ panel: 'retrieval', anchor: 'academic' })
    expect(resolveSettingsTarget('api')).toEqual({ panel: 'usage', anchor: 'api' })
    expect(resolveSettingsTarget('privacy')).toEqual({ panel: 'sendlog' }) // 原来的“发送记录”
    expect(resolveSettingsTarget('files')).toEqual({ panel: 'data', anchor: 'files' })
    expect(resolveSettingsTarget('code')).toEqual({ panel: 'data', anchor: 'code' })
    expect(resolveSettingsTarget('general')).toEqual({ panel: 'about' })
  })
})
