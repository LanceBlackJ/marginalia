import { useCallback, useEffect, useState } from 'react'
import { Button, toast } from '@/components/ui'
import { api, errorMessage } from '@/lib/api'
import { clearApiKey } from '@/lib/native'
import { AI_SETTINGS_CHANGED } from './events'
import { SettingRow, SettingsBlock, SettingsPanel } from './SettingRow'

/**
 * 危险操作：不能撤销的动作放在设置页最后一个面板，默认折叠，头部用淡色小字提醒。
 * 目前只有“清除 API Key”；没有保存过密钥时没有可做的危险操作，整个面板不显示。
 */
export function DangerZone() {
  const [hasKey, setHasKey] = useState(false)
  const [busy, setBusy] = useState(false)

  const load = useCallback(async () => {
    try {
      setHasKey((await api.settings.getAI()).has_api_key)
    } catch {
      setHasKey(false)
    }
  }, [])

  useEffect(() => {
    void load()
    const onChanged = (): void => void load()
    window.addEventListener(AI_SETTINGS_CHANGED, onChanged)
    return () => window.removeEventListener(AI_SETTINGS_CHANGED, onChanged)
  }, [load])

  const removeKey = async (): Promise<void> => {
    setBusy(true)
    try {
      await clearApiKey()
      toast.success('已清除 API Key')
      window.dispatchEvent(new Event(AI_SETTINGS_CHANGED))
      await load()
    } catch (err) {
      toast.error('清除失败', errorMessage(err))
    } finally {
      setBusy(false)
    }
  }

  if (!hasKey) return null
  return (
    <SettingsPanel id="danger" title="危险操作" summary="清除 API Key，不能撤销" warn>
      <SettingsBlock title="API Key" description="从系统安全存储和本地服务中删除密钥。">
        <SettingRow title="清除 API Key" description="清除后 AI 功能不可用，直到重新填写。">
          <Button variant="danger" disabled={busy} onClick={() => void removeKey()}>
            清除 API Key
          </Button>
        </SettingRow>
      </SettingsBlock>
    </SettingsPanel>
  )
}
