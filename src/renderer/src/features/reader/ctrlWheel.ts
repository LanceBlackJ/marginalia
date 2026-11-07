/**
 * Ctrl + 鼠标滚轮 = 调整大小：滚一格（或触控板捏合累计够一格）调一档，向上放大、向下缩小。
 * 必须 preventDefault，否则整个页面会被浏览器按比例缩放；滚轮事件要用 { passive: false } 才允许阻止。
 * 一次滚动累计够 THRESHOLD 才算一档（鼠标一格滚轮的 deltaY 约 100，触控板每帧只有几个像素），停顿 IDLE_MS 后重新累计。
 */
const THRESHOLD = 40
const IDLE_MS = 250

export function onCtrlWheel(el: HTMLElement, onStep: (dir: 1 | -1) => void): () => void {
  let acc = 0
  let last = 0
  const handler = (e: WheelEvent): void => {
    if (!e.ctrlKey) return
    e.preventDefault()
    const now = performance.now()
    if (now - last > IDLE_MS) acc = 0
    last = now
    acc += e.deltaY
    if (Math.abs(acc) >= THRESHOLD) {
      onStep(acc < 0 ? 1 : -1)
      acc = 0
    }
  }
  el.addEventListener('wheel', handler, { passive: false })
  return () => el.removeEventListener('wheel', handler)
}
