/**
 * 在论文里选了一段话、再去右边 AI 面板点一下（输入框、按钮）时，浏览器会把左边的选区取消。
 * 这种“因为点了右边面板而取消”的选区要保留（提问就是针对它的），只有点回论文正文、按 Esc 或点芯片上的 × 才清掉。
 */
let keepUntil = 0
let installed = false

function install(): void {
  if (installed || typeof document === 'undefined') return
  installed = true
  document.addEventListener(
    'mousedown',
    (e) => {
      const t = e.target as HTMLElement | null
      if (t?.closest?.('.pd-rpanel')) keepUntil = Date.now() + 1500
    },
    true
  )
}

install()

/** 刚刚点的是右边面板：这次选区折叠不算“用户取消了选择”。 */
export function selectionCollapseIsFromPanel(): boolean {
  if (Date.now() < keepUntil) return true
  const ae = document.activeElement as HTMLElement | null
  return !!ae?.closest?.('.pd-rpanel')
}
