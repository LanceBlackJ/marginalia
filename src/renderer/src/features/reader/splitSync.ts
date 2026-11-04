/**
 * 并排视图的同步滚动防抖：一栏响应了对方发来的跳转之后，短时间内不再向对方回发，避免两栏来回“拔河”。
 */
let mutedUntil = 0

export function muteSync(ms = 700): void {
  mutedUntil = performance.now() + ms
}

export function syncMuted(): boolean {
  return performance.now() < mutedUntil
}
