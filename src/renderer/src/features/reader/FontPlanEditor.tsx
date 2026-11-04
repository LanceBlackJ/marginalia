import { useMemo, useState } from 'react'
import { Minus, Plus, RotateCcw } from 'lucide-react'
import { IconButton } from '@/components/ui'
import {
  CJK_FACES,
  DEFAULT_FONT_PLAN,
  FONT_BLOCKS,
  FONT_PRESETS,
  LATIN_FACES,
  SCALE_MAX,
  SCALE_MIN,
  buildStack,
  exportFontPlan,
  findInstalledName,
  importFontPlan,
  isFaceAvailable,
  makeCanvasMeasure,
  matchPreset,
  presetLineHeight,
  presetPlan,
  type BlockFont,
  type Face,
  type FontBlock,
  type FontPresetId,
  type MeasureFn
} from '@/lib/fontPlan'
import { usePrefs } from '@/stores/prefsStore'

const SAMPLE = 'Abstract. Finding key players… 摘要：关键节点 σ(G)'

function FaceSelect({
  label,
  faces,
  value,
  onChange,
  measure
}: {
  label: string
  faces: Face[]
  value: string
  onChange: (v: string) => void
  measure: MeasureFn | null
}) {
  return (
    <select className="pd-ap-select" aria-label={label} value={value} onChange={(e) => onChange(e.target.value)}>
      {faces.map((f) => {
        const ok = isFaceAvailable(f, measure)
        return (
          <option key={f.key} value={f.key} disabled={!ok && f.key !== value}>
            {f.label}
            {ok ? '' : ' · 本机未安装'}
          </option>
        )
      })}
      <option value="custom">自定义…</option>
    </select>
  )
}

function CustomInput({ value, onChange, measure, label }: { value: string; onChange: (v: string) => void; measure: MeasureFn | null; label: string }) {
  const r = findInstalledName(value, measure)
  return (
    <div className="pd-ap-custom">
      <input className="pd-ap-input" aria-label={label} placeholder="字体名，逗号分隔多个备选" value={value} onChange={(e) => onChange(e.target.value)} />
      <div className="pd-ap-hint" role="status">
        {r.names.length === 0 ? '没填字体名，将使用默认字体。' : r.found ? `已找到：${r.found}` : '未找到，将使用备选字体。'}
      </div>
    </div>
  )
}

function BlockEditor({
  block,
  label,
  hint,
  value,
  measure,
  onChange
}: {
  block: FontBlock
  label: string
  hint: string
  value: BlockFont
  measure: MeasureFn | null
  onChange: (v: BlockFont) => void
}) {
  const set = (patch: Partial<BlockFont>): void => onChange({ ...value, ...patch })
  const step = (d: number): void => set({ scale: Math.round(Math.min(SCALE_MAX, Math.max(SCALE_MIN, value.scale + d)) * 100) / 100 })
  const isDefault = JSON.stringify(value) === JSON.stringify(DEFAULT_FONT_PLAN[block])
  return (
    <div className="pd-ap-block" data-block={block}>
      <div className="pd-ap-block-head">
        <strong title={hint}>{label}</strong>
        <label className="pd-ap-bold">
          <input type="checkbox" checked={value.bold} onChange={(e) => set({ bold: e.target.checked })} />
          加粗
        </label>
        <span className="pd-spacer" />
        <div className="pd-ap-stepper" role="group" aria-label={`${label}字号倍率`}>
          <IconButton icon={<Minus size={12} />} label="缩小" size="sm" disabled={value.scale <= SCALE_MIN} onClick={() => step(-0.1)} />
          <span className="pd-ap-value">{value.scale.toFixed(1)}×</span>
          <IconButton icon={<Plus size={12} />} label="放大" size="sm" disabled={value.scale >= SCALE_MAX} onClick={() => step(0.1)} />
        </div>
        <IconButton icon={<RotateCcw size={12} />} label={`恢复${label}的默认`} size="sm" disabled={isDefault} onClick={() => onChange({ ...DEFAULT_FONT_PLAN[block] })} />
      </div>
      <div className="pd-ap-pair">
        <FaceSelect label={`${label}的英文字体`} faces={LATIN_FACES} value={value.latin} onChange={(v) => set({ latin: v })} measure={measure} />
        <FaceSelect label={`${label}的中文字体`} faces={CJK_FACES} value={value.cjk} onChange={(v) => set({ cjk: v })} measure={measure} />
      </div>
      {value.latin === 'custom' && <CustomInput label={`${label}的自定义英文字体`} value={value.latinCustom} onChange={(v) => set({ latinCustom: v })} measure={measure} />}
      {value.cjk === 'custom' && <CustomInput label={`${label}的自定义中文字体`} value={value.cjkCustom} onChange={(v) => set({ cjkCustom: v })} measure={measure} />}
      <div className="pd-ap-preview" style={{ fontFamily: buildStack(value), fontWeight: value.bold ? 700 : 400, fontSize: `${(14 * value.scale).toFixed(1)}px` }}>
        {SAMPLE}
      </div>
    </div>
  )
}

/** “阅读外观”里的字体部分：预设、逐块字体、自定义字体、导出/导入方案。 */
export function FontPlanEditor() {
  const fontPlan = usePrefs((s) => s.fontPlan)
  const setPrefs = usePrefs((s) => s.set)
  const measure = useMemo(() => makeCanvasMeasure(), [])
  const [open, setOpen] = useState(false)
  const [io, setIo] = useState(false)
  const [importText, setImportText] = useState('')
  const [ioMsg, setIoMsg] = useState('')
  const preset = matchPreset(fontPlan)

  const choosePreset = (id: FontPresetId): void => {
    const lh = presetLineHeight(id)
    setPrefs({ fontPlan: presetPlan(id, fontPlan), ...(lh ? { readLineHeight: lh } : {}) })
  }

  return (
    <div className="pd-ap-fonts">
      <div className="pd-ap-row">
        <div className="pd-ap-label">字体方案</div>
        <div className="pd-ap-ctl">
          <select
            className="pd-ap-select"
            aria-label="字体方案"
            value={preset}
            onChange={(e) => {
              if (e.target.value !== 'custom') choosePreset(e.target.value as FontPresetId)
            }}
          >
            {FONT_PRESETS.map((p) => (
              <option key={p.id} value={p.id}>
                {p.label}：{p.desc}
              </option>
            ))}
            {preset === 'custom' && <option value="custom">自定义</option>}
          </select>
        </div>
      </div>

      <button type="button" className="pd-ap-toggle" aria-expanded={open} onClick={() => setOpen((v) => !v)}>
        {open ? '收起分块字体' : '分块设置字体'}
      </button>
      {open && (
        <div className="pd-ap-blocks">
          {FONT_BLOCKS.map((b) => (
            <BlockEditor
              key={b.key}
              block={b.key}
              label={b.label}
              hint={b.hint}
              value={fontPlan[b.key]}
              measure={measure}
              onChange={(v) => setPrefs({ fontPlan: { ...fontPlan, [b.key]: v } })}
            />
          ))}
          <div className="pd-ap-hint">英文字体放在前面，它没有的汉字自动用后面的中文字体。灰显的字体表示本机没装；“自定义”可以填任意本机已装的字体名。</div>
        </div>
      )}

      <button type="button" className="pd-ap-toggle" aria-expanded={io} onClick={() => setIo((v) => !v)}>
        {io ? '收起导出 / 导入' : '导出 / 导入字体方案'}
      </button>
      {io && (
        <div className="pd-ap-io">
          <textarea className="pd-ap-text" aria-label="当前字体方案" readOnly rows={5} value={exportFontPlan(fontPlan)} onFocus={(e) => e.currentTarget.select()} />
          <textarea className="pd-ap-text" aria-label="粘贴要导入的字体方案" rows={4} placeholder="粘贴导出的字体方案" value={importText} onChange={(e) => setImportText(e.target.value)} />
          <div className="pd-ap-io-actions">
            <button
              type="button"
              className="pd-ap-linkbtn"
              disabled={!importText.trim()}
              onClick={() => {
                const r = importFontPlan(importText)
                if (r.ok) {
                  setPrefs({ fontPlan: r.plan })
                  setIoMsg('已导入并应用。')
                  setImportText('')
                } else setIoMsg(r.error)
              }}
            >
              导入并应用
            </button>
            <span className="pd-ap-hint" role="status">
              {ioMsg}
            </span>
          </div>
        </div>
      )}
    </div>
  )
}
