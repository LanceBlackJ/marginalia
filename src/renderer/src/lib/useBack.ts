import { useCallback } from 'react'
import { useLocation, useNavigate } from 'react-router-dom'

/**
 * 统一的“返回”：回到你来的上一页（文献库的筛选、书架的位置、笔记页等都原样保留）。
 * 刚打开应用、或从外部直接进到这一页时没有上一页，才回到 fallback（这一页的上级页面）。
 */
export function useBack(fallback: string): () => void {
  const navigate = useNavigate()
  const location = useLocation()
  return useCallback(() => {
    if (location.key !== 'default') navigate(-1)
    else navigate(fallback, { replace: true })
  }, [navigate, location.key, fallback])
}
