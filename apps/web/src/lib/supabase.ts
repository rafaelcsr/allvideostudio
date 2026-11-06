import { createClient, type SupabaseClient } from "@supabase/supabase-js";

let client: SupabaseClient | null = null;

export function supabase(): SupabaseClient {
  if (!client) {
    const url = process.env.NEXT_PUBLIC_SUPABASE_URL;
    const key = process.env.NEXT_PUBLIC_SUPABASE_ANON_KEY;
    if (!url || !key) throw new Error("Configure NEXT_PUBLIC_SUPABASE_URL e NEXT_PUBLIC_SUPABASE_ANON_KEY");
    client = createClient(url, key, { auth: { persistSession: true, autoRefreshToken: true } });
  }
  return client;
}

export type Project = {
  id: string;
  title: string;
  status: string;
  source_name: string | null;
  source_size: number | null;
  declared_duration: number | null;
  duration: number | null;
  created_at: string;
};

export type Job = {
  id: string;
  project_id: string;
  kind: "edit" | "revise";
  style: string | null;
  options: Record<string, unknown>;
  status: "queued" | "running" | "done" | "failed" | "canceled";
  progress: number;
  stage: string | null;
  error: string | null;
  warnings: string[];
  estimate_usd: number | null;
  cost_usd: number | null;
  analysis: Analysis | null;
  parent_job_id: string | null;
  created_at: string;
};

export type Analysis = {
  title: string;
  summary: string;
  hashtags: string[];
  topics: { title: string; start_sentence: number; end_sentence: number }[];
  removals: { sentence_id: number; quote: string; reason: string }[];
  highlights: { start_sentence: number; end_sentence: number; title: string; hook: string; score: number; reason: string }[];
  hooks: { sentence_id: number; quote: string; why: string }[];
  impact_phrases: { sentence_id: number; quote: string; keywords: string[] }[];
  source: "llm" | "heuristic";
};

export type Render = {
  id: string;
  job_id: string;
  kind: "full" | "short";
  variant: string | null;
  title: string | null;
  aspect: string;
  duration: number;
  width: number;
  height: number;
  size_bytes: number;
  captions_key: string | null;
  qc: { ok: boolean; loudness: number | null; checks: { name: string; ok: boolean; detail: string; blocking: boolean }[] };
  created_at: string;
};
