/** 学术发现 / 比较 / 知识库的类型（契约见 docs/API-discovery.md）。 */

export type PubStatus = 'published' | 'preprint' | 'accepted' | 'other'
export type DiscoveryMode = 'comprehensive' | 'recent' | 'impact' | 'related' | 'cross'

export interface WorkVersion {
  kind: 'preprint' | 'published' | 'accepted' | 'other'
  doi: string | null
  arxiv_id: string | null
  url: string | null
  venue: string | null
}

export interface WorkSource {
  name: string
  id: string | null
  url: string | null
}

export interface Work {
  key: string
  title: string
  authors: string[]
  year: number | null
  date: string | null
  venue: string | null
  venue_type: string | null
  doi: string | null
  arxiv_id: string | null
  pmid: string | null
  abstract: string | null
  url: string | null
  pdf_url: string | null
  status: PubStatus
  status_label: string
  /** 论文类型：review 综述 | research 研究论文 | dataset 数据集与基准 | perspective 观点与评论 | tutorial 教程与手册 | thesis 学位论文 | book 书籍与章节 */
  kind: string
  kind_label: string
  /** 判断依据（数据源给的类型，或标题/摘要里的措辞） */
  kind_evidence: string
  status_evidence: string
  peer_reviewed: boolean | null
  core_venue: boolean | null
  citations: number | null
  fwci: number | null
  percentile: number | null
  field: string | null
  field_zh?: string | null
  domain: string | null
  topics: string[]
  open_access: boolean | null
  work_type: string | null
  openalex_id: string | null
  sources: WorkSource[]
  versions: WorkVersion[]
  in_library: string | null
  score: number
  breakdown: Record<string, number>
  rank_note: string
  reason: string
  reason_source: 'ai' | 'rule' | ''
  relevance_note: string
  relation: string
  /** 随机探索：来源经白名单确认后的信息；其它页面没有这个字段 */
  top_venue?: TopVenue | null
  // 推荐扩展
  from_query?: string
  query_why?: string
  related_library?: { paper_id: string; title: string; shared: string[] } | null
  quality_facts?: string
  summary?: { research_question: string; contribution: string; why_read: string; source: string } | null
  saved_at?: string
}

export interface SourceReport {
  name: string
  ok: boolean
  count: number
  ms: number
  error: string | null
}

export interface SearchPlan {
  english_query: string
  alt_queries: string[]
  keywords: string[]
  domains: string[]
  ai_used: boolean
  notes: string[]
  analogies: string[]
}

export interface SearchResponse {
  query: string
  mode: DiscoveryMode
  plan: SearchPlan
  results: Work[]
  sources: SourceReport[]
  warnings: string[]
  ai: { planner: boolean; embeddings: boolean; rerank: boolean }
  total_candidates: number
  after_dedupe: number
  seed: { title: string; doi: string | null; year: number | null; openalex_id: string | null } | null
}

export type AdvancedField = 'all' | 'title' | 'author' | 'abstract' | 'tak' | 'venue' | 'doi'

export interface AdvancedRow {
  field: AdvancedField
  term: string
  op: 'and' | 'or' | 'not'
  exact: boolean
}

export interface AdvancedRequest {
  rows: AdvancedRow[]
  year_from?: number | null
  year_to?: number | null
  sort?: 'relevance' | 'cited' | 'date'
  limit?: number
}

export interface AdvancedResponse {
  rows: AdvancedRow[]
  branches: number
  results: Work[]
  total_candidates: number
  after_dedupe: number
  warnings: string[]
  exact_matches: number
}

export interface SearchRequest {
  query: string
  mode: DiscoveryMode
  year_from?: number | null
  year_to?: number | null
  limit?: number
  seed?: { doi?: string | null; title?: string | null; paper_id?: string | null } | null
  ai?: boolean
}

export interface DirectionGroup {
  id: string
  title: string
  hint: string
  items: Work[]
}

export interface DirectionResponse {
  topic: string
  plan: SearchPlan
  groups: DirectionGroup[]
  sources: SourceReport[]
  warnings: string[]
  ai: SearchResponse['ai']
}

export interface PersonalResponse {
  interests: string[]
  queries: Array<{ query: string; why: string }>
  results: Work[]
  sources: SourceReport[]
  warnings: string[]
  ai_used: boolean
}

export interface TopVenue {
  id: string
  /** 论文来源的实际名称（来自数据源） */
  name: string
  short: string
  kind: 'journal' | 'conference'
  category: string
  /** “已确认顶刊” / “已确认顶会” */
  label: string
}

export interface VenueInfo {
  id: string
  name: string
  short: string
  kind: 'journal' | 'conference'
  category: string
  fields: number[] | null
  /** top：顶刊顶会 | core：各领域的重要期刊/会议 */
  level?: 'top' | 'core'
  /** 名录里人工写的简介 */
  intro?: string | null
}

export interface RandomResponse {
  items: Work[]
  filter: {
    /** 没选学科（全部）时为 null */
    field: string | null
    field_zh: string | null
    /** 按研究画像里的方向探索时为该方向（此时不限定学科），否则为 null */
    topic?: string | null
    field_id: number | null
    /** 本次随机取到并确认过的白名单来源 */
    venues: Array<Pick<VenueInfo, 'id' | 'name' | 'short' | 'kind' | 'category'>>
    /** 这个学科范围内一共收录了多少个顶刊顶会 */
    venue_pool: number
    venue_policy: string
    /** 这些来源该年份区间的全部论文数 / 通过全部质量条件的论文数（OpenAlex 的真实计数，取不到时为 null） */
    pool_total: number | null
    pool_passed: number | null
    /** 从通过筛选的论文里随机抽出的样本数 */
    sampled: number
    /** 因来源无法确认而被丢弃的数量 */
    dropped_unconfirmed: number
    after_exclusions: number
    thresholds: { fwci_min: number | null; years: string; venue: string; type: string }
  }
  ai: boolean
}

export interface FieldInfo {
  id: number
  name: string
  name_zh: string
  venue_count?: number
}

export interface VenueList {
  count: number
  policy: string
  items: VenueInfo[]
}

export interface CodeCandidate {
  full_name: string
  url: string
  description: string | null
  stars: number | null
  language: string | null
  updated: string | null
  match: 'high' | 'mention' | 'candidate'
  evidence: string[]
}

export interface CodeResponse {
  candidates: CodeCandidate[]
  queries: string[]
  warnings: string[]
}

export interface SourceInfo {
  id: string
  name: string
  scope: string
  gives: string
  note: string
}

export interface DiscoverySettings {
  use_embeddings: boolean
  embedding_model: string
}

// ---------------- 比较 ----------------
export type CompareMode = 'quick' | 'method' | 'lineage'

export interface CompareReadiness {
  label: string
  paper_id: string
  title: string
  year: number | null
  has_fulltext: boolean
  has_abstract: boolean
  basis: 'fulltext' | 'abstract' | 'none'
}

export interface ComparePreflight {
  papers: CompareReadiness[]
  blockers: string[]
  warnings: string[]
  ok: boolean
}

export interface CompareEvidence {
  label: string
  paper_id: string
  paper_title: string
  ord: number | null
  page: number | null
  section_title: string | null
  quote: string
  bbox: number[] | null
  kind: 'paper_text' | 'abstract' | 'metadata'
}

export interface CompareCite {
  label: string
  ord: number | null
  page: number | null
}

export interface CompareEdge {
  from: string
  to: string
  relation: string
  relation_label: string
  basis: 'openalex' | 'text_reference' | 'ai'
  ai_inferred: boolean
  note: string
  evidence: CompareEvidence[]
}

export interface CompareResult {
  mode: CompareMode
  mode_label: string
  papers: Array<{ label: string; paper_id: string; title: string; year: number | null; basis: string }>
  overall: string
  dimensions: string[]
  rows: Array<{ dimension: string; cells: Record<string, { text: string; cites: CompareCite[] }> }>
  similarities: string[]
  differences: string[]
  detail: string
  graph: { nodes: Array<{ id: string; title: string; year: number | null }>; edges: CompareEdge[] }
  shared_references: Array<{ title: string; year: number | null; doi: string | null; venue: string | null; cited_by: string[]; citations: number | null }>
  evidence: CompareEvidence[]
  verification: { status: 'verified' | 'partial' | 'no_evidence' | 'unverified'; invalid_citations: string[]; notes: string[] }
  warnings: string[]
}

export interface ComparisonSummary {
  id: string
  title: string
  mode: CompareMode
  mode_label: string
  paper_ids: string[]
  status: 'pending' | 'running' | 'ready' | 'failed' | 'cancelled'
  job_id: string | null
  error: string | null
  created_at: string
  updated_at: string
}

export interface Comparison extends ComparisonSummary {
  result: CompareResult | null
}

// ---------------- 知识库 ----------------
export type ItemKind = 'method' | 'finding' | 'concept' | 'formula' | 'limitation' | 'other'
export type ConceptState = 'mastered' | 'learning' | 'unsure' | 'unknown'

export interface KbEvidence {
  kind: 'paper_text' | 'note'
  paper_id: string
  paper_title: string
  ord: number | null
  page: number | null
  section_title: string | null
  quote: string
  bbox: number[] | null
  note_id?: number
}

export interface KbTopic {
  id: string
  name: string
  description: string
  source: 'manual' | 'ai' | 'auto'
  status: 'active' | 'suggested'
  item_count: number
  paper_count: number
  created_at: string
  updated_at: string
}

export interface KbItem {
  id: string
  title: string
  content: string
  kind: ItemKind
  topic_ids: string[]
  source_paper: { id: string; title: string; year: number | null; doi: string | null } | null
  evidence: KbEvidence[]
  card_id: string | null
  note_id: string | null
  origin: 'candidate' | 'manual'
  created_at: string
  updated_at: string
  relations?: KbRelation[]
  cards?: Array<{ id: string; paper_id: string; question: string; object_label: string }>
  notes?: Array<{ id: number; paper_id: string; page: number | null; text: string }>
}

export interface KbCandidate {
  id: string
  paper_id: string
  paper_title: string | null
  kind: ItemKind
  title: string
  content: string
  evidence: KbEvidence[]
  origin_kind: 'analysis' | 'card' | 'note'
  origin_ref: string | null
  status: 'pending' | 'accepted' | 'rejected'
  created_at: string
}

export interface KbRelation {
  id: string
  from_kind: 'item' | 'paper'
  from_id: string
  to_kind: 'item' | 'paper'
  to_id: string
  relation: string
  relation_label: string
  description: string
  evidence: KbEvidence[]
  ai_inferred: boolean
  status: 'confirmed' | 'suggested'
}

export interface TopicSuggestion {
  id: string
  name: string
  description?: string
  status: string
  existing?: boolean
  papers: Array<{ id: string; title: string; year?: number | null }>
}

export interface ProfileMeta {
  interests: string[]
  explanation_depth: 'brief' | 'standard' | 'deep' | null
  use_in_prompts: boolean
}

export interface ProfileConcept {
  id: string
  name: string
  state: ConceptState
  state_label: string
  note: string
  origin: 'user' | 'suggestion'
  updated_at: string
}

export interface ProfileSuggestion {
  id: string
  name: string
  state: ConceptState
  state_label: string
  basis: string
  evidence: Array<{ kind: 'card'; card_id: string }>
}

/** 根据笔记、重点、收藏、提问和阅读深度推测的兴趣主题；只是推测，不是你确认的画像。 */
export interface InferredInterest {
  topic_id: string
  name: string
  level: 'interest' | 'watching' | 'read_only'
  level_label: string
  /** 0 到 1 */
  score: number
  papers: number
  engaged_papers: number
  basis: string
}

export interface Profile {
  meta: ProfileMeta
  concepts: ProfileConcept[]
  suggestions: ProfileSuggestion[]
  states: Record<ConceptState, string>
  prompt_hint: string | null
}

export interface GlobalNote {
  id: number
  paper_id: string
  paper_title: string
  paper_year: number | null
  page: number | null
  quote: string | null
  passage_ord: number | null
  text: string
  card_id: string | null
  created_at: string
  updated_at: string
}

export interface MarkdownExport {
  filename: string
  content: string
  count: number
}

export interface VenueIntro {
  name: string
  listed: { id: string; short: string; kind: 'journal' | 'conference'; category: string; label: string; intro: string | null } | null
  stats: {
    name: string | null
    type: string | null
    publisher: string | null
    country: string | null
    works_count: number | null
    cited_by_count: number | null
    mean_citedness_2y: number | null
    h_index: number | null
    is_oa: boolean | null
    in_doaj: boolean | null
    homepage: string | null
    issn: string | null
    openalex_id: string | null
  } | null
  note: string | null
  /** 只用统计数字写的中文说明（没有统计数据时没有） */
  summary?: string
}

/** 我文献库里的一个来源：论文数、年份范围；名录里有的带分类和档次。 */
export interface LibraryVenue {
  name: string
  count: number
  year_from: number | null
  year_to: number | null
  id: string | null
  label: string | null
  level: 'top' | 'core' | null
  kind: string | null
  category: string | null
  short: string | null
}

/** 一个期刊/会议的页面：介绍 + 这个来源上被引最多的和最新的论文。 */
export interface VenuePageData {
  name: string
  id: string | null
  kind: string | null
  category: string | null
  level: 'top' | 'core' | null
  label: string | null
  short: string | null
  info: VenueIntro
  errors: string[]
  works: { cited: Work[]; recent: Work[] }
}

/** OpenAlex 里检索到的一个期刊/会议。 */
export interface VenueSearchHit {
  id: string
  name: string
  abbreviation: string | null
  type: string | null
  publisher: string | null
  works_count: number | null
  cited_by_count: number | null
  h_index: number | null
  /** 名录里有的话是名录 id */
  listed_id: string | null
  label: string | null
}
