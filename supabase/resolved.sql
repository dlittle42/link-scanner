-- Add an explicit resolved mark. Replacement and deletion stay on confirmed errors.
alter table public.decisions
  add column if not exists resolved_by text,
  add column if not exists resolved_at text;

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
