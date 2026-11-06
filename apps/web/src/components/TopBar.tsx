"use client";
import { supabase } from "@/lib/supabase";

export function TopBar({ active }: { active?: "studio" | "budget" }) {
  return (
    <header className="topbar">
      <a href="/studio/" className="brand">
        <span className="logo">AI</span> AI Video Studio
      </a>
      <nav className="nav">
        <a href="/studio/" className={active === "studio" ? "active" : ""}>Projetos</a>
        <a href="/budget/" className={active === "budget" ? "active" : ""}>Custos</a>
        <button
          className="btn ghost"
          onClick={async () => {
            await supabase().auth.signOut();
            window.location.href = "/";
          }}
        >
          Sair
        </button>
      </nav>
    </header>
  );
}

const LABELS: Record<string, [string, string]> = {
  awaiting_upload: ["aguardando envio", "warn"],
  uploaded: ["pronto para editar", "ok"],
  processing: ["processando", "run"],
  ready: ["concluído", "ok"],
  failed: ["falhou", "err"],
  archived: ["arquivado", ""],
  queued: ["na fila", "run"],
  running: ["processando", "run"],
  done: ["concluído", "ok"],
  canceled: ["cancelado", ""],
};

export function StatusBadge({ status }: { status: string }) {
  const [label, cls] = LABELS[status] || [status, ""];
  return <span className={`badge ${cls}`}>{label}</span>;
}
