-- Fatia 3e: o convite passa a ser mandado POR e-mail, e preso ao endereco.
--
-- A 3c registrou por escrito o que faltava: "o convite nao prova posse do e-mail". A 3d
-- trouxe o provedor; esta migracao e quem prende o convite ao endereco. A prova de posse
-- so vale porque o link passa a existir SOMENTE dentro da caixa de entrada -- o token
-- deixa de ser devolvido ao admin (ver routes/invites.py).
alter table invitations add column if not exists email text;

-- NULLABLE de proposito. As linhas historicas (aceitas, revogadas) nao tem endereco, e um
-- `not null` exigiria inventar valor para elas. Quem garante que todo convite NOVO tem
-- e-mail e a borda -- nao uma restricao que obrigue a mentir sobre o passado.

-- Um caminho so: convite pendente sem vinculo nao sobrevive a esta fatia. Mante-los
-- exigiria um ramo "convite antigo, sem e-mail" no codigo, preservando para sempre o modo
-- inseguro que a fatia existe para matar.
update invitations set revoked_at = now()
where email is null and accepted_at is null and revoked_at is null;

-- A funcao de aceite muda de assinatura: o e-mail sai da LINHA DO CONVITE, nao de quem
-- chama. Nao ha mais entrada por onde outro endereco entraria -- e isso e estrutura, nao
-- validacao.
--
-- ATENCAO: a licao da 3c NAO se aplica aqui. `create or replace` preserva a ACL quando a
-- assinatura NAO muda; mudando, e `drop` + `create`, e o `drop` ZERA a ACL. Por isso o
-- ritual de grants abaixo e obrigatorio, e nao zelo: sem ele `anon` executaria uma fresta
-- que CRIA CONTA.
drop function if exists auth_accept_invitation(text, text, text);

create function auth_accept_invitation(p_token_hash text, p_password_hash text)
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
  v_email         text;
  v_user_id       uuid;
  v_session_epoch integer;
begin
  -- O consumo continua acontecendo DENTRO do update (licao da 3c): duas pessoas clicando
  -- no mesmo link ao mesmo tempo, uma ganha, a outra nao encontra linha. O que mudou e
  -- que o e-mail volta junto, em vez de vir por parametro.
  update invitations set accepted_at = now()
  where token_hash = p_token_hash
    and accepted_at is null
    and revoked_at is null
    and expires_at > now()
  returning invitations.tenant_id, invitations.email into v_tenant_id, v_email;

  if v_tenant_id is null then
    return;
  end if;

  -- Se o e-mail ja tem conta (em OUTRA organizacao -- na propria, a borda barra antes), o
  -- 23505 sobe daqui e a funcao inteira volta atras: o `accepted_at` e desfeito e o
  -- convite CONTINUA USAVEL. Propriedade da 3c, preservada.
  insert into users (tenant_id, email, password_hash, role)
  values (v_tenant_id, lower(v_email), p_password_hash, 'member')
  returning users.id, users.session_epoch into v_user_id, v_session_epoch;

  update invitations set accepted_user_id = v_user_id
  where token_hash = p_token_hash;

  return query select
    v_user_id, v_tenant_id, lower(v_email), p_password_hash, 'member'::text,
    v_session_epoch;
end
$$;

-- O ritual inteiro, refeito. A funcao nasceu agora e a ACL dela veio do zero.
grant execute on function auth_accept_invitation(text, text) to sensus_app;
revoke execute on function auth_accept_invitation(text, text) from public;

do $$
declare
  papel text;
begin
  foreach papel in array array['anon', 'authenticated', 'service_role'] loop
    if exists (select 1 from pg_roles where rolname = papel) then
      execute format(
        'revoke execute on function auth_accept_invitation(text, text) from %I', papel
      );
    end if;
  end loop;
end
$$;

-- A fresta de LEITURA passa a devolver o e-mail, para a previa poder dizer de quem e o
-- convite.
--
-- `drop` + `create`, e nao `create or replace`: a lista de argumentos nao muda, mas as
-- COLUNAS de `returns table` mudam, e isso e mudanca de tipo de retorno -- o replace
-- recusa com "cannot change return type of existing function". Como e drop, a ACL zera
-- de novo, e o ritual abaixo e obrigatorio.
drop function if exists auth_invitation_by_token_hash(text);

create function auth_invitation_by_token_hash(p_token_hash text)
returns table (
  organization_name text, email text, expires_at timestamptz,
  accepted_at timestamptz, revoked_at timestamptz
)
language sql
security definer
set search_path = public
as $$
  select o.name, i.email, i.expires_at, i.accepted_at, i.revoked_at
  from invitations i
  join organizations o on o.id = i.tenant_id
  where i.token_hash = p_token_hash
$$;

grant execute on function auth_invitation_by_token_hash(text) to sensus_app;
revoke execute on function auth_invitation_by_token_hash(text) from public;

do $$
declare
  papel text;
begin
  foreach papel in array array['anon', 'authenticated', 'service_role'] loop
    if exists (select 1 from pg_roles where rolname = papel) then
      execute format(
        'revoke execute on function auth_invitation_by_token_hash(text) from %I', papel
      );
    end if;
  end loop;
end
$$;
