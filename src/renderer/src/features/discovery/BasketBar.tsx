import { useNavigate } from 'react-router-dom'
import { GitCompare, X } from 'lucide-react'
import { Button } from '@/components/ui'
import { useLibraryPapers } from '@/features/shared/useLibraryPapers'
import { useBasket } from './discoveryStore'

/** 比较篮：来自发现/推荐/随机页的“比较”操作，只能放入已在文献库中的论文。 */
export function BasketBar() {
  const navigate = useNavigate()
  const ids = useBasket((s) => s.ids)
  const remove = useBasket((s) => s.remove)
  const clear = useBasket((s) => s.clear)
  const { byId } = useLibraryPapers()
  if (ids.length === 0) return null
  return (
    <div className="pd-dc-basket" role="region" aria-label="比较篮">
      <GitCompare size={15} aria-hidden />
      <span>比较篮 {ids.length}/8</span>
      <div className="pd-dc-basket-items">
        {ids.map((id) => (
          <span key={id} className="pd-dc-chip" title={byId.get(id)?.title}>
            <span>{byId.get(id)?.title ?? '论文已不在文献库'}</span>
            <button type="button" aria-label="移出比较篮" onClick={() => remove(id)}>
              <X size={12} />
            </button>
          </span>
        ))}
      </div>
      <Button size="sm" variant="ghost" onClick={clear}>
        清空
      </Button>
      <Button
        size="sm"
        variant="primary"
        disabled={ids.length < 2}
        disabledReason="至少放入 2 篇论文才能比较"
        onClick={() => navigate('/compare')}
      >
        去比较
      </Button>
    </div>
  )
}
