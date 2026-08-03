import { useEffect, useMemo, useState, useTransition } from "react";
import { api, MetricsSummary, ReviewItem } from "./api";

function pct(n: number) {
  return `${Math.round(n * 100)}%`;
}

function ScoreBar({ label, value }: { label: string; value: number }) {
  return (
    <div className="score">
      <span>{label}</span>
      <div className="bar">
        <i style={{ width: `${Math.min(100, Math.max(0, value * 100))}%` }} />
      </div>
      <strong>{value.toFixed(2)}</strong>
    </div>
  );
}

export default function App() {
  const [reviewer, setReviewer] = useState("alex.reviewer");
  const [items, setItems] = useState<ReviewItem[]>([]);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [metrics, setMetrics] = useState<MetricsSummary | null>(null);
  const [notes, setNotes] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [pending, startTransition] = useTransition();

  const selected = useMemo(
    () => items.find((i) => i.id === selectedId) || items[0] || null,
    [items, selectedId]
  );

  async function refresh() {
    const [reviews, summary] = await Promise.all([
      api.listReviews("pending"),
      api.metrics(),
    ]);
    // also pull claimed by this reviewer so they can finish
    let claimed: ReviewItem[] = [];
    try {
      claimed = (await api.listReviews("claimed")).filter(
        (r) => r.claimed_by === reviewer
      );
    } catch {
      claimed = [];
    }
    const merged = [...claimed, ...reviews];
    const dedup = new Map(merged.map((r) => [r.id, r]));
    const list = Array.from(dedup.values());
    setItems(list);
    setMetrics(summary);
    if (list.length && !list.some((r) => r.id === selectedId)) {
      setSelectedId(list[0].id);
    }
    if (!list.length) setSelectedId(null);
  }

  useEffect(() => {
    refresh().catch((e: Error) => setError(e.message));
    const t = setInterval(() => {
      refresh().catch(() => undefined);
    }, 4000);
    return () => clearInterval(t);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [reviewer]);

  function run(action: () => Promise<void>) {
    setError(null);
    startTransition(() => {
      void action().catch((e: Error) => setError(e.message));
    });
  }

  return (
    <div className="app">
      <header className="topbar">
        <div className="brand">
          <h1>Sentinel Desk</h1>
          <span>Human review for multimodal moderation</span>
        </div>
        <div className="reviewer">
          <label htmlFor="reviewer">Reviewer</label>
          <input
            id="reviewer"
            value={reviewer}
            onChange={(e) => setReviewer(e.target.value)}
          />
        </div>
      </header>

      <div className="shell">
        <aside className="queue">
          <div className="queue-head">
            <h2>Review queue</h2>
            <small>{items.length} open</small>
          </div>

          {metrics && (
            <div className="metrics-strip">
              <div className="metric">
                <strong>{metrics.pending_reviews}</strong>
                <span>Pending</span>
              </div>
              <div className="metric">
                <strong>{pct(metrics.auto_resolve_rate)}</strong>
                <span>Auto-resolved</span>
              </div>
              <div className="metric">
                <strong>{metrics.decisions_last_minute}</strong>
                <span>Decisions/min</span>
              </div>
              <div className="metric">
                <strong>
                  {metrics.p95_latency_ms != null
                    ? Math.round(metrics.p95_latency_ms)
                    : "—"}
                </strong>
                <span>p95 ms</span>
              </div>
            </div>
          )}

          <div className="queue-list">
            {items.map((item) => (
              <button
                key={item.id}
                className={`queue-item ${selected?.id === item.id ? "active" : ""}`}
                onClick={() => {
                  setSelectedId(item.id);
                  setNotes("");
                }}
              >
                <div className="meta">
                  <span className={`badge ${item.decision}`}>{item.decision}</span>
                  <span className="badge neutral">{pct(item.confidence)}</span>
                </div>
                <p>{item.caption || "(no caption)"}</p>
              </button>
            ))}
            {!items.length && (
              <p style={{ color: "var(--muted)", fontSize: "0.9rem" }}>
                Queue clear. New FLAG / low-confidence items land here.
              </p>
            )}
          </div>
        </aside>

        <main className="desk">
          {!selected ? (
            <div className="empty">
              <div>
                <h2>Nothing to triage</h2>
                <p>Run <code>./scripts/demo.sh</code> to enqueue sample content.</p>
              </div>
            </div>
          ) : (
            <div className="triage">
              <section className="stage">
                <img src={selected.image_url} alt="Content under review" />
                <div className="stage-caption">
                  <strong>Caption</strong>
                  <p>{selected.caption || "—"}</p>
                </div>
              </section>

              <section className="panel">
                <h3>Model decision</h3>
                <div className="badge-row">
                  <span className={`badge ${selected.decision}`}>{selected.decision}</span>
                  <span className="badge neutral">conf {selected.confidence.toFixed(2)}</span>
                  <span className="badge neutral">{selected.status}</span>
                </div>

                <div className="scores">
                  <ScoreBar
                    label="NSFW"
                    value={selected.vision_signals.nsfw_score ?? 0}
                  />
                  <ScoreBar
                    label="Violence"
                    value={selected.vision_signals.violence_score ?? 0}
                  />
                  <ScoreBar
                    label="LLM"
                    value={selected.llm_signals.score ?? selected.confidence}
                  />
                </div>

                <ol className="reasons">
                  {selected.reasons.slice(0, 8).map((r) => (
                    <li key={r}>{r}</li>
                  ))}
                </ol>

                <textarea
                  className="notes"
                  placeholder="Reviewer notes (optional for ALLOW, recommended for BLOCK)"
                  value={notes}
                  onChange={(e) => setNotes(e.target.value)}
                />

                <div className="actions">
                  <button
                    className="btn-claim"
                    disabled={pending || selected.status === "claimed"}
                    onClick={() =>
                      run(async () => {
                        const updated = await api.claim(selected.id, reviewer);
                        setItems((prev) =>
                          prev.map((p) => (p.id === updated.id ? updated : p))
                        );
                      })
                    }
                  >
                    Claim
                  </button>
                  <button
                    className="btn-allow"
                    disabled={pending || selected.status !== "claimed"}
                    onClick={() =>
                      run(async () => {
                        await api.resolve(selected.id, reviewer, "ALLOW", notes);
                        setNotes("");
                        await refresh();
                      })
                    }
                  >
                    Approve
                  </button>
                  <button
                    className="btn-block"
                    disabled={pending || selected.status !== "claimed"}
                    onClick={() =>
                      run(async () => {
                        await api.resolve(selected.id, reviewer, "BLOCK", notes);
                        setNotes("");
                        await refresh();
                      })
                    }
                  >
                    Reject
                  </button>
                </div>

                {error && <div className="error">{error}</div>}

                <div className="signals">
                  <div>
                    Vision: {selected.vision_signals.provider}/
                    {selected.vision_signals.model_version}
                  </div>
                  <div>
                    LLM: {selected.llm_signals.provider}/
                    {selected.llm_signals.model_version}
                  </div>
                  {selected.vision_signals.ocr_text ? (
                    <div>
                      OCR: <code>{selected.vision_signals.ocr_text}</code>
                    </div>
                  ) : null}
                </div>
              </section>
            </div>
          )}
        </main>
      </div>
    </div>
  );
}
