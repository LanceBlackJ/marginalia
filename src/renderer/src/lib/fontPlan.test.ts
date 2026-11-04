import { describe, expect, it } from 'vitest'
import {
  CJK_FACES,
  DEFAULT_FONT_PLAN,
  FONT_BLOCKS,
  LATIN_FACES,
  SCALE_MAX,
  SCALE_MIN,
  buildStack,
  exportFontPlan,
  findInstalledName,
  fontCssVars,
  importFontPlan,
  isFaceAvailable,
  isFontInstalled,
  legacyFontPlan,
  matchPreset,
  parseCustomNames,
  presetLineHeight,
  presetPlan,
  sanitizeFontPlan,
  uniformPlan,
  type BlockFont,
  type FontPlan,
  type MeasureFn
} from './fontPlan'
import { DEFAULTS, sanitizePrefs } from '@/stores/prefsStore'

const edit = (plan: FontPlan, block: keyof FontPlan, patch: Partial<BlockFont>): FontPlan => ({ ...plan, [block]: { ...plan[block], ...patch } })

describe('buildStack：英文字体在前，中文字体在后', () => {
  it('默认正文是 Times New Roman + 宋体，最后是衬线通用族', () => {
    const stack = buildStack(DEFAULT_FONT_PLAN.body)
    expect(stack.startsWith("'Times New Roman'")).toBe(true)
    expect(stack.indexOf("'Times New Roman'")).toBeLessThan(stack.indexOf("'SimSun'"))
    expect(stack.indexOf("'Noto Serif SC'")).toBeLessThan(stack.indexOf("'SimSun'")) // 装了思源宋体则优先
    expect(stack.endsWith('serif')).toBe(true)
  })
  it('标题用黑体、等宽用等宽通用族，没有重复的字体名', () => {
    expect(buildStack(DEFAULT_FONT_PLAN.title)).toContain("'SimHei'")
    const mono = buildStack(DEFAULT_FONT_PLAN.mono)
    expect(mono.endsWith('monospace')).toBe(true)
    for (const b of FONT_BLOCKS) {
      const names = buildStack(DEFAULT_FONT_PLAN[b.key]).split(', ')
      expect(new Set(names).size).toBe(names.length)
    }
  })
  it('自定义字体：用输入的名字；没填就退回默认字体', () => {
    const b = { ...DEFAULT_FONT_PLAN.body, latin: 'custom', latinCustom: "Palatino, 'Book Antiqua'" }
    expect(buildStack(b).startsWith("'Palatino', 'Book Antiqua'")).toBe(true)
    expect(buildStack({ ...b, latinCustom: '  ' })).toContain("'Times New Roman'")
  })
})

describe('parseCustomNames', () => {
  it('去引号、去危险字符、按中英文逗号拆分、最多 6 个', () => {
    expect(parseCustomNames(`"Foo Bar", 'Baz'，Qux;{x}`)).toEqual(['Foo Bar', 'Baz', 'Quxx'])
    expect(parseCustomNames('a,b,c,d,e,f,g,h')).toHaveLength(6)
    expect(parseCustomNames(' , ,')).toEqual([])
    expect(parseCustomNames('x'.repeat(61))).toEqual([])
  })
})

describe('预设与“自定义”', () => {
  it('默认方案就是“论文经典”', () => {
    expect(matchPreset(DEFAULT_FONT_PLAN)).toBe('classic')
  })
  it('套用预设后能匹配回该预设，逐块微调后变成自定义', () => {
    for (const id of ['classic', 'comfort', 'sans'] as const) expect(matchPreset(presetPlan(id, DEFAULT_FONT_PLAN))).toBe(id)
    expect(matchPreset(edit(DEFAULT_FONT_PLAN, 'caption', { latin: 'georgia' }))).toBe('custom')
    expect(matchPreset(edit(DEFAULT_FONT_PLAN, 'title', { scale: 1.2 }))).toBe('custom')
  })
  it('“全部统一”：所有块（除等宽）用正文的字体，标题和论文信息保持加粗', () => {
    const base = edit(DEFAULT_FONT_PLAN, 'body', { latin: 'georgia', cjk: 'noto-serif' })
    const u = presetPlan('uniform', base)
    for (const k of ['title', 'abstract', 'caption', 'trans', 'ref', 'front'] as const) {
      expect(u[k].latin).toBe('georgia')
      expect(u[k].cjk).toBe('noto-serif')
    }
    expect(u.title.bold && u.front.bold).toBe(true)
    expect(u.body.bold).toBe(false)
    expect(u.mono).toEqual(base.mono)
    expect(matchPreset(u)).toBe('uniform')
    expect(uniformPlan(u)).toEqual(u)
  })
  it('“屏幕舒适”带更大的行高，其它预设不改行高', () => {
    expect(presetLineHeight('comfort')).toBeGreaterThan(1.8)
    expect(presetLineHeight('classic')).toBeUndefined()
  })
  it('套用预设不会改到原来的默认方案（深拷贝）', () => {
    const p = presetPlan('classic', DEFAULT_FONT_PLAN)
    p.body.latin = 'arial'
    expect(DEFAULT_FONT_PLAN.body.latin).toBe('tnr')
  })
})

describe('sanitizeFontPlan', () => {
  it('缺的块补默认，未知字体回到默认，倍率夹回范围，自定义名清理', () => {
    const p = sanitizeFontPlan({ body: { latin: 'nope', cjk: 'heiti', scale: 9, bold: 'yes', latinCustom: 'Foo;Bar' }, title: { scale: 0.1 } })
    expect(p.body.latin).toBe(DEFAULT_FONT_PLAN.body.latin)
    expect(p.body.cjk).toBe('heiti')
    expect(p.body.scale).toBe(SCALE_MAX)
    expect(p.body.bold).toBe(DEFAULT_FONT_PLAN.body.bold)
    expect(p.body.latinCustom).toBe('FooBar') // 去掉分号这类危险字符
    expect(p.title.scale).toBe(SCALE_MIN)
    expect(p.ref).toEqual(DEFAULT_FONT_PLAN.ref)
    expect(Object.keys(p).sort()).toEqual(FONT_BLOCKS.map((b) => b.key).sort())
  })
  it('完全无效的输入给出默认方案', () => {
    expect(sanitizeFontPlan(null)).toEqual(DEFAULT_FONT_PLAN)
    expect(sanitizeFontPlan('x')).toEqual(DEFAULT_FONT_PLAN)
  })
})

describe('旧偏好迁移（readFont → fontPlan）', () => {
  it('sans（当年的默认）→ 新默认“论文经典”；serif → 屏幕舒适；system → 无衬线', () => {
    expect(legacyFontPlan('sans')?.preset).toBe('classic')
    expect(legacyFontPlan('serif')?.preset).toBe('comfort')
    expect(legacyFontPlan('system')?.preset).toBe('sans')
    expect(legacyFontPlan('weird')).toBeNull()
  })
  it('sanitizePrefs：只有旧 readFont 时迁移，有 fontPlan 时以它为准，什么都没有时用默认', () => {
    expect(matchPreset(sanitizePrefs({ readFont: 'serif' }).fontPlan)).toBe('comfort')
    expect(matchPreset(sanitizePrefs({ readFont: 'system' }).fontPlan)).toBe('sans')
    expect(matchPreset(sanitizePrefs({ readFont: 'sans' }).fontPlan)).toBe('classic')
    const mine = edit(DEFAULT_FONT_PLAN, 'title', { latin: 'arial' })
    expect(sanitizePrefs({ readFont: 'serif', fontPlan: mine }).fontPlan.title.latin).toBe('arial')
    expect(sanitizePrefs(null).fontPlan).toEqual(DEFAULTS.fontPlan)
    expect('readFont' in sanitizePrefs({ readFont: 'serif' })).toBe(false) // 旧字段不再保留
  })
})

describe('fontCssVars', () => {
  it('每个块都有字体栈、倍率、字重；--rd-font 是正文字体栈', () => {
    const plan = edit(DEFAULT_FONT_PLAN, 'caption', { scale: 1.3, bold: true })
    const v = fontCssVars(plan)
    for (const { key } of FONT_BLOCKS) {
      expect(v[`--rd-font-${key}`]).toBe(buildStack(plan[key]))
      expect(v[`--rd-scale-${key}`]).toBeDefined()
      expect(v[`--rd-weight-${key}`]).toBeDefined()
    }
    expect(v['--rd-font']).toBe(v['--rd-font-body'])
    expect(v['--rd-scale-caption']).toBe('1.3')
    expect(v['--rd-weight-caption']).toBe('700')
    expect(v['--rd-weight-body']).toBe('var(--rd-weight, 400)') // 没勾加粗：跟随全局字重
  })
})

describe('字体检测（可注入测宽函数）', () => {
  // 模拟：没装的字体会退回通用族，宽度和通用族一样；装了的字体宽度不同
  const makeMeasure = (installed: Record<string, number>, defaults: Record<string, number> = { monospace: 100, serif: 110, 'sans-serif': 120 }): MeasureFn => (family) => {
    const m = /^'(.+)', (.+)$/.exec(family)
    if (!m) return defaults[family] ?? 100
    return installed[m[1]] ?? defaults[m[2]] ?? 100
  }
  it('没装的字体：三个通用族下宽度都相同 → 未安装', () => {
    expect(isFontInstalled('Nope Font', makeMeasure({}))).toBe(false)
  })
  it('装了的字体：只要和某个通用族宽度不同就算装了', () => {
    expect(isFontInstalled('Times New Roman', makeMeasure({ 'Times New Roman': 105 }))).toBe(true)
  })
  it('字体恰好和系统默认衬线体一样宽（如宋体就是默认衬线）也能靠另外两个通用族检出', () => {
    expect(isFontInstalled('SimSun', makeMeasure({ SimSun: 110 }))).toBe(true)
  })
  it('应用自带的字体总是可用；测不了宽度时不拦着用户', () => {
    const inter = LATIN_FACES.find((f) => f.key === 'inter')!
    expect(isFaceAvailable(inter, makeMeasure({}))).toBe(true)
    const tnr = LATIN_FACES.find((f) => f.key === 'tnr')!
    expect(isFaceAvailable(tnr, null)).toBe(true)
    expect(isFaceAvailable(tnr, makeMeasure({}))).toBe(false)
    expect(isFaceAvailable(tnr, makeMeasure({ 'Times New Roman': 99 }))).toBe(true)
  })
  it('备选字体里任意一个装了就可用（如楷体）', () => {
    const kaiti = CJK_FACES.find((f) => f.key === 'kaiti')!
    expect(isFaceAvailable(kaiti, makeMeasure({ STKaiti: 130 }))).toBe(true)
    expect(isFaceAvailable(kaiti, makeMeasure({}))).toBe(false)
  })
  it('自定义输入：返回第一个找到的字体名，找不到则 found 为 null', () => {
    const m = makeMeasure({ Palatino: 140 })
    expect(findInstalledName('Nope, Palatino', m)).toEqual({ names: ['Nope', 'Palatino'], found: 'Palatino' })
    expect(findInstalledName('Nope', m).found).toBeNull()
    expect(findInstalledName('', m)).toEqual({ names: [], found: null })
  })
})

describe('导出 / 导入字体方案', () => {
  it('导出再导入得到同样的方案', () => {
    const plan = edit(presetPlan('comfort', DEFAULT_FONT_PLAN), 'trans', { latin: 'custom', latinCustom: 'Palatino', scale: 1.1 })
    const r = importFontPlan(exportFontPlan(plan))
    expect(r.ok).toBe(true)
    if (r.ok) expect(r.plan).toEqual(plan)
  })
  it('不是 JSON、或里面没有字体方案时给出可读的错误', () => {
    const a = importFontPlan('not json')
    expect(a.ok).toBe(false)
    const b = importFontPlan('{"hello": 1}')
    expect(b.ok).toBe(false)
    if (!b.ok) expect(b.error).toContain('字体方案')
  })
  it('导入时会校正：未知字体回到默认、倍率夹回范围', () => {
    const r = importFontPlan(JSON.stringify({ body: { latin: 'bogus', scale: 5 } }))
    expect(r.ok).toBe(true)
    if (r.ok) {
      expect(r.plan.body.latin).toBe('tnr')
      expect(r.plan.body.scale).toBe(SCALE_MAX)
    }
  })
})
