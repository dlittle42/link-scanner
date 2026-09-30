-- Remember the status that was reviewed. A later scan drops that review
-- when the error code changes.

alter table public.decisions
  add column if not exists reviewed_status text;

grant select, update, delete on public.decisions to service_role;
grant select, delete on public.resolutions to service_role;

notify pgrst, 'reload schema';
