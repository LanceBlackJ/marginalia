// @vitest-environment jsdom
import { afterEach, describe, expect, it, vi } from 'vitest'
import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import { KindFilter, countKinds, filterByKind } from './KindFilter'

afterEach(cleanup)
const w = (kind: string, kind_label: string) => ({ kind, kind_label })
const works = [w('research', '研究论文'), w('review', '综述'), w('research', '研究论文'), w('dataset', '数据集与基准')]

describe('论文类型筛选', () => {
  it('按固定顺序统计各类型数量：综述排最前', () => {
    expect(countKinds(works).map((k) => `${k.label}${k.count}`)).toEqual(['综述1', '研究论文2', '数据集与基准1'])
  })

  it('只留下选中的类型；没选就是全部', () => {
    expect(filterByKind(works, 'research')).toHaveLength(2)
    expect(filterByKind(works, null)).toHaveLength(4)
  })

  it('点标签选中、再点取消；只有一种类型时不显示', () => {
    const onChange = vi.fn()
    const { rerender } = render(<KindFilter works={works} value={null} onChange={onChange} />)
    fireEvent.click(screen.getByText('综述 1'))
    expect(onChange).toHaveBeenLastCalledWith('review')
    rerender(<KindFilter works={works} value="review" onChange={onChange} />)
    fireEvent.click(screen.getByText('综述 1'))
    expect(onChange).toHaveBeenLastCalledWith(null)
    cleanup()
    const { container } = render(<KindFilter works={[w('research', '研究论文')]} value={null} onChange={onChange} />)
    expect(container.firstChild).toBeNull()
  })
})
