"use client";
import { useCallback, useEffect, useMemo, useState } from "react";
import { StatusBadge, TopBar } from "@/components/TopBar";
import { api, brl, fmtDur, fmtSize, type BudgetState, type Estimate } from "@/lib/api";
import { supabase, type Job, type Project, type Render } from "@/lib/supabase";
import { useSession } from "@/lib/useSession";

const STYLES = [
  { key: "auto", name: "Automático", desc: "A IA escolhe pelo ritmo e conteúdo" },
  { key: "corporativo", name: "Corporativo", desc: "Elegante, cortes naturais, legenda em caixa" },
  { key: "dinamico", name: "Dinâmico", desc: "Ritmo rápido, zooms e legendas animadas" },
  { key: "cinematografico", name: "Cinematográfico", desc: "Tratamento de cor, transições e respiro" },
];

type Opts = {
  style: string;
  full: boolean;
  full_aspect: string;
  shorts: number;
  shorts_aspect: string;
  shorts_style: string;
  hook_open: boolean;
  captions: boolean;
  remove_disfluencies: boolean;
  use_llm: boolean;
  reanalyze: boolean;
};

const DEFAULTS: Opts = {
  style: "auto", full: true, full_aspect: "16:9", shorts: 3, shorts_aspect: "9:16", shorts_style: "dinamico",
  hook_open: false, captions: true, remove_disfluencies: true, use_llm: true, reanalyze: false,
};

export default function ProjectPage() {
  const { session } = useSession();
  const [id, setId] = useState<string | null>(null);
  const [project, setProject] = useState<Project | null>(null);
  const [jobs, setJobs] = useState<Job[]>([]);
  const [renders, setRenders] = useState<Render[]>([]);
  const [err, setErr] = useState<string | null>(null);

  useEffect(() => setId(new URLSearchParams(window.location.search).get("id")), []);

  const load = useCallback(async () => {
    if (!id) return;
    const sb = supabase();
    const [p, j, r] = await Promise.all([
      sb.from("projects").select("*").eq("id", id).single(),
      sb.from("jobs").select("*").eq("project_id", id).order("created_at", { ascending: false }),
      sb.from("renders").select("*").eq("project_id", id).order("created_at", { ascending: false }),
    ]);
    if (p.error) setErr("Projeto não encontrado.");
    setProject(p.data as Project);
    setJobs((j.data as Job[]) || []);
    setRenders((r.data as Render[]) || []);
  }, [id]);

  useEffect(() => {
    if (!session || !id) return;
    load();
    const ch = supabase()
      .channel(`project-${id}`)
      .on("postgres_changes", { event: "*", schema: "public", table: "jobs", filter: `project_id=eq.${id}` }, load)
      .on("postgres_changes", { event: "INSERT", schema: "public", table: "renders", filter: `project_id=eq.${id}` }, load)
      .subscribe();
    return () => {
      supabase().removeChannel(ch);
    };
  }, [session, id, load]);

  const active = jobs.find((j) => j.status === "queued" || j.status === "running");
  useEffect(() => {
    if (!active) return;
    const t = setInterval(load, 4000); // fallback if realtime is unavailable
    return () => clearInterval(t);
  }, [active, load]);

  const latestDone = jobs.find((j) => j.status === "done");
  const latestRenders = useMemo(() => renders.filter((r) => r.job_id === latestDone?.id), [renders, latestDone]);

  if (!session) return null;
  return (
    <main className="shell">
      <TopBar active="studio" />
      {err && <div className="alert err">{err}</div>}
      {project && (
        <>
          <div className="spread" style={{ marginBottom: 18 }}>
            <div>
              <h1>{project.title}</h1>
              <span className="muted small">
                {project.source_name} · {fmtDur(project.duration ?? project.declared_duration)} · {fmtSize(project.source_size)}
              </span>
            </div>
            <StatusBadge status={project.status} />
          </div>

          {project.status === "awaiting_upload" && (
            <div className="alert warn">O envio deste vídeo não foi concluído. Volte aos projetos e envie novamente.</div>
          )}

          {active && <ActiveJob job={active} />}

          <div className="grid grid-2" style={{ alignItems: "start", marginTop: 16 }}>
            {["uploaded", "ready", "failed"].includes(project.status) && !active && (
              <EditForm projectId={project.id} hasPrevious={!!latestDone} onQueued={load} />
            )}
            {latestDone?.analysis && <AnalysisPanel job={latestDone} />}
          </div>

          {latestRenders.length > 0 && (
            <section style={{ marginTop: 24 }}>
              <h2>Resultados</h2>
              {latestDone && latestDone.warnings?.length > 0 && (
                <div className="alert warn" style={{ marginBottom: 12 }}>{latestDone.warnings.join(" · ")}</div>
              )}
              <div className="grid grid-3">
                {latestRenders.map((r) => (
                  <RenderCard key={r.id} r={r} />
                ))}
              </div>
            </section>
          )}

          {latestDone && !active && <ReviseForm job={latestDone} onQueued={load} />}

          {jobs.length > 0 && (
            <section className="card" style={{ marginTop: 24 }}>
              <h2>Histórico</h2>
              <table>
                <thead>
                  <tr><th>Quando</th><th>Tipo</th><th>Estilo</th><th>Status</th><th style={{ textAlign: "right" }}>Custo (US$)</th></tr>
                </thead>
                <tbody>
                  {jobs.map((j) => (
                    <tr key={j.id}>
                      <td className="small">{new Date(j.created_at).toLocaleString("pt-BR")}</td>
                      <td className="small">{j.kind === "revise" ? "correção" : "edição"}</td>
                      <td className="small">{j.style}</td>
                      <td><StatusBadge status={j.status} />{j.error && <div className="small" style={{ color: "var(--err)" }}>{j.error}</div>}</td>
                      <td className="small" style={{ textAlign: "right" }}>{Number(j.cost_usd ?? 0).toFixed(3)} / est. {Number(j.estimate_usd ?? 0).toFixed(3)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </section>
          )}
        </>
      )}
    </main>
  );
}

function ActiveJob({ job }: { job: Job }) {
  const pct = Math.round((job.progress || 0) * 100);
  return (
    <section className="card stack">
      <div className="spread">
        <h3>{job.kind === "revise" ? "Aplicando correções" : "Editando seu vídeo"}</h3>
        <span className="badge run">{pct}%</span>
      </div>
      <div className="progress"><div style={{ width: `${pct}%` }} /></div>
      <span className="muted small">{job.stage || "na fila"} · processamento em nuvem — pode fechar esta página</span>
    </section>
  );
}

function EditForm({ projectId, hasPrevious, onQueued }: { projectId: string; hasPrevious: boolean; onQueued: () => void }) {
  const [o, setO] = useState<Opts>(DEFAULTS);
  const [est, setEst] = useState<{ estimate: Estimate; budget: BudgetState; allowed: boolean } | null>(null);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const set = <K extends keyof Opts>(k: K, v: Opts[K]) => setO((x) => ({ ...x, [k]: v }));

  useEffect(() => {
    const t = setTimeout(() => {
      api<typeof est>(`/api/projects/${projectId}/estimate`, { body: o }).then(setEst).catch((e) => setErr(e.message));
    }, 300);
    return () => clearTimeout(t);
  }, [o, projectId]);

  async function start() {
    setBusy(true);
    setErr(null);
    try {
      await api(`/api/projects/${projectId}/jobs`, { body: o });
      onQueued();
    } catch (e) {
      setErr(e instanceof Error ? e.message : "Falha ao iniciar");
    } finally {
      setBusy(false);
    }
  }

  return (
    <section className="card stack">
      <h2>{hasPrevious ? "Nova edição" : "Editar com IA"}</h2>
      <div className="styles">
        {STYLES.map((s) => (
          <div key={s.key} className={`style-opt ${o.style === s.key ? "sel" : ""}`} onClick={() => set("style", s.key)}>
            <b>{s.name}</b>
            <span className="muted small">{s.desc}</span>
          </div>
        ))}
      </div>
      <div className="grid grid-2" style={{ gap: 12 }}>
        <div>
          <label className="toggle"><input type="checkbox" checked={o.full} onChange={(e) => set("full", e.target.checked)} /> Versão completa</label>
          <select value={o.full_aspect} disabled={!o.full} onChange={(e) => set("full_aspect", e.target.value)}>
            <option value="16:9">Horizontal 16:9</option>
            <option value="9:16">Vertical 9:16</option>
            <option value="1:1">Quadrado 1:1</option>
          </select>
        </div>
        <div>
          <label htmlFor="shorts">Cortes curtos (Reels/TikTok/Shorts)</label>
          <select id="shorts" value={o.shorts} onChange={(e) => set("shorts", Number(e.target.value))}>
            {[0, 1, 2, 3, 4, 5].map((n) => <option key={n} value={n}>{n === 0 ? "nenhum" : `${n} vídeo${n > 1 ? "s" : ""}`}</option>)}
          </select>
        </div>
      </div>
      <div className="stack" style={{ gap: 6 }}>
        <label className="toggle"><input type="checkbox" checked={o.hook_open} onChange={(e) => set("hook_open", e.target.checked)} /> Abrir com o melhor gancho (cold open)</label>
        <label className="toggle"><input type="checkbox" checked={o.captions} onChange={(e) => set("captions", e.target.checked)} /> Legendas dinâmicas</label>
        <label className="toggle"><input type="checkbox" checked={o.remove_disfluencies} onChange={(e) => set("remove_disfluencies", e.target.checked)} /> Remover gaguejos, repetições e hesitações</label>
        <label className="toggle"><input type="checkbox" checked={o.use_llm} onChange={(e) => set("use_llm", e.target.checked)} /> Análise editorial por IA (recomendado)</label>
        {hasPrevious && (
          <label className="toggle"><input type="checkbox" checked={o.reanalyze} onChange={(e) => set("reanalyze", e.target.checked)} /> Refazer análise editorial (por padrão reaproveita a anterior)</label>
        )}
      </div>
      {est && (
        <div className={`alert ${est.allowed ? "ok" : "err"}`}>
          Custo estimado: <b>{brl(est.estimate.total_brl)}</b> · gasto no mês {brl(est.budget.spent_brl)} de {brl(est.budget.cap_brl)}
          {!est.allowed && " — excede o orçamento mensal"}
        </div>
      )}
      {err && <div className="alert err">{err}</div>}
      <button className="btn primary" disabled={busy || (est ? !est.allowed : false)} onClick={start}>
        {busy ? "Enviando para a nuvem…" : "Iniciar edição"}
      </button>
    </section>
  );
}

function AnalysisPanel({ job }: { job: Job }) {
  const a = job.analysis!;
  return (
    <section className="card stack">
      <div className="spread">
        <h2>Análise editorial</h2>
        <span className="badge">{a.source === "llm" ? "IA" : "heurística"}</span>
      </div>
      {a.title && <h3>{a.title}</h3>}
      {a.summary && <p className="muted">{a.summary}</p>}
      {a.topics?.length > 0 && (
        <div><b className="small">Tópicos</b><ul className="list small">{a.topics.map((t, i) => <li key={i}>{t.title}</li>)}</ul></div>
      )}
      {a.hooks?.length > 0 && (
        <div><b className="small">Ganchos sugeridos</b><ul className="list small">{a.hooks.map((h, i) => <li key={i}>“{h.quote}” <span className="muted">— {h.why}</span></li>)}</ul></div>
      )}
      {a.removals?.length > 0 && (
        <details>
          <summary className="small">Cortes editoriais ({a.removals.length})</summary>
          <ul className="list small">{a.removals.map((r, i) => <li key={i}><span className="badge">{r.reason}</span> “{r.quote}”</li>)}</ul>
        </details>
      )}
      {a.hashtags?.length > 0 && <p className="small muted">{a.hashtags.join(" ")}</p>}
    </section>
  );
}

function RenderCard({ r }: { r: Render }) {
  const [url, setUrl] = useState<string | null>(null);
  const [err, setErr] = useState<string | null>(null);

  useEffect(() => {
    api<{ url: string }>(`/api/renders/${r.id}/url`).then((x) => setUrl(x.url)).catch((e) => setErr(e.message));
  }, [r.id]);

  async function download(captions = false) {
    const x = await api<{ url: string }>(`/api/renders/${r.id}/url?download=1${captions ? "&captions=1" : ""}`);
    window.location.href = x.url;
  }

  const vertical = r.height > r.width;
  return (
    <div className="card stack">
      {url ? (
        <video className={`video ${vertical ? "vertical" : ""}`} src={url} controls preload="metadata" playsInline />
      ) : (
        <div className="video" style={{ aspectRatio: `${r.width}/${r.height}`, maxHeight: 520 }} />
      )}
      {err && <div className="alert err small">{err}</div>}
      <div className="spread">
        <div>
          <b>{r.kind === "full" ? "Versão completa" : r.title}</b>
          <div className="muted small">{r.aspect} · {fmtDur(r.duration)} · {r.width}×{r.height} · {fmtSize(r.size_bytes)}</div>
        </div>
        <span className={`badge ${r.qc?.ok ? "ok" : "err"}`} title={r.qc?.checks.map((c) => `${c.ok ? "✓" : "✗"} ${c.name}: ${c.detail}`).join("\n")}>
          QC {r.qc?.ok ? "ok" : "falhou"}
        </span>
      </div>
      <div className="row">
        <button className="btn primary" onClick={() => download(false)}>Baixar MP4</button>
        {r.captions_key && <button className="btn" onClick={() => download(true)}>Legenda .srt</button>}
      </div>
    </div>
  );
}

function ReviseForm({ job, onQueued }: { job: Job; onQueued: () => void }) {
  const [text, setText] = useState("");
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  async function send() {
    setBusy(true);
    setErr(null);
    try {
      await api(`/api/jobs/${job.id}/revise`, { body: { instructions: text } });
      setText("");
      onQueued();
    } catch (e) {
      setErr(e instanceof Error ? e.message : "Falha ao enviar correção");
    } finally {
      setBusy(false);
    }
  }

  return (
    <section className="card stack" style={{ marginTop: 24 }}>
      <h2>Solicitar correções</h2>
      <p className="muted small">Descreva o que mudar. Ex.: “mantenha a piada do início”, “corte o trecho sobre preços”, “o short 2 deve terminar na conclusão”.</p>
      <textarea rows={3} value={text} onChange={(e) => setText(e.target.value)} placeholder="O que você gostaria de ajustar?" />
      {err && <div className="alert err">{err}</div>}
      <div><button className="btn primary" disabled={busy || text.trim().length < 3} onClick={send}>{busy ? "Enviando…" : "Aplicar correções"}</button></div>
    </section>
  );
}
