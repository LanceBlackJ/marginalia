/** 文库的文件夹、智能视图与筛选项 API 客户端。 */
import { apiRequest } from './api'
import type { FolderItem, FolderList, PaperFacets, PaperFilters, ReviewCategory, ReviewProject, SmartView } from './types.library'

const get = <T>(path: string): Promise<T> => apiRequest<T>('GET', path, {})
const post = <T>(path: string, body?: unknown): Promise<T> => apiRequest<T>('POST', path, { body: body ?? {} })
const patch = <T>(path: string, body: unknown): Promise<T> => apiRequest<T>('PATCH', path, { body })
const del = <T>(path: string): Promise<T> => apiRequest<T>('DELETE', path, {})

export const libraryApi = {
  folders: () => get<FolderList>('/api/folders'),
  createFolder: (name: string, parentId: string | null = null) => post<FolderItem>('/api/folders', { name, parent_id: parentId }),
  renameFolder: (id: string, name: string) => patch<FolderItem>(`/api/folders/${id}`, { name }),
  /** 移动文件夹：parentId=null 表示移到最上层。 */
  moveFolder: (id: string, parentId: string | null) => patch<FolderItem>(`/api/folders/${id}`, { parent_id: parentId }),
  reorderFolders: (parentId: string | null, ids: string[]) => post<{ ok: boolean }>('/api/folders/reorder', { parent_id: parentId, ids }),
  deleteFolder: (id: string) => del<{ ok: boolean; deleted_folders: number; moved_papers: number }>(`/api/folders/${id}`),
  /** 把论文移进文件夹；folderId=null 表示移回“未分类”。补充材料子论文会被跳过。 */
  movePapers: (paperIds: string[], folderId: string | null) =>
    post<{ ok: boolean; moved: number; skipped_supplements: number }>('/api/papers/move', { paper_ids: paperIds, folder_id: folderId }),
  /** 手动排序：把这批论文按给出的顺序重新排（只给了筛选后的一部分也行） */
  reorderPapers: (ids: string[]) => post<{ ok: boolean; count: number }>('/api/papers/reorder', { ids }),
  smartViews: () => get<{ items: SmartView[] }>('/api/smart-views'),
  createSmartView: (name: string, query: PaperFilters) => post<SmartView>('/api/smart-views', { name, query }),
  deleteSmartView: (id: string) => del<{ ok: boolean }>(`/api/smart-views/${id}`),
  facets: () => get<PaperFacets>('/api/papers/facets'),
  reviews: () => get<{ items: ReviewProject[] }>('/api/reviews'),
  createReview: (name: string, description = '') => post<ReviewProject>('/api/reviews', { name, description }),
  renameReview: (id: string, name: string) => patch<ReviewProject>(`/api/reviews/${id}`, { name }),
  updateReview: (id: string, body: { name?: string; description?: string }) => patch<ReviewProject>(`/api/reviews/${id}`, body),
  deleteReview: (id: string) => del<{ ok: boolean }>(`/api/reviews/${id}`),
  reviewCategories: (id: string) => get<{ items: ReviewCategory[] }>(`/api/reviews/${id}/categories`),
  createReviewCategory: (id: string, name: string) => post<ReviewCategory>(`/api/reviews/${id}/categories`, { name }),
  addToReview: (id: string, paperIds: string[], categoryId?: string) => post<{ added: number; existing: number; missing: string[] }>(`/api/reviews/${id}/papers`, { paper_ids: paperIds, category_id: categoryId }),
  updateReviewPaper: (reviewId: string, paperId: string, body: { category_id?: string; screening?: string; exclusion_reason?: string }) => patch<Record<string, unknown>>(`/api/reviews/${reviewId}/papers/${paperId}`, body),
  removeFromReview: (reviewId: string, paperId: string) => del<{ ok: boolean }>(`/api/reviews/${reviewId}/papers/${paperId}`)
}
