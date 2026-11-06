import { supabase } from "./supabase";

const API = (process.env.NEXT_PUBLIC_API_URL || "").replace(/\/$/, "");

export class ApiError extends Error {
  constructor(public status: number, message: string) {
    super(message);
  }
}

export async function api<T = unknown>(path: string, init: { method?: string; body?: unknown } = {}): Promise<T> {
  const { data } = await supabase().auth.getSession();
  const token = data.session?.access_token;
  if (!token) throw new ApiError(401, "Sessão expirada. Entre novamente.");
  const res = await fetch(`${API}${path}`, {
    method: init.method || (init.body ? "POST" : "GET"),
    headers: { Authorization: `Bearer ${token}`, ...(init.body ? { "Content-Type": "application/json" } : {}) },
    body: init.body ? JSON.stringify(init.body) : undefined,
  });
  const text = await res.text();
  const json = text ? JSON.parse(text) : null;
  if (!res.ok) {
    const detail = json?.detail;
    const msg = typeof detail === "string" ? detail : Array.isArray(detail) ? detail.map((d) => d.msg).join("; ") : res.statusText;
    throw new ApiError(res.status, msg || `Erro ${res.status}`);
  }
  return json as T;
}

export function putWithProgress(url: string, file: File, contentType: string, onProgress: (pct: number) => void): Promise<void> {
  return new Promise((resolve, reject) => {
    const xhr = new XMLHttpRequest();
    xhr.open("PUT", url);
    xhr.setRequestHeader("Content-Type", contentType);
    xhr.upload.onprogress = (e) => e.lengthComputable && onProgress(e.loaded / e.total);
    xhr.onload = () => (xhr.status >= 200 && xhr.status < 300 ? resolve() : reject(new Error(`Upload falhou (${xhr.status})`)));
    xhr.onerror = () => reject(new Error("Falha de rede no upload (verifique CORS do bucket R2)"));
    xhr.send(file);
  });
}

export function videoDuration(file: File): Promise<number> {
  return new Promise((resolve, reject) => {
    const v = document.createElement("video");
    v.preload = "metadata";
    v.onloadedmetadata = () => {
      URL.revokeObjectURL(v.src);
      resolve(v.duration);
    };
    v.onerror = () => reject(new Error("Não foi possível ler o vídeo (formato não suportado pelo navegador?)"));
    v.src = URL.createObjectURL(file);
  });
}

export type Estimate = {
  transcription_usd: number;
  editorial_usd: number;
  render_usd: number;
  storage_usd: number;
  total_usd: number;
  total_brl: number;
};

export type BudgetState = {
  spent_usd: number;
  spent_brl: number;
  cap_usd: number;
  cap_brl: number;
  pct: number;
  by_service?: Record<string, number>;
  storage_gb?: number;
  storage_cap_gb?: number;
};

export const brl = (v: number) => v.toLocaleString("pt-BR", { style: "currency", currency: "BRL" });
export const fmtDur = (s: number | null | undefined) => {
  if (!s && s !== 0) return "—";
  const m = Math.floor(s / 60);
  const r = Math.round(s % 60);
  return `${m}:${String(r).padStart(2, "0")}`;
};
export const fmtSize = (b: number | null | undefined) => (b ? `${(b / 1e6).toFixed(b > 1e9 ? 0 : 1)} MB` : "—");
