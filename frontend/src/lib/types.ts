export interface User {
  id: string;
  email: string;
  display_name: string | null;
  onboarding_complete: boolean;
  digest_frequency: string;
  digest_time_morning: string;
  send_time_locked: boolean;
  digest_max_items: number;
  digest_length_locked: boolean;
  serendipity_percentage: number;
  preferred_languages: string[];
  tier: string;
  timezone: string;
  created_at: string;
  /** False only while this instance requires email confirmation (#175). */
  email_verified?: boolean;
  /** Hosted fetch budget exhausted until then (#169). */
  fetch_paused_until?: string | null;
  /** Keep my reading out of other readers' collaborative warmup counts (#210). */
  collaborative_opt_out?: boolean;
  /** Weekly 'What I learned about you' email (#184), opt-in. */
  weekly_email?: boolean;
  /** Seeded demo account persona (#229). */
  is_demo?: boolean;
}

export interface Source {
  id: string;
  user_id: string;
  url: string;
  name: string | null;
  feed_url: string | null;
  source_type: string;
  trust_weight: number;
  is_active: boolean;
  last_fetched_at: string | null;
  last_error_at: string | null;
  last_error: string | null;
  fetch_error_count: number;
  health: "healthy" | "degraded" | "failing" | "platform_outage" | "dead";
  /** Newsletter senders (#249). */
  newsletter_delivery?: "direct" | "forwarded" | null;
  unsubscribe_options?: { url: string | null; mailto: string | null } | null;
  items_per_week: number;
  topics: string[];
  priority: string;
  created_at: string;
}

export interface CreatorPlatform {
  id: string;
  creator_id: string;
  platform: string;
  platform_url: string;
  feed_url: string | null;
  is_verified: boolean;
  last_fetched_at: string | null;
  tracking_tier: string | null;
  display_label: string | null;
}

/** Platform → { tracking_tier, display_label } from the backend capabilities map. */
export type PlatformCapabilities = Record<
  string,
  { tracking_tier: string; display_label: string }
>;

export interface Creator {
  id: string;
  user_id: string;
  display_name: string;
  resolved: boolean;
  priority: string;
  trust_weight: number;
  platforms: CreatorPlatform[];
  created_at: string;
}

export interface ContentItem {
  id: string;
  source_id: string | null;
  creator_platform_id: string | null;
  url: string;
  title: string;
  author: string | null;
  published_at: string | null;
  fetched_at: string;
  summary_headline: string | null;
  summary_brief: string | null;
  summary_detailed: string | null;
  reading_time_minutes: number | null;
  content_depth_score: number | null;
  word_count: number | null;
  has_citations: boolean;
  is_original_reporting: boolean | null;
  topic_clusters: string[];
  summarization_cached: boolean;
  /** "llm" summaries are labelled as AI-written (#177). */
  summary_source?: string | null;
  /** The text and summary come from the episode's published transcript (#252). */
  transcript_url?: string | null;
  paywalled?: boolean;
  page_type?: string | null;
  language?: string | null;
  created_at: string;
}

/** Full content item including the extracted body — only from the single-item endpoint. */
export interface ContentItemFull extends ContentItem {
  full_text: string | null;
  /** Text exists but the hosted display policy withholds it (#177). */
  full_text_withheld?: boolean;
}

export interface DigestItem {
  id: string;
  digest_id: string;
  content_item_id: string;
  position: number;
  section: string;
  prs_score: number;
  /** Features, contributions, no_data, explanation… as stored by the ranker. */
  signal_breakdown: Record<string, unknown>;
  content: ContentItem | null;
}

export interface Digest {
  id: string;
  user_id: string;
  generated_at: string;
  delivered_at: string | null;
  delivery_method: string;
  section_counts: Record<string, number>;
  opened: boolean;
  total_items: number;
  items: DigestItem[];
  created_at: string;
  /** Ask "how many of the top 5 did you want?" (#131). */
  ask_top5?: boolean;
}

/** First-party product metrics for the instance owner (#131). */
export interface ProductDashboard {
  enabled: boolean;
  now: {
    weekly_satisfied_readers: number;
    ttfgd_minutes_median: number | null;
    interleaving_win_rate: number | null;
    retention_d7: number | null;
    retention_d30: number | null;
    top5_wanted_median: number | null;
    feed_health: number | null;
  };
  targets: Record<string, string>;
  events_last_30_days: Record<string, number>;
  series: Record<string, { day: string; value: number | null }[]>;
}

export interface FeedItem {
  content: ContentItem;
  prs_score: number | null;
  /** Features, contributions, no_data, explanation… as stored by the ranker. */
  signal_breakdown: Record<string, unknown>;
}

export interface InterestGraphNode {
  label: string;
  weight: number;
  is_core: boolean;
  last_reinforced_at?: string | null;
  reinforcement_count?: number;
  suppressed_until?: string | null;
}

export interface InterestGraphEdge {
  from_label: string;
  to_label: string;
  weight: number;
}

export interface InterestGraph {
  nodes: InterestGraphNode[];
  edges: InterestGraphEdge[];
}

export interface Token {
  /** Empty in cookie mode (#103): credentials are httpOnly cookies. */
  access_token: string;
  refresh_token?: string | null;
  token_type: string;
  expires_in: number;
  csrf_token?: string | null;
}

export interface BillingState {
  plan: "selfhost" | "trial" | "reader" | "pro" | "team" | "paused";
  /** Paused (trial over, no plan): when the account will be deleted (#341). */
  deletes_at?: string | null;
  billing_enabled: boolean;
  /** Academic discount applies at checkout (verified academic email, #250). */
  academic_discount?: boolean;
  status: string | null;
  trial_ends_at: string | null;
  trial_days_left: number | null;
  plan_expires_at: string | null;
  can_manage: boolean;
  /** Hosted customer portal URL; null when provider has no hosted portal (e.g. Razorpay, #342). */
  portal_url?: string | null;
  /** "email" when the provider has no self-serve portal (Razorpay, #342). */
  manage_via?: "portal" | "email";
  support_email?: string | null;
  /** Hosted plan quotas and today's use (#172); empty on self-host. 0 = unlimited. */
  quotas?: PlanQuota[];
  /** Plans on sale, cheapest first (#172); empty when billing is off. */
  plans?: PlanOffer[];
}

export interface PlanOffer {
  plan: "reader" | "pro";
  name: string;
  intervals: ("month" | "year")[];
  /** Limits by quota name; 0 = unlimited. */
  quotas: Record<PlanQuota["quota"], number>;
  /** Pro-only features this plan includes (#246); empty for Reader. */
  features?: string[];
}

export interface PlanQuota {
  quota:
    | "sources"
    | "creators"
    | "newsletters_per_day"
    | "newsletter_mb_per_day"
    | "llm_summaries_per_day"
    | "digest_builds_per_day";
  limit: number;
  used: number;
  /** Resets at midnight UTC. */
  daily: boolean;
}

/** A curated starter pack of feeds (#152). */
export interface StarterPack {
  id: string;
  title: string;
  /** One line on what the pack covers. */
  description?: string;
  feed_count: number;
  feeds: { name: string; url: string }[];
  /** Matches the user's confirmed topics; pre-selected in the picker. */
  suggested: boolean;
  /** How many of the pack's feeds the user already follows. */
  subscribed: number;
}

/** Result of a feed or saved-article import (#150, #202). */
export interface ImportResult {
  kind?: string;
  created: number;
  skipped?: number;
  already_had?: number;
  queued?: boolean;
  job_id?: string;
  errors: { row?: number; url: string; error: string }[];
}

/** Live score and explanation for one item (#267). */
export interface WhyNotSeen {
  content_id: string;
  title: string;
  score: number;
  explanation: string;
  why_topics: string | null;
  verdict: "shown" | "not_an_article" | "not_followed" | "ranked_below_cutoff" | "no_digest_yet" | "outside_window";
  message: string;
  latest_digest_cutoff: number | null;
  contributions: { signal: string; feature: number; weight: number; contribution: number; has_data: boolean }[];
  raise_it: { signal: string; how: string }[];
}

/** Your own LLM key on hosted (#255). The key itself is never returned. */
export interface LLMKeyState {
  configured: boolean;
  base_url: string | null;
  model: string | null;
  last4: string | null;
  allowed_base_urls?: string[];
}
