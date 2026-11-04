import { useEffect, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { Button, Segmented, Skeleton, Switch, toast } from '@/components/ui'
import { errorMessage } from '@/lib/api'
import { profileApi } from '@/lib/api.discovery'
import type { Profile } from '@/lib/types.discovery'
import { DEPTH_OPTIONS } from '@/features/knowledge/kbUtils'
import '@/features/discovery/discovery.css'
import { SettingRow } from './SettingRow'

/** 知识画像：是否让 AI 解读参考你的画像（默认关闭，只附一句话），以及解释详略偏好。 */
export function ProfileSection() {
  const navigate = useNavigate()
  const [profile, setProfile] = useState<Profile | null>(null)

  const load = (): void => {
    profileApi
      .get()
      .then(setProfile)
      .catch((err) => toast.error('无法读取知识画像', errorMessage(err)))
  }
  useEffect(load, [])

  const save = async (patch: Parameters<typeof profileApi.putMeta>[0]): Promise<void> => {
    try {
      await profileApi.putMeta(patch)
      load()
    } catch (err) {
      toast.error('保存失败', errorMessage(err))
    }
  }

  if (!profile) return <Skeleton height={120} />
  const m = profile.meta
  return (
    <div>
      <SettingRow
        title="让 AI 解读参考我的知识画像"
        description="开启后，阅读时向模型发送的提示里会多一行很短的说明，只调整解释的方式和详略，不改变“事实必须引用原文”的要求。默认关闭；关闭时不会向模型发送任何画像信息。"
      >
        <div className="pd-fields">
          <Switch checked={m.use_in_prompts} onChange={(v) => void save({ use_in_prompts: v })} label={m.use_in_prompts ? '已开启' : '已关闭'} />
          <p className="pd-dc-sub" style={{ margin: 0 }}>
            {m.use_in_prompts
              ? profile.prompt_hint
                ? `每次会附带：“${profile.prompt_hint}”`
                : '画像里还没有设置兴趣、详略偏好或概念状态，所以不会附带任何内容。'
              : '关闭时的发送内容：无。'}
          </p>
        </div>
      </SettingRow>
      <SettingRow title="解释详略" description="影响 AI 解读的详细程度；未设置时由模型自行把握。">
        <Segmented
          variant="text"
          aria-label="解释详略"
          value={m.explanation_depth ?? ''}
          options={DEPTH_OPTIONS.map((o) => ({ value: o.value, label: o.label }))}
          onChange={(v) => void save({ explanation_depth: v === '' ? null : (v as 'brief' | 'standard' | 'deep') })}
        />
      </SettingRow>
      <SettingRow title="编辑画像" description="兴趣方向与概念掌握状态在“主题知识库 → 个人知识画像”中维护。AI 只能提出候选，由你确认。">
        <Button variant="secondary" onClick={() => navigate('/knowledge?tab=profile')}>
          打开个人知识画像
        </Button>
      </SettingRow>
    </div>
  )
}
