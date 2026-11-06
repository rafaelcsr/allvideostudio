"use client";
import { useEffect, useState } from "react";
import { TopBar } from "@/components/TopBar";
import { api, brl, type BudgetState } from "@/lib/api";
import { supabase } from "@/lib/supabase";
import { useSession } from "@/lib/useSession";

type Usage = { id: number; service: string; description: string; cost_usd: number; created_at: string; job_id: string | null };

const SERVICE: Record<string, string> = {
  modal_gpu: "Transcrição (GPU)",
  modal_cpu: "Renderização (CPU)",
  anthropic: "Análise editorial (IA)",
  r2_storage: "Armazenamento",
};

export default function Budget() {
  const { session } = useSession();
  const [b, setB] = useState<BudgetState | null>(null);
  const [rows, setRows] = useState<Usage[]>([]);
  const [alerts, setAlerts] = useState<{ id: number; message: string; created_at: string }[]>([]);
  const [err, setErr] = useState<string | null>(null);

  useEffect(() => {
    if (!session) return;
    api<BudgetState>("/api/budget").then(setB).catch((e) => setErr(e.message));
    const sb = supabase();
    sb.from("usage_ledger").select("*").order("created_at", { ascending: false }).limit(100).then(({ data }) => setRows((data as Usage[]) || []));
    sb.from("budget_alerts").select("*").order("created_at", { ascending: false }).limit(10).then(({ data }) => setAlerts(data || []));
  }, [session]);

  if (!session) return null;
  const rate = b && b.spent_usd > 0 ? b.spent_brl / b.spent_usd : 5.5;
  const pct = Math.min(1, b?.pct ?? 0);
  return (
    <main className="shell">
      <TopBar active="budget" />
      <h1>Controle de custos</h1>
      {err && <div className="alert err">{err}</div>}
      {b && (
        <div className="grid grid-3" style={{ margin: "16px 0" }}>
          <div className="card">
            <span className="muted small">Gasto no mês</span>
            <div className="kpi">{brl(b.spent_brl)}</div>
            <div className="progress" style={{ marginTop: 10 }}>
              <div style={{ width: `${pct * 100}%`, background: pct > 0.8 ? "var(--err)" : undefined }} />
            </div>
            <span className="muted small">{(pct * 100).toFixed(1)}% do teto de {brl(b.cap_brl)}</span>
          </div>
          <div className="card">
            <span className="muted small">Disponível</span>
            <div className="kpi">{brl(Math.max(0, b.cap_brl - b.spent_brl))}</div>
            <span className="muted small">Jobs que ultrapassariam o teto são bloqueados antes de iniciar.</span>
          </div>
          <div className="card">
            <span className="muted small">Armazenamento</span>
            <div className="kpi">{(b.storage_gb ?? 0).toFixed(2)} GB</div>
            <span className="muted small">limite configurado: {b.storage_cap_gb} GB (R2 gratuito até 10 GB)</span>
          </div>
        </div>
      )}
      {alerts.length > 0 && (
        <div className="stack" style={{ marginBottom: 16 }}>
          {alerts.map((a) => (
            <div key={a.id} className="alert warn">{a.message}</div>
          ))}
        </div>
      )}
      <div className="grid grid-2" style={{ alignItems: "start" }}>
        <section className="card">
          <h2>Por serviço (mês)</h2>
          <table>
            <tbody>
              {Object.entries(b?.by_service || {}).map(([k, v]) => (
                <tr key={k}>
                  <td>{SERVICE[k] || k}</td>
                  <td style={{ textAlign: "right" }}>{brl(v * rate)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </section>
        <section className="card">
          <h2>Lançamentos recentes</h2>
          <table>
            <thead>
              <tr><th>Quando</th><th>Serviço</th><th style={{ textAlign: "right" }}>Custo</th></tr>
            </thead>
            <tbody>
              {rows.map((r) => (
                <tr key={r.id}>
                  <td className="small">{new Date(r.created_at).toLocaleString("pt-BR")}</td>
                  <td className="small">{SERVICE[r.service] || r.service}<br /><span className="muted">{r.description}</span></td>
                  <td className="small" style={{ textAlign: "right" }}>{brl(Number(r.cost_usd) * rate)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </section>
      </div>
    </main>
  );
}
