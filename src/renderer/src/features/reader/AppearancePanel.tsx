import { useEffect, useRef } from 'react'
import { Minus, Plus, RotateCcw } from 'lucide-react'
import { Button, IconButton, Segmented, Switch } from '@/components/ui'
import { DEFAULT_FONT_PLAN } from '@/lib/fontPlan'
import { FontPlanEditor } from './FontPlanEditor'
import {
  DEFAULTS,
  SIZE_MAX,
  SIZE_MIN,
  THEMES,
  usePrefs,
  type Measure,
  type ParaGap,
  type PdfTone,
  type ThemeName
} from '@/stores/prefsStore'

const LH_OPTIONS: Array<{ value: string; label: string }> = [
  { value: '1.6', label: '紧凑' },
  { value: '1.8', label: '标准' },
  { value: '2', label: '宽松' }
]

function Row({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div className="pd-ap-row">
      <div className="pd-ap-label">{label}</div>
      <div className="pd-ap-ctl">{children}</div>
    </div>
  )
}

/**
 * “阅读外观”面板：主题、字体、字号、行高、行宽、段间距、两端对齐、首行缩进。
 * 偏好写进 usePrefs（本机存储），立即作用于整站；点击面板外或按 Esc 关闭。
 */
export function AppearancePanel({ open, onClose }: { open: boolean; onClose: () => void }) {
  const p = usePrefs()
  const ref = useRef<HTMLDivElement>(null)

  useEffect(() => {
    if (!open) return
    const onDown = (e: MouseEvent): void => {
      const el = ref.current
      if (el && !el.contains(e.target as Node) && !(e.target as HTMLElement).closest('[data-appearance-trigger]')) onClose()
    }
    const onKey = (e: KeyboardEvent): void => {
      if (e.key === 'Escape') onClose()
    }
    document.addEventListener('mousedown', onDown)
    document.addEventListener('keydown', onKey)
    return () => {
      document.removeEventListener('mousedown', onDown)
      document.removeEventListener('keydown', onKey)
    }
  }, [open, onClose])

  if (!open) return null
  const dark = p.theme === 'gray' || p.theme === 'dark'
  return (
    <div className="pd-appearance" ref={ref} role="dialog" aria-label="阅读外观">
      <div className="pd-ap-head">
        <strong>阅读外观</strong>
        <Button
          size="sm"
          variant="ghost"
          icon={<RotateCcw size={13} />}
          onClick={() =>
            p.set({
              theme: DEFAULTS.theme,
              fontPlan: DEFAULT_FONT_PLAN,
              readSize: DEFAULTS.readSize,
              readLineHeight: DEFAULTS.readLineHeight,
              measure: DEFAULTS.measure,
              paraGap: DEFAULTS.paraGap,
              justify: DEFAULTS.justify,
              indent: DEFAULTS.indent,
              boldText: DEFAULTS.boldText,
              pdfTone: DEFAULTS.pdfTone
            })
          }
        >
          恢复默认
        </Button>
      </div>

      <Row label="主题">
        <div className="pd-ap-themes" role="group" aria-label="主题">
          {THEMES.map((t) => (
            <button
              key={t.value}
              type="button"
              className="pd-ap-theme"
              data-theme={t.value}
              aria-pressed={p.theme === t.value}
              onClick={() => p.set({ theme: t.value as ThemeName })}
            >
              <span className="pd-ap-theme-aa">Aa</span>
              <span className="pd-ap-theme-name">{t.label}</span>
            </button>
          ))}
        </div>
      </Row>

      <FontPlanEditor />

      <Row label="字号">
        <div className="pd-ap-stepper">
          <IconButton icon={<Minus size={14} />} label="缩小字号" size="sm" disabled={p.readSize <= SIZE_MIN} onClick={() => p.set({ readSize: p.readSize - 1 })} />
          <span className="pd-ap-value">{p.readSize}px</span>
          <IconButton icon={<Plus size={14} />} label="放大字号" size="sm" disabled={p.readSize >= SIZE_MAX} onClick={() => p.set({ readSize: p.readSize + 1 })} />
        </div>
      </Row>

      <Row label="行高">
        <Segmented<string>
          aria-label="行高"
          value={String(p.readLineHeight)}
          onChange={(v) => p.set({ readLineHeight: Number(v) })}
          options={LH_OPTIONS}
        />
      </Row>

      <Row label="行宽">
        <Segmented<Measure>
          aria-label="行宽"
          value={p.measure}
          onChange={(v) => p.set({ measure: v })}
          options={[
            { value: 'narrow', label: '窄' },
            { value: 'medium', label: '中' },
            { value: 'wide', label: '宽' },
            { value: 'full', label: '满' }
          ]}
        />
      </Row>

      <Row label="段间距">
        <Segmented<ParaGap>
          aria-label="段间距"
          value={p.paraGap}
          onChange={(v) => p.set({ paraGap: v })}
          options={[
            { value: 'tight', label: '紧' },
            { value: 'normal', label: '中' },
            { value: 'loose', label: '松' }
          ]}
        />
      </Row>

      <div className="pd-ap-switches">
        <Switch checked={p.justify} onChange={(v) => p.set({ justify: v })} label="两端对齐" />
        <Switch checked={p.indent} onChange={(v) => p.set({ indent: v })} label="段落首行缩进" />
        <Switch checked={p.boldText} onChange={(v) => p.set({ boldText: v })} label="加粗字重" />
      </div>

      <Row label="PDF 页面">
        <Segmented<PdfTone>
          aria-label="暗色主题下的 PDF 页面"
          value={p.pdfTone}
          onChange={(v) => p.set({ pdfTone: v })}
          options={[
            { value: 'normal', label: '原样' },
            { value: 'dim', label: '降亮度' },
            { value: 'invert', label: '反色' }
          ]}
        />
      </Row>
      <div className="pd-ap-switches">
        <Switch checked={p.hoverTranslate} onChange={(v) => p.set({ hoverTranslate: v })} label="PDF 上悬停段落显示译文" />
        <Switch checked={p.smartLayer} onChange={(v) => p.set({ smartLayer: v })} label="PDF 上显示公式/图/表角标与页边标记" />
        <Switch checked={p.cropMargins} onChange={(v) => p.set({ cropMargins: v })} label="PDF 裁掉左右白边" />
      </div>
      {!dark && <div className="pd-ap-hint">PDF 页面的处理只在深灰、夜间主题下生效。</div>}
    </div>
  )
}
