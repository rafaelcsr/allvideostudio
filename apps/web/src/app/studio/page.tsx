"use client";
import { useCallback, useEffect, useRef, useState } from "react";
import { StatusBadge, TopBar } from "@/components/TopBar";
import { api, fmtDur, fmtSize, putWithProgress, videoDuration } from "@/lib/api";
import { supabase, type Project } from "@/lib/supabase";
import { useSession } from "@/lib/useSession";

export default function Studio() {
  const { session } = useSession();
  const [projects, setProjects] = useState<Project[]>([]);
  const [loading, setLoading] = useState(true);

  const load = useCallback(async () => {
    const { data, error } = await supabase()
      .from("projects")
      .select("*")
      .neq("status", "archived")
      .order("created_at", { ascending: false })
      .limit(100);
    if (!error) setProjects((data as Project[]) || []);
    setLoading(false);
  }, []);

  useEffect(() => {
    if (session) load();
  }, [session, load]);

  if (!session) return null;
  return (
    <main className="shell">
      <TopBar active="studio" />
      <div className="grid grid-2" style={{ alignItems: "start" }}>
        <Uploader />
        <section className="card">
          <div className="spread" style={{ marginBottom: 8 }}>
            <h2>Projetos</h2>
            <button className="btn ghost small" onClick={load}>Atualizar</button>
          </div>
          {loading && <p className="muted">Carregando…</p>}
          {!loading && projects.length === 0 && <p className="muted">Nenhum projeto ainda. Envie seu primeiro vídeo.</p>}
          <ul className="list">
            {projects.map((p) => (
              <li key={p.id}>
                <a href={`/project/?id=${p.id}`} className="spread">
                  <span>
                    <b>{p.title}</b>
                    <br />
                    <span className="muted small">
                      {new Date(p.created_at).toLocaleString("pt-BR")} · {fmtDur(p.duration ?? p.declared_duration)} · {fmtSize(p.source_size)}
                    </span>
                  </span>
                  <StatusBadge status={p.status} />
                </a>
              </li>
            ))}
          </ul>
        </section>
      </div>
    </main>
  );
}

function Uploader() {
  const input = useRef<HTMLInputElement>(null);
  const [file, setFile] = useState<File | null>(null);
  const [title, setTitle] = useState("");
  const [over, setOver] = useState(false);
  const [pct, setPct] = useState<number | null>(null);
  const [err, setErr] = useState<string | null>(null);

  function pick(f: File | undefined) {
    if (!f) return;
    if (!f.type.startsWith("video/")) {
      setErr("Selecione um arquivo de vídeo.");
      return;
    }
    setErr(null);
    setFile(f);
    setTitle(f.name.replace(/\.[^.]+$/, ""));
  }

  async function send() {
    if (!file) return;
    setErr(null);
    setPct(0);
    try {
      const duration = await videoDuration(file).catch(() => 60);
      const { project, upload_url } = await api<{ project: Project; upload_url: string }>("/api/projects", {
        body: { title: title || file.name, filename: file.name, size: file.size, content_type: file.type || "video/mp4", duration },
      });
      await putWithProgress(upload_url, file, file.type || "video/mp4", setPct);
      await api(`/api/projects/${project.id}/uploaded`, { method: "POST" });
      window.location.href = `/project/?id=${project.id}`;
    } catch (e) {
      setErr(e instanceof Error ? e.message : "Falha no envio");
      setPct(null);
    }
  }

  return (
    <section className="card stack">
      <h2>Novo vídeo</h2>
      <div
        className={`drop ${over ? "over" : ""}`}
        onClick={() => input.current?.click()}
        onDragOver={(e) => {
          e.preventDefault();
          setOver(true);
        }}
        onDragLeave={() => setOver(false)}
        onDrop={(e) => {
          e.preventDefault();
          setOver(false);
          pick(e.dataTransfer.files[0]);
        }}
      >
        <input ref={input} type="file" accept="video/*" hidden onChange={(e) => pick(e.target.files?.[0])} />
        {file ? (
          <>
            <b>{file.name}</b>
            <p className="muted small">{fmtSize(file.size)}</p>
          </>
        ) : (
          <>
            <b>Arraste o vídeo aqui</b>
            <p className="muted small">ou clique para escolher · MP4, MOV, MKV, WebM</p>
          </>
        )}
      </div>
      {file && (
        <div>
          <label htmlFor="title">Título do projeto</label>
          <input id="title" value={title} onChange={(e) => setTitle(e.target.value)} />
        </div>
      )}
      {pct !== null && (
        <div className="stack" style={{ gap: 6 }}>
          <div className="progress"><div style={{ width: `${Math.round(pct * 100)}%` }} /></div>
          <span className="muted small">Enviando direto para o armazenamento em nuvem… {Math.round(pct * 100)}%</span>
        </div>
      )}
      {err && <div className="alert err">{err}</div>}
      <button className="btn primary" disabled={!file || pct !== null} onClick={send}>
        Enviar vídeo
      </button>
    </section>
  );
}
