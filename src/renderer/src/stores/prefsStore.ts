import { create } from 'zustand'
import { loadJSON, saveJSON } from '@/lib/storage'
import { DEFAULT_FONT_PLAN, fontCssVars, legacyFontPlan, sanitizeFontPlan, type FontPlan } from '@/lib/fontPlan'

export type PdfDefaultZoom = 'fit-width' | 'fit-page' | '100' | '125' | '150'
export type ReadMode = 'continuous' | 'single' | 'double'
/** 主题：白、纸色（护眼暖白）、深灰、夜间。颜色全部来自 styles/tokens.css 的主题变量。 */
export type ThemeName = 'light' | 'paper' | 'gray' | 'dark'
/** 旧版本的单一字体选项；只用于从旧偏好迁移（见 lib/fontPlan.ts 的 legacyFontPlan）。 */
export type ReadFont = 'sans' | 'serif' | 'system'
export type Measure = 'narrow' | 'medium' | 'wide' | 'full'
export type ParaGap = 'tight' | 'normal' | 'loose'
/** 暗色主题下 PDF 页面的处理：原样 / 降低亮度 / 反色。 */
export type PdfTone = 'normal' | 'dim' | 'invert'

export interface Prefs {
  navCollapsed: boolean
  pdfDefaultZoom: PdfDefaultZoom
  defaultReadMode: ReadMode
  fontScale: number
  /** 文献库的显示方式：书架（默认）、列表、网格 */
  libraryLayout: 'shelf' | 'list' | 'grid'
  librarySort: 'recent' | 'title' | 'author' | 'year' | 'imported'
  // ---- 阅读外观（“阅读外观”面板） ----
  theme: ThemeName
  /** 分块字体方案：标题、正文、摘要、图注、译文、参考文献、论文信息、等宽各有英文+中文字体。 */
  fontPlan: FontPlan
  readSize: number // px
  readLineHeight: number
  measure: Measure
  paraGap: ParaGap
  justify: boolean
  justifyMigrated: boolean // 正文默认两端对齐：旧偏好里存的 false 只是当时的默认值，迁移一次改成开启，之后用户自己关掉的会被尊重
  indent: boolean
  boldText: boolean
  pdfTone: PdfTone
  // ---- 并排视图 ----
  splitRatio: number // 左栏占比 0.3–0.7
  splitSwap: boolean // 交换左右
  syncScroll: boolean // 两栏同步滚动
  hoverTranslate: boolean // PDF 上悬停段落显示译文气泡
  smartLayer: boolean // PDF 上的公式/图/表角标与页边标记
  cropMargins: boolean // PDF 裁掉左右白边（按正文宽度适应）
  // ---- 自动行为 ----
  autoReparse: boolean // 打开有新版解析的论文时自动重新解析（有备份，可撤销）
  autoTranslateWorks: boolean // 发现页里找到的论文自动翻译标题和摘要（会调用模型）
  askWeb: boolean // 向 AI 提问时，论文里没有的内容允许联网查资料（维基百科、OpenAlex）
  /** 阅读区右侧面板（AI 对话、术语、笔记……）里内容的缩放，1 = 原样；在面板上按 Ctrl+鼠标滚轮调整 */
  panelScale: number
}

const KEY = 'pd.prefs'

export const DEFAULTS: Prefs = {
  navCollapsed: false,
  pdfDefaultZoom: 'fit-width',
  defaultReadMode: 'continuous',
  fontScale: 1,
  libraryLayout: 'shelf',
  librarySort: 'imported',
  theme: 'light',
  fontPlan: DEFAULT_FONT_PLAN,
  readSize: 16,
  readLineHeight: 1.8,
  measure: 'medium',
  paraGap: 'normal',
  justify: true,
  justifyMigrated: true,
  indent: false,
  boldText: false,
  pdfTone: 'dim',
  splitRatio: 0.5,
  splitSwap: false,
  syncScroll: true,
  hoverTranslate: true,
  smartLayer: true,
  cropMargins: false,
  autoReparse: true,
  autoTranslateWorks: true,
  askWeb: true,
  panelScale: 1
}

export const THEMES: Array<{ value: ThemeName; label: string }> = [
  { value: 'light', label: '白' },
  { value: 'paper', label: '纸色' },
  { value: 'gray', label: '深灰' },
  { value: 'dark', label: '夜间' }
]
export const MEASURES: Record<Measure, string> = { narrow: '680px', medium: '820px', wide: '980px', full: '100%' }
export const PARA_GAPS: Record<ParaGap, string> = { tight: '0.55em', normal: '0.9em', loose: '1.3em' }
export const SIZE_MIN = 13
export const SIZE_MAX = 26
export const PANEL_SCALE_MIN = 0.8
export const PANEL_SCALE_MAX = 1.6

const clamp = (n: number, lo: number, hi: number): number => Math.min(hi, Math.max(lo, n))

/** 校正从本地存储里读出的偏好：缺的补默认值，越界的夹回范围，未知枚举回到默认。 */
export function sanitizePrefs(raw: (Partial<Prefs> & { readFont?: unknown }) | null | undefined): Prefs {
  const { readFont: _legacyReadFont, ...v } = { ...DEFAULTS, ...(raw ?? {}) } as Prefs & { readFont?: unknown }
  void _legacyReadFont // 旧字段不再保留，迁移结果写进 fontPlan
  // 旧版本只存 readFont：没有 fontPlan 时按旧选项迁移；已经有 fontPlan 就以它为准
  const legacy = raw && !('fontPlan' in raw) ? legacyFontPlan(raw.readFont) : null
  const pick = <T extends string>(x: unknown, allowed: readonly T[], d: T): T => (allowed.includes(x as T) ? (x as T) : d)
  return {
    ...v,
    theme: pick(v.theme, ['light', 'paper', 'gray', 'dark'] as const, DEFAULTS.theme),
    fontPlan: legacy ? legacy.plan : sanitizeFontPlan(v.fontPlan),
    measure: pick(v.measure, ['narrow', 'medium', 'wide', 'full'] as const, DEFAULTS.measure),
    paraGap: pick(v.paraGap, ['tight', 'normal', 'loose'] as const, DEFAULTS.paraGap),
    pdfTone: pick(v.pdfTone, ['normal', 'dim', 'invert'] as const, DEFAULTS.pdfTone),
    readSize: clamp(Number(v.readSize) || DEFAULTS.readSize, SIZE_MIN, SIZE_MAX),
    readLineHeight: clamp(Number(v.readLineHeight) || DEFAULTS.readLineHeight, 1.4, 2.4),
    justify: raw && raw.justifyMigrated !== true ? true : v.justify !== false,
    justifyMigrated: true,
    indent: v.indent === true,
    boldText: v.boldText === true,
    splitRatio: clamp(Number(v.splitRatio) || DEFAULTS.splitRatio, 0.3, 0.7),
    splitSwap: v.splitSwap === true,
    syncScroll: v.syncScroll !== false,
    hoverTranslate: v.hoverTranslate !== false,
    autoReparse: v.autoReparse !== false,
    autoTranslateWorks: v.autoTranslateWorks !== false,
    askWeb: v.askWeb !== false,
    panelScale: clamp(Number(v.panelScale) || DEFAULTS.panelScale, PANEL_SCALE_MIN, PANEL_SCALE_MAX),
    smartLayer: v.smartLayer !== false,
    cropMargins: v.cropMargins === true
  }
}

interface PrefsState extends Prefs {
  set: (patch: Partial<Prefs>) => void
}

function applyFontScale(scale: number): void {
  if (typeof document === 'undefined') return // 单元测试（node 环境）里没有 document
  document.documentElement.style.setProperty('--font-scale', String(scale))
}

/** 把外观偏好写到 <html> 上：主题属性 + 阅读用的 CSS 变量。样式只读这些变量。 */
export function applyAppearance(p: Prefs): void {
  if (typeof document === 'undefined') return
  const root = document.documentElement
  root.dataset.theme = p.theme
  root.dataset.pdf = p.pdfTone
  root.dataset.justify = String(p.justify)
  root.dataset.indent = String(p.indent)
  const s = root.style
  for (const [name, value] of Object.entries(fontCssVars(p.fontPlan))) s.setProperty(name, value)
  s.setProperty('--rd-size', `${p.readSize}px`)
  s.setProperty('--rd-lh', String(p.readLineHeight))
  s.setProperty('--rd-measure', MEASURES[p.measure])
  s.setProperty('--rd-gap', PARA_GAPS[p.paraGap])
  s.setProperty('--rd-weight', p.boldText ? '500' : '400')
}

export const usePrefs = create<PrefsState>((set, get) => {
  const initial = sanitizePrefs(loadJSON<Partial<Prefs>>(KEY, DEFAULTS))
  applyFontScale(initial.fontScale)
  applyAppearance(initial)
  return {
    ...initial,
    set: (patch) => {
      set(patch)
      const { set: _s, ...prefs } = get()
      void _s
      saveJSON(KEY, prefs)
      if (patch.fontScale !== undefined) applyFontScale(patch.fontScale)
      applyAppearance(prefs)
    }
  }
})
