-- Fatia 3a, correcao: o cadastro nao passava pela propria RLS.
--
-- O INSERT em `users` acontece na mesma transacao que cria a organizacao, e por isso
-- NAO tem como ter contexto de tenant: o tenant esta nascendo ali. A policy
-- `users_por_tenant` da 003 e `for all`, entao o `with check` avaliava
-- `nullif(current_setting('sensus.tenant_id', true), '')::uuid` -> nulo -> recusa.
-- Conectando pelo `sensus_app`, que e o papel de producao, POST /auth/signup virava
-- 500 com `new row violates row-level security policy for table "users"`. Nao
-- aparecia porque o teste de integracao do diretorio conectava pelo dono das tabelas,
-- que ignora policy.
--
-- A saida e a mesma fresta ja estabelecida na 002 para o login: uma funcao
-- `security definer`, explicita no schema, em vez de privilegio difuso da aplicacao.
create or replace function auth_create_organization_with_admin(
  p_organization_name text, p_email text, p_password_hash text
)
returns table (id uuid, tenant_id uuid, email text, password_hash text, role text)
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

  return query select v_user_id, v_tenant_id, lower(p_email), p_password_hash, 'admin'::text;
end
$$;

grant execute on function auth_create_organization_with_admin(text, text, text) to sensus_app;

-- `create function` concede EXECUTE a PUBLIC por default, e no Supabase o schema
-- `public` ainda carrega EXECUTE para `anon` e `authenticated` — os papeis do
-- PostgREST. Estas tres funcoes existem justamente para FURAR a RLS: deixa-las
-- abertas e publicar um primitivo de leitura de hash de senha e de enumeracao de
-- e-mail. Os grants nominais acima e na 003 sao a autorizacao inteira.
revoke execute on function auth_user_by_email(text) from public;
revoke execute on function auth_user_by_id(uuid) from public;
revoke execute on function auth_create_organization_with_admin(text, text, text) from public;

-- `anon` e `authenticated` sao do Supabase e podem nao existir num Postgres nu; sem o
-- guarda, a migracao quebraria fora do Supabase.
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
        'auth_create_organization_with_admin(text, text, text)'
      ] loop
        execute format('revoke execute on function %s from %I', alvo, papel);
      end loop;
    end if;
  end loop;
end
$$;
