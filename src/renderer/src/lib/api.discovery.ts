import { apiRequest } from './api'
import type {
  AdvancedRequest, AdvancedResponse, CodeResponse, ComparePreflight, CompareMode, Comparison, ComparisonSummary, ConceptState, DirectionResponse, DiscoverySettings,
  FieldInfo, GlobalNote, InferredInterest, ItemKind, KbCandidate, KbItem, KbRelation, KbTopic, MarkdownExport, PersonalResponse, Profile,
  ProfileConcept, ProfileMeta, ProfileSuggestion, RandomResponse, SearchRequest, SearchResponse, SourceInfo, TopicSuggestion, LibraryVenue, VenueIntro, VenueList, VenuePageData, VenueSearchHit, Work
} from './types.discovery'

/** 推荐的筛选：年份区间、来源水平，以及“再推荐一些”时已经显示过的论文。 */
export interface RecommendOpts {
  year_from?: number | null
  year_to?: number | null
  level?: 'any' | 'peer' | 'core' | 'top'
  exclude?: string[]
}

type Query = Record<string, string | number | boolean | null | undefined>
const get = <T>(path: string, query?: Query, signal?: AbortSignal) => apiRequest<T>('GET', path, { query, signal })
const post = <T>(path: string, body?: unknown, signal?: AbortSignal) => apiRequest<T>('POST', path, { body: body ?? {}, signal })
const put = <T>(path: string, body?: unknown) => apiRequest<T>('PUT', path, { body: body ?? {} })
const patch = <T>(path: string, body: unknown) => apiRequest<T>('PATCH', path, { body })
const del = <T>(path: string) => apiRequest<T>('DELETE', path)

export const discoveryApi = {
  /** Scopus 式的字段检索（标题/作者/摘要/期刊/DOI，且/或/非，标题可精确匹配）。 */
  advanced: (body: AdvancedRequest, signal?: AbortSignal) => post<AdvancedResponse>('/api/discovery/advanced', body, signal),
  search: (body: SearchRequest, signal?: AbortSignal) => post<SearchResponse>('/api/discovery/search', body, signal),
  direction: (topic: string, ai = true, opts: RecommendOpts = {}, signal?: AbortSignal) =>
    post<DirectionResponse>('/api/discovery/recommend/direction', { topic, ai, ...opts }, signal),
  personal: (ai = true, opts: RecommendOpts = {}, signal?: AbortSignal) => post<PersonalResponse>('/api/discovery/recommend/personal', { ai, ...opts }, signal),
  random: (body: { field_id?: number | null; topic?: string | null; exclude?: string[]; seen_fields?: number[]; count?: number; ai?: boolean; year_from?: number | null; year_to?: number | null }, signal?: AbortSignal) =>
    post<RandomResponse>('/api/discovery/random', body, signal),
  /** 某个期刊/会议的介绍：名单内来源的简介 + OpenAlex 的真实统计。 */
  venueInfo: (name: string, type?: string | null) => get<VenueIntro>('/api/discovery/venue-info', { name, type }),
  /** 我文献库里的论文都发表在哪些期刊/会议上。 */
  libraryVenues: () => get<{ items: LibraryVenue[]; papers: number }>('/api/discovery/library-venues'),
  /** 一个期刊/会议的页面：介绍和这个来源上被引最多、最新的论文。用名录 id 或来源名称。 */
  venuePage: (q: { id?: string | null; name?: string | null; type?: string | null; oa?: string | null; works?: boolean }) =>
    get<VenuePageData>('/api/discovery/venue-page', { id: q.id ?? undefined, name: q.name ?? undefined, type: q.type ?? undefined, oa: q.oa ?? undefined, works: q.works ?? true }),
  /** 在 OpenAlex 里按名称或缩写检索期刊/会议（名录里没有的也能找到）。 */
  venueSearch: (q: string) => get<{ items: VenueSearchHit[]; error?: string }>('/api/discovery/venue-search', { q }),
  fields: () => get<{ items: FieldInfo[] }>('/api/discovery/fields'),
  /** 随机探索收录的顶刊顶会名单（可审阅）。 */
  venues: () => get<VenueList>('/api/discovery/venues'),
  similar: (body: { doi?: string | null; title?: string | null }) =>
    post<{ seed: { title: string; doi: string | null }; results: Work[] }>('/api/discovery/similar', body),
  findCode: (body: { title?: string | null; doi?: string | null; arxiv_id?: string | null }) => post<CodeResponse>('/api/discovery/find-code', body),
  saved: () => get<{ items: Work[] }>('/api/discovery/saved'),
  save: (work: Work) => post<Work>('/api/discovery/saved', work),
  unsave: (key: string) => del<{ ok: boolean }>(`/api/discovery/saved/${encodeURIComponent(key).replace(/%2F/g, '/').replace(/%3A/g, ':')}`),
  libraryMatch: (items: Array<{ key: string; doi: string | null; arxiv_id: string | null; title: string }>) =>
    post<{ matches: Record<string, string> }>('/api/discovery/library-match', { items }),
  settings: () => get<DiscoverySettings>('/api/discovery/settings'),
  putSettings: (body: Partial<DiscoverySettings>) => put<DiscoverySettings>('/api/discovery/settings', body),
  sources: () => get<{ items: SourceInfo[] }>('/api/discovery/sources')
}

export const compareApi = {
  preflight: (paperIds: string[], mode: CompareMode) => get<ComparePreflight>('/api/compare/preflight', { paper_ids: paperIds.join(','), mode }),
  create: (paperIds: string[], mode: CompareMode, title?: string) => post<{ id: string; job_id: string }>('/api/compare', { paper_ids: paperIds, mode, title }),
  list: () => get<{ items: ComparisonSummary[] }>('/api/compare'),
  get: (id: string) => get<Comparison>(`/api/compare/${id}`),
  remove: (id: string) => del<{ ok: boolean }>(`/api/compare/${id}`),
  rerun: (id: string) => post<{ id: string; job_id: string }>(`/api/compare/${id}/rerun`)
}

export const kbApi = {
  topics: () => get<{ items: KbTopic[] }>('/api/kb/topics'),
  createTopic: (name: string, description = '') => post<KbTopic>('/api/kb/topics', { name, description }),
  updateTopic: (id: string, body: { name?: string; description?: string }) => patch<KbTopic>(`/api/kb/topics/${id}`, body),
  deleteTopic: (id: string) => del<{ ok: boolean }>(`/api/kb/topics/${id}`),
  topicPapers: (id: string) => get<{ items: Array<{ id: string; title: string; year: number | null; doi: string | null }> }>(`/api/kb/topics/${id}/papers`),
  addTopicPaper: (id: string, paperId: string) => post<{ ok: boolean }>(`/api/kb/topics/${id}/papers`, { paper_id: paperId }),
  removeTopicPaper: (id: string, paperId: string) => del<{ ok: boolean }>(`/api/kb/topics/${id}/papers/${paperId}`),
  suggestTopics: () => post<{ items: TopicSuggestion[] }>('/api/kb/topics/suggest'),
  topicSuggestions: () => get<{ items: TopicSuggestion[] }>('/api/kb/topic-suggestions'),
  acceptTopicSuggestion: (id: string) => post<KbTopic>(`/api/kb/topic-suggestions/${id}/accept`),
  rejectTopicSuggestion: (id: string) => post<{ ok: boolean }>(`/api/kb/topic-suggestions/${id}/reject`),

  items: (q: { topic_id?: string; paper_id?: string; q?: string; unassigned?: boolean } = {}) => get<{ items: KbItem[] }>('/api/kb/items', { ...q }),
  item: (id: string) => get<KbItem>(`/api/kb/items/${id}`),
  createItem: (body: { title: string; content: string; kind?: ItemKind; topic_ids?: string[]; source_paper_id?: string | null; evidence?: Array<{ paper_id?: string; ord?: number }> }) =>
    post<KbItem>('/api/kb/items', body),
  updateItem: (id: string, body: { title?: string; content?: string; kind?: ItemKind; topic_ids?: string[] }) => patch<KbItem>(`/api/kb/items/${id}`, body),
  deleteItem: (id: string) => del<{ ok: boolean }>(`/api/kb/items/${id}`),

  extract: (paperId: string) => post<{ job_id: string }>('/api/kb/extract', { paper_id: paperId }),
  candidates: (paperId?: string) => get<{ items: KbCandidate[] }>('/api/kb/candidates', { paper_id: paperId }),
  acceptCandidate: (id: string, body: { topic_id?: string | null; new_topic?: string | null; title?: string; content?: string; kind?: ItemKind }) =>
    post<KbItem>(`/api/kb/candidates/${id}/accept`, body),
  rejectCandidate: (id: string) => post<{ ok: boolean }>(`/api/kb/candidates/${id}/reject`),

  relations: (q: { topic_id?: string; status?: string } = {}) => get<{ items: KbRelation[] }>('/api/kb/relations', { ...q }),
  suggestRelations: (topicId: string) => post<{ suggested: KbRelation[] }>('/api/kb/relations/suggest', { topic_id: topicId }),
  confirmRelation: (id: string) => post<KbRelation>(`/api/kb/relations/${id}/confirm`),
  deleteRelation: (id: string) => del<{ ok: boolean }>(`/api/kb/relations/${id}`)
}

export const profileApi = {
  get: () => get<Profile>('/api/profile'),
  putMeta: (body: Partial<ProfileMeta>) => put<ProfileMeta>('/api/profile/meta', body),
  addConcept: (name: string, state: ConceptState, note = '') => post<ProfileConcept>('/api/profile/concepts', { name, state, note }),
  updateConcept: (id: string, body: { name?: string; state?: ConceptState; note?: string }) => patch<ProfileConcept>(`/api/profile/concepts/${id}`, body),
  deleteConcept: (id: string) => del<{ ok: boolean }>(`/api/profile/concepts/${id}`),
  interests: () => get<{ items: InferredInterest[] }>('/api/profile/interests'),
  muteInterest: (topicId: string) => post<{ ok: boolean }>(`/api/profile/interests/${topicId}/mute`),
  suggest: () => post<{ items: ProfileSuggestion[] }>('/api/profile/suggest'),
  acceptSuggestion: (id: string, state?: ConceptState) => post<ProfileConcept>(`/api/profile/suggestions/${id}/accept`, { state }),
  dismissSuggestion: (id: string) => post<{ ok: boolean }>(`/api/profile/suggestions/${id}/dismiss`)
}

export const notesApi = {
  all: (q: { q?: string; paper_id?: string; limit?: number; offset?: number } = {}) =>
    get<{ items: GlobalNote[]; total: number }>('/api/notes/all', { ...q })
}

export const exportApi = {
  markdown: (body: { kind: 'topic' | 'notes' | 'cards' | 'viewpoints'; topic_id?: string; paper_ids?: string[]; include_evidence?: boolean }) =>
    post<MarkdownExport>('/api/export/markdown', body)
}
