import { useEffect, useState } from 'react'
import { Copy } from 'lucide-react'
import { IconButton, Segmented, Select, Skeleton, Switch, Tag, toast } from '@/components/ui'
import { api, errorMessage } from '@/lib/api'
import { formatBytes } from '@/lib/format'
import {
  usePrefs,
  THEMES,
  SIZE_MAX,
  SIZE_MIN,
  type Measure,
  type ParaGap,
  type PdfDefaultZoom,
  type PdfTone,
  type ReadMode,
  type ThemeName
} from '@/stores/prefsStore'
import { FONT_PRESETS, matchPreset, presetLineHeight, presetPlan, type FontPresetId } from '@/lib/fontPlan'
import { SHORTCUTS as READER_SHORTCUTS } from '@/features/reader/ShortcutHelp'
import type { SystemInfo } from '@/lib/types'
import { SettingRow, SettingsBlock } from './SettingRow'

/** 关于：界面语言与版本。 */
function AutoSettings() {
  const prefs = usePrefs()
  return (
    <SettingRow title="自动" description="">
      <div className="pd-switchlist">
        <Switch checked={prefs.autoReparse} onChange={(v) => prefs.set({ autoReparse: v })} label="打开有新版解析的论文时自动重新解析" />
        <Switch checked={prefs.autoTranslatePaper} onChange={(v) => prefs.set({ autoTranslatePaper: v })} label="打开论文时自动翻译全文（调用模型，译文边翻边出现）" />
        <Switch checked={prefs.autoTranslateWorks} onChange={(v) => prefs.set({ autoTranslateWorks: v })} label="发现页的论文自动翻译标题和摘要（调用模型）" />
      </div>
    </SettingRow>
  )
}

export function GeneralSection() {
  const [info, setInfo] = useState<SystemInfo | null>(null)
  useEffect(() => {
    api.systemInfo().then(setInfo).catch(() => undefined)
  }, [])
  return (
    <div>
      <SettingRow title="界面语言" description="界面以中文为主，支持阅读英文及多语言论文；其他界面语言暂不提供。">
        <Select aria-label="界面语言" value="zh-CN" onChange={() => undefined} options={[{ value: 'zh-CN', label: '简体中文' }]} />
      </SettingRow>
      <AutoSettings />
      <SettingRow title="版本" description="Marginalia 的本地版本信息。">
        <dl className="pd-kv">
          <dt>应用</dt>
          <dd>Marginalia</dd>
          <dt>本地服务版本</dt>
          <dd>{info?.version ?? '—'}</dd>
        </dl>
      </SettingRow>
    </div>
  )
}

/** 阅读外观：主题、界面字号、正文版式、PDF 显示——都接在真实的偏好上，改动立即生效。 */
export function AppearanceSection() {
  const prefs = usePrefs()
  const sizes = Array.from({ length: SIZE_MAX - SIZE_MIN + 1 }, (_, k) => SIZE_MIN + k)
  return (
    <>
      <SettingsBlock id="theme" title="主题与界面" description="整个软件的配色和界面字号。阅读时也可以按 A 键随时调整。">
        <SettingRow title="主题" description="白色是默认；纸色偏暖、更护眼；深灰和夜间适合弱光环境。">
          <Segmented<ThemeName> aria-label="主题" value={prefs.theme} onChange={(v) => prefs.set({ theme: v })} options={THEMES.map((t) => ({ value: t.value, label: t.label }))} />
        </SettingRow>
        <SettingRow title="界面字号" description="只影响软件界面，不影响论文正文。">
          <Select
            aria-label="界面字号"
            value={String(prefs.fontScale)}
            onChange={(v) => prefs.set({ fontScale: Number(v) })}
            options={[
              { value: '0.9', label: '小' },
              { value: '1', label: '标准' },
              { value: '1.1', label: '大' },
              { value: '1.2', label: '特大' }
            ]}
          />
        </SettingRow>
      </SettingsBlock>

      <SettingsBlock id="reading" title="正文阅读" description="文字阅读器里正文的字体和版式。">
        <SettingRow title="字体方案" description="英文和中文字体可以按块分别设置，逐块微调请在阅读器顶栏的“Aa”阅读外观里。">
          <Select
            aria-label="字体方案"
            value={matchPreset(prefs.fontPlan)}
            onChange={(v) => {
              if (v === 'custom') return
              const id = v as FontPresetId
              const lh = presetLineHeight(id)
              prefs.set({ fontPlan: presetPlan(id, prefs.fontPlan), ...(lh ? { readLineHeight: lh } : {}) })
            }}
            options={[
              ...FONT_PRESETS.map((pr) => ({ value: pr.id, label: `${pr.label}：${pr.desc}` })),
              ...(matchPreset(prefs.fontPlan) === 'custom' ? [{ value: 'custom', label: '自定义' }] : [])
            ]}
          />
        </SettingRow>
        <SettingRow title="字号与行高" description={`正文字号 ${SIZE_MIN}–${SIZE_MAX} 像素；行高越大越松。`}>
          <div className="pd-fields pd-fields--2">
            <Select aria-label="正文字号" value={String(prefs.readSize)} onChange={(v) => prefs.set({ readSize: Number(v) })} options={sizes.map((n) => ({ value: String(n), label: `${n} px` }))} />
            <Select
              aria-label="行高"
              value={String(prefs.readLineHeight)}
              onChange={(v) => prefs.set({ readLineHeight: Number(v) })}
              options={['1.5', '1.65', '1.8', '2', '2.2'].map((n) => ({ value: n, label: `行高 ${n}` }))}
            />
          </div>
        </SettingRow>
        <SettingRow title="行宽与段距" description="窄一些更容易读；满宽适合宽屏对照。">
          <div className="pd-fields pd-fields--2">
            <Select
              aria-label="行宽"
              value={prefs.measure}
              onChange={(v) => prefs.set({ measure: v as Measure })}
              options={[
                { value: 'narrow', label: '行宽：窄' },
                { value: 'medium', label: '行宽：中' },
                { value: 'wide', label: '行宽：宽' },
                { value: 'full', label: '行宽：满' }
              ]}
            />
            <Select
              aria-label="段距"
              value={prefs.paraGap}
              onChange={(v) => prefs.set({ paraGap: v as ParaGap })}
              options={[
                { value: 'tight', label: '段距：紧' },
                { value: 'normal', label: '段距：标准' },
                { value: 'loose', label: '段距：松' }
              ]}
            />
          </div>
        </SettingRow>
        <SettingRow title="排版细节" description="两端对齐、首行缩进、加粗字重。">
          <div className="pd-fields">
            <Switch checked={prefs.justify} onChange={(v) => prefs.set({ justify: v })} label="两端对齐" />
            <Switch checked={prefs.indent} onChange={(v) => prefs.set({ indent: v })} label="首行缩进" />
            <Switch checked={prefs.boldText} onChange={(v) => prefs.set({ boldText: v })} label="正文略加粗" />
          </div>
        </SettingRow>
      </SettingsBlock>

      <SettingsBlock id="pdf" title="PDF 显示" description="打开 PDF 页面时的默认方式。">
        <SettingRow title="默认缩放" description="只作用于第一次打开的论文；打开过的论文会恢复你上次的阅读状态。">
          <Select
            aria-label="PDF 默认缩放"
            value={prefs.pdfDefaultZoom}
            onChange={(v) => prefs.set({ pdfDefaultZoom: v as PdfDefaultZoom })}
            options={[
              { value: 'fit-width', label: '适合宽度' },
              { value: 'fit-page', label: '适合页面' },
              { value: '100', label: '100%' },
              { value: '125', label: '125%' },
              { value: '150', label: '150%' }
            ]}
          />
        </SettingRow>
        <SettingRow title="默认阅读模式" description="连续滚动、单页或双页。">
          <Select
            aria-label="默认阅读模式"
            value={prefs.defaultReadMode}
            onChange={(v) => prefs.set({ defaultReadMode: v as ReadMode })}
            options={[
              { value: 'continuous', label: '连续滚动' },
              { value: 'single', label: '单页' },
              { value: 'double', label: '双页' }
            ]}
          />
        </SettingRow>
        <SettingRow title="暗色主题下的 PDF" description="深灰和夜间主题下，PDF 页面本身是白底，可以降低亮度或反色。">
          <Segmented<PdfTone>
            aria-label="暗色主题下的 PDF"
            value={prefs.pdfTone}
            onChange={(v) => prefs.set({ pdfTone: v })}
            options={[
              { value: 'normal', label: '原样' },
              { value: 'dim', label: '降低亮度' },
              { value: 'invert', label: '反色' }
            ]}
          />
        </SettingRow>
      </SettingsBlock>
    </>
  )
}

/** 数据与存储：路径、占用空间。 */
export function FilesSection() {
  const [info, setInfo] = useState<SystemInfo | null>(null)
  const [error, setError] = useState<string | null>(null)
  useEffect(() => {
    api.systemInfo().then(setInfo).catch((e) => setError(errorMessage(e)))
  }, [])

  const copy = async (text: string): Promise<void> => {
    try {
      await navigator.clipboard.writeText(text)
      toast.success('路径已复制')
    } catch {
      toast.error('复制失败')
    }
  }

  if (error) return <div className="pd-notice pd-notice--danger">加载失败：{error}</div>
  if (!info) return <Skeleton height={100} />

  const paths: Array<[string, string, string]> = [
    ['数据目录', info.data_dir, '导入的 PDF 副本、数据库、缓存和日志都保存在这里。'],
    ['数据库', info.db_path, '元数据、全文索引、笔记、研究卡片和任务记录。'],
    ['论文副本目录', info.papers_dir, '每篇论文一个独立目录。']
  ]
  return (
    <div>
      {paths.map(([title, value, desc]) => (
        <SettingRow key={title} title={title} description={desc}>
          <div className="pd-row" style={{ alignItems: 'flex-start' }}>
            <span style={{ flex: 1, minWidth: 0, wordBreak: 'break-all', fontSize: 'var(--fs-small)' }}>{value}</span>
            <IconButton icon={<Copy size={14} />} label="复制路径" size="sm" onClick={() => void copy(value)} />
          </div>
        </SettingRow>
      ))}
      <SettingRow title="占用情况" description="导入 PDF 时只复制一份到副本目录，原文件不会被移动或删除。数据目录位置目前不能在界面中修改，可用环境变量 PAPERDESK_DATA_DIR 指定后重启。">
        <dl className="pd-kv">
          <dt>论文数量</dt>
          <dd>{info.paper_count}</dd>
          <dt>占用空间</dt>
          <dd>{formatBytes(info.disk_usage_bytes)}</dd>
        </dl>
      </SettingRow>
    </div>
  )
}

/** 代码与复现：说明本机不执行代码，执行要在用户自己的隔离环境里进行。 */
export function CodeExecutionSection() {
  return (
    <div>
      <SettingRow title="代码执行" description="复现功能只做分析、计划和结果对比，不会在这台电脑上运行论文作者的代码。">
        <div className="pd-fields">
          <p style={{ fontSize: 'var(--fs-small)', lineHeight: 1.65 }}>
            下载、安装依赖、运行都需要你逐步批准，并且只会导出成说明和脚本，由你在自己的 WSL2、Docker 或远程机器上运行，再把结果导入对比。
          </p>
          <div>
            <Tag>本机不执行代码</Tag>
          </div>
        </div>
      </SettingRow>
    </div>
  )
}

/** 快捷键：阅读器里实际生效的那一份，加上全局的几个。 */
export function ShortcutsSection() {
  const global: Array<[string, string]> = [
    ['Enter / Shift + Enter', '提问框中发送 / 换行'],
    ['Ctrl + 滚轮', '缩放 PDF']
  ]
  const rows: Array<[string, string]> = [...READER_SHORTCUTS.map((s): [string, string] => [s.keys, s.what]), ...global]
  return (
    <div>
      <p className="pd-notice" style={{ marginBottom: 'var(--sp-md)' }}>
        快捷键自定义将在后续版本提供。下面是目前已经生效的快捷键；在输入框里打字时不会触发。
      </p>
      <table className="pd-table">
        <tbody>
          {rows.map(([k, d]) => (
            <tr key={k}>
              <td style={{ width: 220 }}>
                <Tag>{k}</Tag>
              </td>
              <td>{d}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}
