-- Fatia 3a: identidade. Uma pessoa pertence a UMA organizacao, e o papel mora nela.
create table if not exists organizations (
  id         uuid primary key default gen_random_uuid(),
  slug       text not null unique,
  name       text not null,
  created_at timestamptz not null default now()
);

create table if not exists users (
  id            uuid primary key default gen_random_uuid(),
  tenant_id     uuid not null references organizations(id) on delete cascade,
  email         text not null unique,
  password_hash text not null,
  role          text not null check (role in ('admin', 'member')),
  created_at    timestamptz not null default now()
);

create index if not exists users_tenant_idx on users (tenant_id);

-- O ovo e a galinha do login: autenticar exige ler `users` ANTES de existir tenant, e
-- a policy da 003 impede isso. Estas duas funcoes sao a unica fresta, e ficam
-- explicitas no schema em vez de virarem privilegio difuso da aplicacao.
create or replace function auth_user_by_email(p_email text)
returns table (id uuid, tenant_id uuid, email text, password_hash text, role text)
language sql
security definer
set search_path = public
as $$
  select u.id, u.tenant_id, u.email, u.password_hash, u.role
  from users u
  where u.email = lower(p_email)
$$;

create or replace function auth_user_by_id(p_id uuid)
returns table (id uuid, tenant_id uuid, email text, password_hash text, role text)
language sql
security definer
set search_path = public
as $$
  select u.id, u.tenant_id, u.email, u.password_hash, u.role
  from users u
  where u.id = p_id
$$;
