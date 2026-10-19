-- Fatia 3b: o logout passa a encerrar a sessao no servidor.
--
-- Ate aqui `POST /auth/logout` apagava o cookie do navegador e mais nada: um token
-- copiado antes continuava valendo os 7 dias do prazo. O contador abaixo entra no
-- payload assinado do token; `require_session` compara, e o logout incrementa.
alter table users add column if not exists session_epoch integer not null default 0;

-- As tres funcoes da 3a precisam devolver a coluna nova, senao `require_session` nao
-- tem com o que comparar. Mudar o tipo de retorno EXIGE drop: `create or replace`
-- recusa com "cannot change return type of existing function".
--
-- ATENCAO: o drop devolve o EXECUTE a PUBLIC. E o mesmo furo que a 004 fechou, e ele
-- reabre sozinho aqui se os revoke do fim deste arquivo forem esquecidos.
drop function if exists auth_user_by_email(text);
drop function if exists auth_user_by_id(uuid);
drop function if exists auth_create_organization_with_admin(text, text, text);

create function auth_user_by_email(p_email text)
returns table (
  id uuid, tenant_id uuid, email text, password_hash text, role text,
  session_epoch integer
)
language sql
security definer
set search_path = public
as $$
  select u.id, u.tenant_id, u.email, u.password_hash, u.role, u.session_epoch
  from users u
  where u.email = lower(p_email)
$$;

create function auth_user_by_id(p_id uuid)
returns table (
  id uuid, tenant_id uuid, email text, password_hash text, role text,
  session_epoch integer
)
language sql
security definer
set search_path = public
as $$
  select u.id, u.tenant_id, u.email, u.password_hash, u.role, u.session_epoch
  from users u
  where u.id = p_id
$$;

create function auth_create_organization_with_admin(
  p_organization_name text, p_email text, p_password_hash text
)
returns table (
  id uuid, tenant_id uuid, email text, password_hash text, role text,
  session_epoch integer
)
language plpgsql
security definer
set search_path = public
as $$
declare
  v_tenant_id uuid;
  v_user_id   uuid;
begin
  insert into organizations (slug, name)
  values (gen_random_uuid()::text, p_organization_name)
  returning organizations.id into v_tenant_id;

  -- Se o e-mail ja existe, o 23505 sobe daqui e a funcao inteira volta atras: a
  -- organizacao orfa nunca chega a existir. E `EmailAlreadyUsed` na borda.
  insert into users (tenant_id, email, password_hash, role)
  values (v_tenant_id, lower(p_email), p_password_hash, 'admin')
  returning users.id into v_user_id;

  return query select
    v_user_id, v_tenant_id, lower(p_email), p_password_hash, 'admin'::text, 0;
end
$$;

-- A fresta nova. O incremento acontece DENTRO do update, nao lido-e-escrito: dois
-- logouts simultaneos nao podem gravar o mesmo valor.
create or replace function auth_bump_session_epoch(p_user_id uuid)
returns integer
language sql
security definer
set search_path = public
as $$
  update users set session_epoch = session_epoch + 1
  where id = p_user_id
  returning session_epoch;
$$;

-- Privilegios reaplicados para as QUATRO funcoes. As tres recriadas perderam os
-- grants no drop, e ganharam EXECUTE para PUBLIC de volta; a quarta nasce com o
-- mesmo default. Estes grants nominais sao a autorizacao inteira.
grant execute on function auth_user_by_email(text) to sensus_app;
grant execute on function auth_user_by_id(uuid) to sensus_app;
grant execute on function auth_create_organization_with_admin(text, text, text) to sensus_app;
grant execute on function auth_bump_session_epoch(uuid) to sensus_app;

revoke execute on function auth_user_by_email(text) from public;
revoke execute on function auth_user_by_id(uuid) from public;
revoke execute on function auth_create_organization_with_admin(text, text, text) from public;
revoke execute on function auth_bump_session_epoch(uuid) from public;

-- `anon`, `authenticated` e `service_role` sao do Supabase e podem nao existir num
-- Postgres nu; sem o guarda, a migracao quebraria fora do Supabase.
do $$
declare
  papel text;
  alvo  text;
begin
  foreach papel in array array['anon', 'authenticated', 'service_role'] loop
    if exists (select 1 from pg_roles where rolname = papel) then
      foreach alvo in array array[
        'auth_user_by_email(text)',
        'auth_user_by_id(uuid)',
        'auth_create_organization_with_admin(text, text, text)',
        'auth_bump_session_epoch(uuid)'
      ] loop
        execute format('revoke execute on function %s from %I', alvo, papel);
      end loop;
    end if;
  end loop;
end
$$;
