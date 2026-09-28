-- Run this in the Supabase SQL editor for a new project.
-- The GitHub Action writes scans with the service role key.
-- Signed-in users read the queue. Only an admin can set a verdict.

create table public.scans (
  id integer primary key check (id = 1),
  generated_at text not null,
  errors jsonb not null default '[]'::jsonb
);

create table public.findings (
  id bigint generated always as identity primary key,
  site text not null,
  url text not null,
  kind text not null check (kind in ('broken', 'unchecked')),
  status text not null,
  sources jsonb not null default '[]'::jsonb
);

create index findings_site_url_idx on public.findings (site, url);

create table public.decisions (
  url text primary key,
  verdict text check (verdict in ('broken', 'not_broken')),
  verdict_by text,
  verdict_at text,
  action text check (action in ('replace', 'delete')),
  alternative_url text,
  action_note text,
  action_by text,
  action_at text
);

create table public.profiles (
  id uuid primary key references auth.users (id) on delete cascade,
  role text not null check (role in ('admin', 'member'))
);

create or replace function public.is_admin()
returns boolean
language sql
stable
security definer
set search_path = public
as $$
  select exists (
    select 1
    from public.profiles
    where id = auth.uid()
      and role = 'admin'
  );
$$;

create or replace function public.handle_new_user()
returns trigger
language plpgsql
security definer
set search_path = public
as $$
begin
  insert into public.profiles (id, role)
  values (new.id, 'member')
  on conflict (id) do nothing;
  return new;
end;
$$;

create trigger on_auth_user_created
  after insert on auth.users
  for each row execute function public.handle_new_user();

create or replace function public.enforce_decision_write()
returns trigger
language plpgsql
security definer
set search_path = public
as $$
declare
  verdict_changed boolean := false;
  action_changed boolean := false;
begin
  if tg_op = 'UPDATE' then
    verdict_changed := new.verdict is distinct from old.verdict
      or new.verdict_by is distinct from old.verdict_by
      or new.verdict_at is distinct from old.verdict_at;
    action_changed := new.action is distinct from old.action
      or new.alternative_url is distinct from old.alternative_url
      or new.action_note is distinct from old.action_note
      or new.action_by is distinct from old.action_by
      or new.action_at is distinct from old.action_at;
  else
    verdict_changed := true;
    action_changed := new.action is not null
      or new.alternative_url is not null
      or new.action_note is not null;
  end if;

  if verdict_changed and not public.is_admin() then
    raise exception 'Only an admin can confirm whether a link is broken';
  end if;

  if new.verdict is null or new.verdict not in ('broken', 'not_broken') then
    raise exception 'Verdict must be broken or not_broken';
  end if;

  if new.verdict = 'not_broken' then
    new.action := null;
    new.alternative_url := null;
    new.action_note := null;
    new.action_by := null;
    new.action_at := null;
    return new;
  end if;

  if action_changed then
    if auth.uid() is null then
      raise exception 'Sign in to choose an action';
    end if;
    if new.verdict is distinct from 'broken' then
      raise exception 'Confirm the link is broken before choosing an action';
    end if;
    if new.action = 'replace' then
      if new.alternative_url is null or new.alternative_url !~* '^https?://' then
        raise exception 'Enter a full http or https URL';
      end if;
    elsif new.action = 'delete' then
      new.alternative_url := null;
    else
      raise exception 'Action must be replace or delete';
    end if;
  end if;

  return new;
end;
$$;

create trigger decisions_enforce_write
  before insert or update on public.decisions
  for each row execute function public.enforce_decision_write();

alter table public.scans enable row level security;
alter table public.findings enable row level security;
alter table public.decisions enable row level security;
alter table public.profiles enable row level security;

create policy scans_read on public.scans
  for select to authenticated
  using (true);

create policy findings_read on public.findings
  for select to authenticated
  using (true);

create policy decisions_read on public.decisions
  for select to authenticated
  using (true);

create policy decisions_insert on public.decisions
  for insert to authenticated
  with check (public.is_admin());

create policy decisions_update on public.decisions
  for update to authenticated
  using (true)
  with check (true);

create policy profiles_read_own on public.profiles
  for select to authenticated
  using (id = auth.uid());

grant select on public.scans, public.findings, public.profiles to authenticated;
grant select, insert, update on public.decisions to authenticated;
grant execute on function public.is_admin() to authenticated;
