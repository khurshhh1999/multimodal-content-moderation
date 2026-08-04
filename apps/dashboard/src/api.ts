const API_BASE = import.meta.env.VITE_API_BASE_URL || "http://localhost:8000";

export type ReviewItem = {
  id: string;
  decision_id: string;
  content_id: string;
  job_id: string;
  status: string;
  priority: number;
  claimed_by: string | null;
  claimed_at: string | null;
  claim_expires_at?: string | null;
  created_at: string;
  decision: "ALLOW" | "FLAG" | "BLOCK";
  confidence: number;
  reasons: string[];
  vision_signals: {
    labels?: string[];
    nsfw_score?: number;
    violence_score?: number;
    ocr_text?: string;
    provider?: string;
    model_version?: string;
  };
  llm_signals: {
    label?: string;
    score?: number;
    rationale?: string;
    provider?: string;
    model_version?: string;
  };
  caption: string;
  image_url: string;
  content_type: string;
};

export type MetricsSummary = {
  queue_depth: number;
  pending_reviews: number;
  claimed_reviews: number;
  decisions_total: number;
  decisions_allow: number;
  decisions_flag: number;
  decisions_block: number;
  auto_resolved: number;
  auto_resolve_rate: number;
  p95_latency_ms: number | null;
  decisions_last_minute: number;
};

export type AuditEvent = {
  id: string;
  entity_type: string;
  entity_id: string;
  action: string;
  actor: string;
  detail: Record<string, unknown>;
  created_at: string;
};

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(`${API_BASE}${path}`, {
    ...init,
    headers: {
      "Content-Type": "application/json",
      ...(init?.headers || {}),
    },
  });
  if (!res.ok) {
    const text = await res.text();
    throw new Error(text || res.statusText);
  }
  return res.json() as Promise<T>;
}

export const api = {
  listReviews: (status = "pending") =>
    request<ReviewItem[]>(`/v1/reviews?status=${encodeURIComponent(status)}`),
  claim: (id: string, reviewer: string) =>
    request<ReviewItem>(`/v1/reviews/${id}/claim`, {
      method: "POST",
      body: JSON.stringify({ reviewer }),
    }),
  resolve: (id: string, reviewer: string, reviewer_decision: "ALLOW" | "BLOCK", notes: string) =>
    request<ReviewItem>(`/v1/reviews/${id}/resolve`, {
      method: "POST",
      body: JSON.stringify({ reviewer, reviewer_decision, notes }),
    }),
  metrics: () => request<MetricsSummary>("/v1/metrics/summary"),
  audit: (params?: { entity_type?: string; entity_id?: string; actor?: string }) => {
    const q = new URLSearchParams();
    if (params?.entity_type) q.set("entity_type", params.entity_type);
    if (params?.entity_id) q.set("entity_id", params.entity_id);
    if (params?.actor) q.set("actor", params.actor);
    const suffix = q.toString() ? `?${q}` : "";
    return request<AuditEvent[]>(`/v1/audit${suffix}`);
  },
};
