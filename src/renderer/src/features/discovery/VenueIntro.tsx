import { useState } from 'react'
import { VenueCardBody } from './VenueHover'
import { useVenueInfo } from './venueInfo'

/** “关于这个来源”：点开才查。名单内来源有一句简介，数字都来自 OpenAlex，查不到的不显示，不编造影响因子或分区。 */
export function VenueIntro({ name, type }: { name: string | null | undefined; type?: string | null }) {
  const [open, setOpen] = useState(false)
  const { info, loading } = useVenueInfo(name, type, open)
  if (!name) return null
  return (
    <div className="pd-dc-venueintro">
      <button type="button" className="pd-dc-more" onClick={() => setOpen(!open)}>
        {open ? '收起来源介绍' : '这个来源是什么水平'}
      </button>
      {open && (
        <div className="pd-dc-venueintro-body">
          <VenueCardBody info={info} loading={loading} />
        </div>
      )}
    </div>
  )
}
