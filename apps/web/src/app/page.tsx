"use client";
import { useEffect, useState } from "react";
import { supabase } from "@/lib/supabase";

export default function Login() {
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [mode, setMode] = useState<"password" | "magic">("password");
  const [msg, setMsg] = useState<{ kind: "ok" | "err"; text: string } | null>(null);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    supabase().auth.getSession().then(({ data }) => {
      if (data.session) window.location.href = "/studio/";
    });
  }, []);

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    setBusy(true);
    setMsg(null);
    const sb = supabase();
    try {
      if (mode === "magic") {
        const { error } = await sb.auth.signInWithOtp({
          email,
          options: { emailRedirectTo: `${window.location.origin}/studio/`, shouldCreateUser: false },
        });
        if (error) throw error;
        setMsg({ kind: "ok", text: "Link de acesso enviado. Verifique seu e-mail." });
      } else {
        const { error } = await sb.auth.signInWithPassword({ email, password });
        if (error) throw error;
        window.location.href = "/studio/";
      }
    } catch (err) {
      setMsg({ kind: "err", text: err instanceof Error ? err.message : "Falha no login" });
    } finally {
      setBusy(false);
    }
  }

  return (
    <main className="shell" style={{ maxWidth: 440, paddingTop: "12vh" }}>
      <div className="brand" style={{ fontSize: 20, marginBottom: 8 }}>
        <span className="logo">AI</span> AI Video Studio
      </div>
      <p className="muted" style={{ marginBottom: 24 }}>
        Envie o vídeo bruto. Receba a edição profissional — cortes, legendas, enquadramento e cortes curtos.
      </p>
      <form className="card stack" onSubmit={submit}>
        <div>
          <label htmlFor="email">E-mail</label>
          <input id="email" type="email" required autoComplete="email" value={email} onChange={(e) => setEmail(e.target.value)} />
        </div>
        {mode === "password" && (
          <div>
            <label htmlFor="pw">Senha</label>
            <input id="pw" type="password" required autoComplete="current-password" value={password} onChange={(e) => setPassword(e.target.value)} />
          </div>
        )}
        <button className="btn primary" disabled={busy}>
          {busy ? "Entrando…" : mode === "password" ? "Entrar" : "Enviar link de acesso"}
        </button>
        <button type="button" className="btn ghost small" onClick={() => setMode(mode === "password" ? "magic" : "password")}>
          {mode === "password" ? "Entrar com link por e-mail" : "Entrar com senha"}
        </button>
        {msg && <div className={`alert ${msg.kind}`}>{msg.text}</div>}
      </form>
    </main>
  );
}
