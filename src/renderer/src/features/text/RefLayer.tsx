import { SelectTranslate } from '@/components/SelectTranslate'
import { RefDrawer } from './RefDrawer'
import { RefHoverCard } from './RefHoverCard'
import { RefMenu } from './RefMenu'
import { RefPopover } from './RefPopover'
import { useRefStore } from './refStore'
import './references.css'

/** 引用相关的浮层都挂在这里：浮层、右键菜单、详情抽屉，以及这些浮层里英文的划词翻译（由 TextReader 在末尾挂一次）。 */
export function RefLayer() {
  const paperId = useRefStore((s) => s.paperId)
  return (
    <>
      <RefHoverCard />
      <RefPopover />
      <RefMenu />
      <RefDrawer />
      <SelectTranslate paperId={paperId} />
    </>
  )
}
