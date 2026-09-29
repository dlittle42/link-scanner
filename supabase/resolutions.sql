-- Resolved is per site. The same URL on two sites can be resolved independently.

create table if not exists public.resolutions (
  site text not null,
  url text not null,
  resolved_by text,
  resolved_at text not null,
  primary key (site, url)
);

alter table public.resolutions enable row level security;

drop policy if exists resolutions_read on public.resolutions;
create policy resolutions_read on public.resolutions
  for select to authenticated
  using (true);

drop policy if exists resolutions_insert on public.resolutions;
create policy resolutions_insert on public.resolutions
  for insert to authenticated
  with check (auth.uid() is not null);

drop policy if exists resolutions_update on public.resolutions;
create policy resolutions_update on public.resolutions
  for update to authenticated
  using (true)
  with check (auth.uid() is not null);

drop policy if exists resolutions_delete on public.resolutions;
create policy resolutions_delete on public.resolutions
  for delete to authenticated
  using (auth.uid() is not null);

grant select, insert, update, delete on public.resolutions to authenticated;

-- Keep a resolved mark that belongs to only one site. A URL found on
-- more than one site goes back to confirmed error so each site can be
-- resolved on its own. This runs before the write check, which requires
-- a signed-in user.
insert into public.resolutions (site, url, resolved_by, resolved_at)
select f.site, d.url, d.resolved_by, d.resolved_at
from public.decisions d
join public.findings f on f.url = d.url
where d.resolved_at is not null
  and (
    select count(distinct f2.site)
    from public.findings f2
    where f2.url = d.url
  ) = 1
on conflict (site, url) do nothing;

alter table public.decisions disable trigger decisions_enforce_write;
update public.decisions
set resolved_by = null,
    resolved_at = null
where resolved_at is not null;
alter table public.decisions enable trigger decisions_enforce_write;

create or replace function public.enforce_resolution_write()
returns trigger
language plpgsql
security definer
set search_path = public
as $$
begin
  if auth.uid() is null then
    raise exception 'Sign in to mark a link resolved';
  end if;
  if not exists (
    select 1
    from public.decisions
    where url = new.url
      and verdict = 'broken'
  ) then
    raise exception 'Confirm the link is an error before marking it resolved';
  end if;
  return new;
end;
$$;

drop trigger if exists resolutions_enforce_write on public.resolutions;
create trigger resolutions_enforce_write
  before insert or update on public.resolutions
  for each row execute function public.enforce_resolution_write();

create or replace function public.enforce_decision_write()
returns trigger
language plpgsql
security definer
set search_path = public
as $$
declare
  verdict_changed boolean := false;
  action_changed boolean := false;
  resolved_changed boolean := false;
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
    resolved_changed := new.resolved_at is distinct from old.resolved_at
      or new.resolved_by is distinct from old.resolved_by;
  else
    verdict_changed := true;
    action_changed := new.action is not null
      or new.alternative_url is not null
      or new.action_note is not null;
    resolved_changed := new.resolved_at is not null
      or new.resolved_by is not null;
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
    new.resolved_by := null;
    new.resolved_at := null;
    delete from public.resolutions where url = new.url;
    return new;
  end if;

  if resolved_changed then
    if auth.uid() is null then
      raise exception 'Sign in to mark a link resolved';
    end if;
    if new.verdict is distinct from 'broken' then
      raise exception 'Confirm the link is an error before marking it resolved';
    end if;
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

notify pgrst, 'reload schema';
