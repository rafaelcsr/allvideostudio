-- AI Video Studio — schema inicial
-- Leituras: navegador via RLS (somente dados do próprio usuário).
-- Escritas: somente a API/worker com service role (o navegador não grava diretamente).

create extension if not exists pgcrypto;

create or replace function public.touch_updated_at() returns trigger
language plpgsql set search_path = '' as $$
begin
  new.updated_at = now();
  return new;
end $$;

create table public.projects (
  id uuid primary key default gen_random_uuid(),
  user_id uuid not null references auth.users(id) on delete cascade,
  title text not null,
  source_key text,
  source_name text,
  source_size bigint,
  declared_duration real,
  duration real,
  status text not null default 'awaiting_upload'
    check (status in ('awaiting_upload','uploaded','processing','ready','failed','archived')),
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);
create index on public.projects (user_id, created_at desc);

create table public.jobs (
  id uuid primary key default gen_random_uuid(),
  project_id uuid not null references public.projects(id) on delete cascade,
  user_id uuid not null references auth.users(id) on delete cascade,
  parent_job_id uuid references public.jobs(id) on delete set null,
  kind text not null default 'edit' check (kind in ('edit','revise')),
  style text,
  options jsonb not null default '{}',
  status text not null default 'queued' check (status in ('queued','running','done','failed','canceled')),
  progress real not null default 0,
  stage text,
  error text,
  warnings jsonb not null default '[]',
  estimate_usd numeric(10,4),
  cost_usd numeric(10,4) not null default 0,
  analysis jsonb,
  transcript_key text,
  modal_call_id text,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  finished_at timestamptz
);
create index on public.jobs (project_id, created_at desc);
create index on public.jobs (user_id, created_at desc);
create index on public.jobs (status);

create table public.renders (
  id uuid primary key default gen_random_uuid(),
  job_id uuid not null references public.jobs(id) on delete cascade,
  project_id uuid not null references public.projects(id) on delete cascade,
  user_id uuid not null references auth.users(id) on delete cascade,
  kind text not null check (kind in ('full','short')),
  variant text,
  title text,
  r2_key text not null,
  captions_key text,
  aspect text,
  duration real,
  width int,
  height int,
  size_bytes bigint,
  qc jsonb,
  created_at timestamptz not null default now()
);
create index on public.renders (project_id, created_at desc);

create table public.usage_ledger (
  id bigint generated always as identity primary key,
  user_id uuid references auth.users(id) on delete set null,
  job_id uuid references public.jobs(id) on delete set null,
  service text not null,            -- modal_gpu | modal_cpu | anthropic | r2_storage
  description text,
  quantity numeric,
  unit text,
  cost_usd numeric(12,6) not null default 0,
  created_at timestamptz not null default now()
);
create index on public.usage_ledger (created_at);

create table public.budget_alerts (
  id bigint generated always as identity primary key,
  month date not null,
  level text not null default 'warning',
  message text not null,
  created_at timestamptz not null default now()
);

create trigger projects_touch before update on public.projects for each row execute function public.touch_updated_at();
create trigger jobs_touch before update on public.jobs for each row execute function public.touch_updated_at();

-- ---------- RLS ----------
alter table public.projects enable row level security;
alter table public.jobs enable row level security;
alter table public.renders enable row level security;
alter table public.usage_ledger enable row level security;
alter table public.budget_alerts enable row level security;

create policy "own projects" on public.projects for select using (user_id = (select auth.uid()));
create policy "own jobs" on public.jobs for select using (user_id = (select auth.uid()));
create policy "own renders" on public.renders for select using (user_id = (select auth.uid()));
create policy "own usage" on public.usage_ledger for select using (user_id = (select auth.uid()));
create policy "alerts for signed-in" on public.budget_alerts for select using ((select auth.uid()) is not null);

-- Defesa em profundidade: além do RLS, as roles do navegador não têm privilégio de escrita.
-- anon (não logado) não lê nada; authenticated só lê (filtrado pelas policies acima).
revoke all on public.projects, public.jobs, public.renders, public.usage_ledger, public.budget_alerts from anon, authenticated;
grant select on public.projects, public.jobs, public.renders, public.usage_ledger, public.budget_alerts to authenticated;
revoke execute on function public.touch_updated_at() from public, anon, authenticated;

-- monthly spend summary (respects RLS of the caller)
create view public.monthly_spend with (security_invoker = true) as
  select date_trunc('month', created_at)::date as month, service,
         sum(cost_usd)::numeric(12,4) as cost_usd, count(*) as entries
  from public.usage_ledger group by 1, 2;
revoke all on public.monthly_spend from anon, authenticated;
grant select on public.monthly_spend to authenticated;

-- realtime progress
alter publication supabase_realtime add table public.jobs, public.renders, public.projects;
