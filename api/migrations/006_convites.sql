-- Fatia 3c: convites.
--
-- Ate aqui `POST /auth/signup` era o unico caminho para ter conta, e ele CRIA uma
-- organizacao nova. Um cliente com cinco pessoas tinha cinco organizacoes isoladas,
-- cada uma com a propria base -- o oposto do produto. O convite abaixo e o caminho de
-- entrada que faltava: o admin gera, e quem abre entra como `member` na organizacao
-- dele.
--
-- O link E a credencial: sem provedor de e-mail (fatia 3d) nao ha como provar posse de
-- endereco. O que limita o vazamento e o uso unico, o prazo e a revogacao.
create table if not exists invitations (
  id               uuid primary key default gen_random_uuid(),
  tenant_id        uuid not null references organizations(id) on delete cascade,
  -- sha256 do token, nunca o token. Vazamento do banco nao pode entregar convites
  -- validos. Consequencia aceita: o link so aparece uma vez, na criacao.
  --
  -- Unico GLOBAL, nao por tenant: o convidado chega sem tenant, e a busca precisa
  -- funcionar so pelo hash.
  token_hash       text not null unique,
  created_by       uuid not null references users(id) on delete cascade,
  created_at       timestamptz not null default now(),
  expires_at       timestamptz not null,
  -- O estado do convite e DERIVADO destes carimbos, e nao ha coluna `status`: um
  -- status gravado pode discordar dos carimbos, e ai ha duas verdades sobre o mesmo
  -- convite. Pendente e `accepted_at is null and revoked_at is null and expires_at >
  -- now()`.
  accepted_at      timestamptz,
  accepted_user_id uuid references users(id) on delete set null,
  revoked_at       timestamptz
);

create index if not exists invitations_tenant_idx on invitations (tenant_id);

grant select, insert, update, delete on invitations to sensus_app;

-- Mesmo desenho das policies da 003, inclusive as duas defesas contra conexao sem
-- contexto: o segundo argumento `true` de current_setting (GUC nunca lido -> nulo em
-- vez de erro) e o `nullif(..., '')` (GUC ja lido, que volta a string VAZIA no fim da
-- transacao). Sem eles, a proxima consulta da conexao do pool estouraria em `''::uuid`
-- ou, pior, veria linhas de todo mundo.
alter table invitations enable row level security;

create policy invitations_por_tenant on invitations
  using (tenant_id = nullif(current_setting('sensus.tenant_id', true), '')::uuid)
  with check (tenant_id = nullif(current_setting('sensus.tenant_id', true), '')::uuid);

-- A fresta de LEITURA. Devolve a linha sempre que o token existir, com os carimbos
-- crus, e nenhuma linha se nao existir. Quem deriva o veredito -- pendente, usado,
-- vencido ou revogado -- e a borda.
--
-- Deliberado: uma funcao que devolvesse linha SO se pendente deixaria a borda sem como
-- distinguir "ja usado" de "vencido" de "inexistente", e a borda promete justamente
-- essa distincao. Nao expoe nada a mais: so o `sensus_app` a executa, e quem chega
-- aqui ja apresentou um token de 256 bits.
create function auth_invitation_by_token_hash(p_token_hash text)
returns table (
  organization_name text, expires_at timestamptz,
  accepted_at timestamptz, revoked_at timestamptz
)
language sql
security definer
set search_path = public
as $$
  select o.name, i.expires_at, i.accepted_at, i.revoked_at
  from invitations i
  join organizations o on o.id = i.tenant_id
  where i.token_hash = p_token_hash
$$;

-- A fresta de ESCRITA. Mesmo formato de retorno das outras tres, para o adaptador
-- reusar o `_one` que ja existe.
create function auth_accept_invitation(
  p_token_hash text, p_email text, p_password_hash text
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
  v_tenant_id     uuid;
  v_user_id       uuid;
  v_session_epoch integer;
begin
  -- O consumo acontece DENTRO do update, nao lido-e-escrito -- mesma licao do
  -- auth_bump_session_epoch da 3b. Duas pessoas clicando no mesmo link ao mesmo
  -- tempo: uma ganha o update, a outra nao encontra linha.
  update invitations set accepted_at = now()
  where token_hash = p_token_hash
    and accepted_at is null
    and revoked_at is null
    and expires_at > now()
  returning invitations.tenant_id into v_tenant_id;

  if v_tenant_id is null then
    -- Nada consumido. A borda chama auth_invitation_by_token_hash para dizer POR QUE:
    -- uma consulta a mais so no caminho que ja falhou.
    return;
  end if;

  -- Se o e-mail ja tem conta, o 23505 sobe daqui e a funcao inteira volta atras: o
  -- `accepted_at` acima e desfeito e o convite CONTINUA USAVEL. Tentar com um e-mail
  -- que ja tem conta nao queima o link.
  insert into users (tenant_id, email, password_hash, role)
  values (v_tenant_id, lower(p_email), p_password_hash, 'member')
  returning users.id, users.session_epoch into v_user_id, v_session_epoch;

  update invitations set accepted_user_id = v_user_id
  where token_hash = p_token_hash;

  return query select
    v_user_id, v_tenant_id, lower(p_email), p_password_hash, 'member'::text,
    v_session_epoch;
end
$$;

-- A correcao estacionada pela 3b: a funcao devolvia o literal `0` como session_epoch
-- em vez de ler a coluna. Acerta hoje por coincidencia (default `0`, linha criada por
-- ela mesma) e erra no instante em que um trigger ou backfill tocar a coluna.
--
-- A nota da 3b supos que consertar exigiria `drop` + `create`, reabrindo EXECUTE para
-- PUBLIC. A suposicao estava ERRADA: a assinatura nao muda, so o corpo, e `create or
-- replace` PRESERVA a ACL (sondado no Postgres real em 2026-09-12, em transacao
-- revertida). O teste de grants prova que a ACL sobreviveu.
create or replace function auth_create_organization_with_admin(
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
  v_tenant_id     uuid;
  v_user_id       uuid;
  v_session_epoch integer;
begin
  insert into organizations (slug, name)
  values (gen_random_uuid()::text, p_organization_name)
  returning organizations.id into v_tenant_id;

  -- Se o e-mail ja existe, o 23505 sobe daqui e a funcao inteira volta atras: a
  -- organizacao orfa nunca chega a existir. E `EmailAlreadyUsed` na borda.
  insert into users (tenant_id, email, password_hash, role)
  values (v_tenant_id, lower(p_email), p_password_hash, 'admin')
  returning users.id, users.session_epoch into v_user_id, v_session_epoch;

  return query select
    v_user_id, v_tenant_id, lower(p_email), p_password_hash, 'admin'::text,
    v_session_epoch;
end
$$;

-- Privilegios das DUAS funcoes novas. Elas nascem com `anon`, `authenticated` e
-- `service_role` na ACL -- e o `alter default privileges` do Supabase -- e `revoke
-- from public` NAO os remove, porque o grant nao veio de PUBLIC. `anon` e o papel das
-- requisicoes nao autenticadas: uma fresta que ACEITA convite, alcancavel por ele, e
-- buraco aberto. Estes grants nominais sao a autorizacao inteira.
grant execute on function auth_invitation_by_token_hash(text) to sensus_app;
grant execute on function auth_accept_invitation(text, text, text) to sensus_app;

revoke execute on function auth_invitation_by_token_hash(text) from public;
revoke execute on function auth_accept_invitation(text, text, text) from public;

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
        'auth_invitation_by_token_hash(text)',
        'auth_accept_invitation(text, text, text)'
      ] loop
        execute format('revoke execute on function %s from %I', alvo, papel);
      end loop;
    end if;
  end loop;
end
$$;
