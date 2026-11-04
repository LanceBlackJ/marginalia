/**
 * 阅读字体方案：每个内容块（标题、正文、摘要、图注、译文、参考文献、论文信息、等宽）各有“英文字体 + 中文字体”，
 * 拼成 CSS font-family 栈：英文字体放前面，它没有的汉字自动落到后面的中文字体。
 * 这里只有纯函数（栈拼装、预设、校正、旧偏好迁移、字体检测、导入导出），不依赖 DOM，便于测试。
 */

export type FontBlock = 'body' | 'title' | 'abstract' | 'caption' | 'trans' | 'ref' | 'front' | 'mono'

export interface BlockFont {
  /** 英文字体：LATIN_FACES 的 key，或 'custom' */
  latin: string
  /** 中文字体：CJK_FACES 的 key，或 'custom' */
  cjk: string
  latinCustom: string
  cjkCustom: string
  /** 字号倍率（相对这个块原来的大小） */
  scale: number
  bold: boolean
}

export type FontPlan = Record<FontBlock, BlockFont>
export type FontPresetId = 'classic' | 'comfort' | 'sans' | 'uniform'

export interface Face {
  key: string
  label: string
  /** 栈里的字体名，按优先级 */
  names: string[]
  /** 用来检测本机是否装了这个字体：任意一个检测到即可；空数组表示总是可用 */
  probe: string[]
  generic: 'serif' | 'sans-serif' | 'monospace'
}

export const LATIN_FACES: Face[] = [
  { key: 'tnr', label: 'Times New Roman', names: ['Times New Roman', 'Times', 'Liberation Serif'], probe: ['Times New Roman'], generic: 'serif' },
  { key: 'georgia', label: 'Georgia', names: ['Georgia'], probe: ['Georgia'], generic: 'serif' },
  { key: 'cambria', label: 'Cambria', names: ['Cambria'], probe: ['Cambria'], generic: 'serif' },
  { key: 'palatino', label: 'Palatino Linotype', names: ['Palatino Linotype', 'Palatino', 'Book Antiqua'], probe: ['Palatino Linotype', 'Book Antiqua'], generic: 'serif' },
  { key: 'arial', label: 'Arial', names: ['Arial', 'Helvetica'], probe: ['Arial'], generic: 'sans-serif' },
  { key: 'segoe', label: 'Segoe UI / Helvetica', names: ['Segoe UI', 'Helvetica Neue', 'Helvetica'], probe: ['Segoe UI', 'Helvetica Neue'], generic: 'sans-serif' },
  { key: 'inter', label: 'Inter · 应用自带', names: ['Inter'], probe: [], generic: 'sans-serif' },
  { key: 'mono', label: 'Cascadia Mono / Consolas', names: ['Cascadia Mono', 'Consolas', 'Courier New'], probe: ['Cascadia Mono', 'Consolas'], generic: 'monospace' }
]

export const CJK_FACES: Face[] = [
  // “宋体”里把思源宋体放前面：本机装了思源宋体时优先用它，没装就是宋体
  { key: 'songti', label: '宋体 · 装了思源宋体则优先', names: ['Noto Serif SC', 'Source Han Serif SC', 'Songti SC', 'SimSun', 'NSimSun'], probe: ['SimSun', 'Songti SC', 'Noto Serif SC', 'Source Han Serif SC'], generic: 'serif' },
  { key: 'noto-serif', label: '思源宋体', names: ['Noto Serif SC', 'Source Han Serif SC', 'Source Han Serif CN', 'Songti SC', 'SimSun'], probe: ['Noto Serif SC', 'Source Han Serif SC', 'Source Han Serif CN'], generic: 'serif' },
  { key: 'heiti', label: '黑体', names: ['SimHei', 'Heiti SC', 'Microsoft YaHei UI'], probe: ['SimHei', 'Heiti SC'], generic: 'sans-serif' },
  { key: 'yahei', label: '微软雅黑', names: ['Microsoft YaHei UI', 'Microsoft YaHei', 'PingFang SC'], probe: ['Microsoft YaHei UI', 'Microsoft YaHei', 'PingFang SC'], generic: 'sans-serif' },
  { key: 'dengxian', label: '等线', names: ['DengXian', 'Microsoft YaHei UI'], probe: ['DengXian'], generic: 'sans-serif' },
  { key: 'kaiti', label: '楷体', names: ['KaiTi', 'Kaiti SC', 'STKaiti', 'SimSun'], probe: ['KaiTi', 'Kaiti SC', 'STKaiti'], generic: 'serif' },
  { key: 'fangsong', label: '仿宋', names: ['FangSong', 'STFangsong', 'SimSun'], probe: ['FangSong', 'STFangsong'], generic: 'serif' },
  { key: 'noto-sans', label: '思源黑体', names: ['Noto Sans SC', 'Source Han Sans SC', 'Source Han Sans CN', 'Microsoft YaHei UI'], probe: ['Noto Sans SC', 'Source Han Sans SC', 'Source Han Sans CN'], generic: 'sans-serif' }
]

export const FONT_BLOCKS: Array<{ key: FontBlock; label: string; hint: string }> = [
  { key: 'title', label: '章节标题', hint: '正文里的各级标题' },
  { key: 'body', label: '正文', hint: '普通段落' },
  { key: 'abstract', label: '摘要', hint: '论文开头的摘要块' },
  { key: 'caption', label: '图注与表注', hint: '图、表下面的说明' },
  { key: 'trans', label: '译文', hint: '对照、译文模式里的中文译文' },
  { key: 'ref', label: '参考文献', hint: '文末参考文献列表' },
  { key: 'front', label: '论文信息', hint: '开头的标题、作者、单位' },
  { key: 'mono', label: '等宽内容', hint: '表格文字、代码、未识别的公式' }
]

export const SCALE_MIN = 0.8
export const SCALE_MAX = 1.6

const GENERICS = new Set(['serif', 'sans-serif', 'monospace', 'system-ui', 'cursive', 'fantasy'])
const quote = (n: string): string => (GENERICS.has(n) ? n : `'${n.replace(/['"\\]/g, '')}'`)

/** 把用户输入的字体名（逗号分隔）拆成干净的名字列表：去引号和危险字符，最多 6 个。 */
export function parseCustomNames(input: string): string[] {
  return input
    .split(/[,，、]/)
    .map((s) => s.replace(/^[\s'"]+|[\s'"]+$/g, '').replace(/[;{}()<>\\]/g, '').trim())
    .filter((s) => s.length > 0 && s.length <= 60)
    .slice(0, 6)
}

function namesOf(faces: Face[], key: string, custom: string, fallbackKey: string): { names: string[]; generic?: Face['generic'] } {
  if (key === 'custom') {
    const names = parseCustomNames(custom)
    if (names.length > 0) return { names }
  }
  const face = faces.find((f) => f.key === key) ?? faces.find((f) => f.key === fallbackKey)!
  return { names: face.names, generic: face.generic }
}

/** 拼出这个块的 CSS font-family：英文字体在前，中文字体在后，最后是通用族。 */
export function buildStack(b: BlockFont): string {
  const latin = namesOf(LATIN_FACES, b.latin, b.latinCustom, 'tnr')
  const cjk = namesOf(CJK_FACES, b.cjk, b.cjkCustom, 'songti')
  const generic = latin.generic ?? cjk.generic ?? 'serif'
  const seen = new Set<string>()
  const all: string[] = []
  for (const n of [...latin.names, ...cjk.names]) {
    if (!seen.has(n)) {
      seen.add(n)
      all.push(n)
    }
  }
  return [...all, generic].map(quote).join(', ')
}

const blk = (latin: string, cjk: string, bold = false, scale = 1): BlockFont => ({ latin, cjk, latinCustom: '', cjkCustom: '', scale, bold })

export const FONT_PRESETS: Array<{ id: FontPresetId; label: string; desc: string; lineHeight?: number }> = [
  { id: 'classic', label: '论文经典', desc: '英文 Times New Roman，中文宋体，标题黑体' },
  { id: 'comfort', label: '屏幕舒适', desc: 'Georgia + 思源宋体 / 雅黑，行距更大', lineHeight: 1.95 },
  { id: 'sans', label: '无衬线', desc: 'Inter + 微软雅黑' },
  { id: 'uniform', label: '全部统一', desc: '所有块都用正文的字体' }
]

const CLASSIC: FontPlan = {
  title: blk('tnr', 'heiti', true),
  body: blk('tnr', 'songti'),
  abstract: blk('tnr', 'songti'),
  caption: blk('tnr', 'kaiti'),
  trans: blk('tnr', 'songti'),
  ref: blk('tnr', 'songti'),
  front: blk('tnr', 'heiti', true),
  mono: blk('mono', 'yahei')
}
const COMFORT: FontPlan = {
  title: blk('georgia', 'yahei', true),
  body: blk('georgia', 'noto-serif'),
  abstract: blk('georgia', 'noto-serif'),
  caption: blk('georgia', 'yahei'),
  trans: blk('georgia', 'noto-serif'),
  ref: blk('georgia', 'noto-serif'),
  front: blk('georgia', 'yahei', true),
  mono: blk('mono', 'yahei')
}
const SANS: FontPlan = {
  title: blk('inter', 'yahei', true),
  body: blk('inter', 'yahei'),
  abstract: blk('inter', 'yahei'),
  caption: blk('inter', 'yahei'),
  trans: blk('inter', 'yahei'),
  ref: blk('inter', 'yahei'),
  front: blk('inter', 'yahei', true),
  mono: blk('mono', 'yahei')
}

const clonePlan = (p: FontPlan): FontPlan => Object.fromEntries(Object.entries(p).map(([k, v]) => [k, { ...v }])) as FontPlan

export const DEFAULT_FONT_PLAN: FontPlan = clonePlan(CLASSIC)

/** “全部统一”：所有块（除等宽）用正文的英文+中文字体；标题与论文信息保持加粗，倍率回到 1。 */
export function uniformPlan(plan: FontPlan): FontPlan {
  const b = plan.body
  const same = (bold: boolean): BlockFont => ({ latin: b.latin, cjk: b.cjk, latinCustom: b.latinCustom, cjkCustom: b.cjkCustom, scale: 1, bold })
  return {
    title: same(true),
    body: { ...b, scale: 1, bold: false },
    abstract: same(false),
    caption: same(false),
    trans: same(false),
    ref: same(false),
    front: same(true),
    mono: { ...plan.mono }
  }
}

/** 取预设对应的整套方案；“全部统一”以当前正文字体为准。 */
export function presetPlan(id: FontPresetId, current: FontPlan): FontPlan {
  if (id === 'classic') return clonePlan(CLASSIC)
  if (id === 'comfort') return clonePlan(COMFORT)
  if (id === 'sans') return clonePlan(SANS)
  return uniformPlan(current)
}

export const presetLineHeight = (id: FontPresetId): number | undefined => FONT_PRESETS.find((p) => p.id === id)?.lineHeight

const sameBlock = (a: BlockFont, b: BlockFont): boolean =>
  a.latin === b.latin && a.cjk === b.cjk && a.latinCustom === b.latinCustom && a.cjkCustom === b.cjkCustom && a.scale === b.scale && a.bold === b.bold
const samePlan = (a: FontPlan, b: FontPlan): boolean => (Object.keys(a) as FontBlock[]).every((k) => sameBlock(a[k], b[k]))

/** 当前方案对应哪个预设；逐块微调过就是 'custom'。 */
export function matchPreset(plan: FontPlan): FontPresetId | 'custom' {
  for (const id of ['classic', 'comfort', 'sans'] as const) if (samePlan(plan, presetPlan(id, plan))) return id
  if (samePlan(plan, uniformPlan(plan))) return 'uniform'
  return 'custom'
}

const round2 = (n: number): number => Math.round(n * 100) / 100
const clampScale = (n: unknown, d: number): number => {
  const v = Number(n)
  return Number.isFinite(v) && v > 0 ? round2(Math.min(SCALE_MAX, Math.max(SCALE_MIN, v))) : d
}
const cleanCustom = (s: unknown): string => (typeof s === 'string' ? parseCustomNames(s).join(', ').slice(0, 200) : '')

/** 校正存储里或导入的字体方案：缺的块补默认，未知字体回到默认，倍率夹回范围。 */
export function sanitizeFontPlan(raw: unknown): FontPlan {
  const src = raw && typeof raw === 'object' ? (raw as Record<string, unknown>) : {}
  const latinKeys = new Set([...LATIN_FACES.map((f) => f.key), 'custom'])
  const cjkKeys = new Set([...CJK_FACES.map((f) => f.key), 'custom'])
  const out = {} as FontPlan
  for (const { key } of FONT_BLOCKS) {
    const d = DEFAULT_FONT_PLAN[key]
    const r = src[key] && typeof src[key] === 'object' ? (src[key] as Record<string, unknown>) : {}
    out[key] = {
      latin: typeof r.latin === 'string' && latinKeys.has(r.latin) ? r.latin : d.latin,
      cjk: typeof r.cjk === 'string' && cjkKeys.has(r.cjk) ? r.cjk : d.cjk,
      latinCustom: cleanCustom(r.latinCustom),
      cjkCustom: cleanCustom(r.cjkCustom),
      scale: clampScale(r.scale, d.scale),
      bold: typeof r.bold === 'boolean' ? r.bold : d.bold
    }
  }
  return out
}

/**
 * 旧版本只存一个 readFont（sans/serif/system）。迁移：
 * sans 是当年的默认值（分不出是不是用户主动选的），一律回到新的默认“论文经典”；serif → 屏幕舒适；system → 无衬线。
 */
export function legacyFontPlan(readFont: unknown): { plan: FontPlan; preset: FontPresetId } | null {
  if (readFont === 'sans') return { plan: presetPlan('classic', DEFAULT_FONT_PLAN), preset: 'classic' }
  if (readFont === 'serif') return { plan: presetPlan('comfort', DEFAULT_FONT_PLAN), preset: 'comfort' }
  if (readFont === 'system') return { plan: presetPlan('sans', DEFAULT_FONT_PLAN), preset: 'sans' }
  return null
}

/** 写到 <html> 上的 CSS 变量：每个块的字体栈、字号倍率、字重。样式只读这些变量。 */
export function fontCssVars(plan: FontPlan): Record<string, string> {
  const vars: Record<string, string> = { '--rd-font': buildStack(plan.body) }
  for (const { key } of FONT_BLOCKS) {
    vars[`--rd-font-${key}`] = buildStack(plan[key])
    vars[`--rd-scale-${key}`] = String(plan[key].scale)
    vars[`--rd-weight-${key}`] = plan[key].bold ? '700' : 'var(--rd-weight, 400)'
  }
  return vars
}

export function exportFontPlan(plan: FontPlan): string {
  return JSON.stringify({ version: 1, fontPlan: plan }, null, 2)
}

export function importFontPlan(text: string): { ok: true; plan: FontPlan } | { ok: false; error: string } {
  let data: unknown
  try {
    data = JSON.parse(text)
  } catch {
    return { ok: false, error: '不是有效的 JSON，请粘贴导出的字体方案。' }
  }
  const obj = data && typeof data === 'object' ? (data as Record<string, unknown>) : null
  const plan = obj && typeof obj.fontPlan === 'object' ? obj.fontPlan : obj
  if (!plan || typeof plan !== 'object' || !FONT_BLOCKS.some(({ key }) => key in (plan as Record<string, unknown>))) {
    return { ok: false, error: '里面没有字体方案，找不到标题、正文等各块的设置。' }
  }
  return { ok: true, plan: sanitizeFontPlan(plan) }
}

// ---------------------------------------------------------------- 字体检测（画布测宽法）
/** 返回某个 font-family 串的文字宽度；测不了（没有画布）时用 null 代替函数。 */
export type MeasureFn = (family: string) => number

const PROBE_BASES = ['monospace', 'serif', 'sans-serif']

/**
 * 画布测宽：同一段文字用“目标字体, 通用族”和单独的通用族各量一次，只要有一个通用族下宽度不同，
 * 说明目标字体真的存在（没装的字体会退回通用族，宽度完全一样）。用三个通用族是因为中文系统里
 * 默认衬线体可能恰好就是宋体，只比一个会漏检。
 */
export function isFontInstalled(name: string, measure: MeasureFn): boolean {
  return PROBE_BASES.some((base) => Math.abs(measure(`'${name}', ${base}`) - measure(base)) > 0.01)
}

export function isFaceAvailable(face: Face, measure: MeasureFn | null): boolean {
  if (face.probe.length === 0) return true // 应用自带
  if (!measure) return true // 测不了就不拦着用户
  return face.probe.some((n) => isFontInstalled(n, measure))
}

/** 自定义字体名里有没有找到：返回找到的名字（没找到的为 null）。 */
export function findInstalledName(input: string, measure: MeasureFn | null): { names: string[]; found: string | null } {
  const names = parseCustomNames(input)
  if (!measure) return { names, found: names[0] ?? null }
  return { names, found: names.find((n) => isFontInstalled(n, measure)) ?? null }
}

/** 在浏览器里用 canvas 量宽度；没有 canvas（测试环境）返回 null。字符串里混了拉丁字母、数字、中文和标点，字形宽度才会因字体而不同。 */
export function makeCanvasMeasure(): MeasureFn | null {
  if (typeof document === 'undefined') return null
  const ctx = document.createElement('canvas').getContext?.('2d')
  if (!ctx) return null
  const text = 'mmmmmmmmmmlliWi0Oo1Ag中文字体测试，。ABab'
  return (family) => {
    ctx.font = `72px ${family}`
    return ctx.measureText(text).width
  }
}
