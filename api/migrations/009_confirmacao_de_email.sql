-- Fatia 3f: confirmacao de e-mail no cadastro.
--
-- Ate aqui o cadastro criava organizacao e conta na hora, e por isso respondia 409
-- quando o e-mail ja existia -- um oraculo de quem tem conta no Sensus, aberto a
-- qualquer um. Com confirmacao, NADA e criado antes de a pessoa abrir o link: o
-- cadastro vira um pedido, e o pedido mora nesta tabela.
create table if not exists pending_signups (
  id                uuid primary key default gen_random_uuid(),
  organization_name text not null,
  email             text not null,
  -- sha256 do token, nunca o token. Unico GLOBAL: quem chega com o link nao tem tenant
  -- a informar, e a busca e so pelo hash.
  token_hash        text not null unique,
  created_at        timestamptz not null default now(),
  expires_at        timestamptz not null,
  -- Sem coluna `status`, pela razao da 006 e da 007: um status gravado pode discordar
  -- dos carimbos, e ai ha duas verdades sobre o mesmo link. Pendente e
  -- `consumed_at is null and superseded_at is null and expires_at > now()`.
  consumed_at       timestamptz,
  superseded_at     timestamptz
);

create index if not exists pending_signups_email_idx on pending_signups (email);

-- Sem `user_id`, diferente da 007: nao ha conta a que se referir -- e essa e a ideia
-- inteira da fatia. E sem `tenant_id` pela razao da 007, ampliada: as tres rotas sao
-- anonimas E o tenant ainda nao existe.
--
-- Por isso, SEM POLICY e SEM GRANT. Com RLS ligada e nenhum grant, a tabela e
-- inalcancavel pela aplicacao: so as tres frestas `security definer` a tocam.
alter table pending_signups enable row level security;

-- A fresta de PEDIR. Trava o endereco, aplica o freio, insere e substitui as
-- anteriores -- tudo numa chamada.
--
-- Devolve ZERO LINHAS quando freada, e a borda responde 202 do mesmo jeito.
create function auth_request_signup(
  p_organization_name text, p_email text, p_token_hash text,
  p_expires_at timestamptz, p_throttle_seconds integer
)
returns table (email text, already_has_account boolean)
language plpgsql
security definer
set search_path = public
as $$
declare
  v_email text := lower(p_email);
begin
  -- O mutex do freio. A licao da 3d vale -- sob READ COMMITTED, duas transacoes que
  -- avaliam um `not exists` veem cada uma o proprio snapshot e AS DUAS PASSAM --, mas
  -- o MECANISMO dela nao se transporta: la o mutex era a linha do usuario, e aqui a
  -- linha do usuario pode nao existir; o caso normal do cadastro e justamente esse.
  -- Um lock consultivo serializa por endereco sem depender de linha nenhuma, e solta
  -- no fim da transacao. Colisao de hashtext entre enderecos diferentes apenas
  -- serializa dois pedidos sem relacao -- inocuo.
  perform pg_advisory_xact_lock(hashtext(v_email)::bigint);

  insert into pending_signups (organization_name, email, token_hash, expires_at)
  select p_organization_name, v_email, p_token_hash, p_expires_at
  where not exists (
    select 1 from pending_signups p
    where p.email = v_email
      and p.consumed_at is null
      and p.superseded_at is null
      and p.expires_at > now()
      and p.created_at > now() - make_interval(secs => p_throttle_seconds)
  );

  if not found then
    return;  -- freado
  end if;

  -- DEPOIS do insert, nunca antes. Substituir primeiro e ser freado depois deixaria a
  -- pessoa sem link nenhum valido -- pior que qualquer um dos dois estados.
  update pending_signups set superseded_at = now()
  where pending_signups.email = v_email
    and pending_signups.token_hash <> p_token_hash
    and pending_signups.consumed_at is null
    and pending_signups.superseded_at is null;

  -- O booleano NAO chega ao navegador: a resposta HTTP e a mesma dos dois lados, e ele
  -- so escolhe QUAL e-mail a borda manda. A pendencia acima foi inserida de todo jeito,
  -- inclusive para endereco com conta -- e deliberado: sem linha nao ha sobre o que
  -- aplicar o freio, e a rota anonima viraria amplificador de e-mail.
  return query
  select v_email, exists (select 1 from users u where u.email = v_email);
end
$$;

-- A fresta de LEITURA. Carimbos crus sempre que o token existir, nenhuma linha se nao
-- existir. Quem deriva o veredito -- usado, substituido, vencido -- e a borda. Sem
-- `join`: nao ha conta a juntar.
create function auth_pending_signup_by_token_hash(p_token_hash text)
returns table (
  organization_name text, email text, expires_at timestamptz,
  consumed_at timestamptz, superseded_at timestamptz
)
language sql
security definer
set search_path = public
as $$
  select p.organization_name, p.email, p.expires_at, p.consumed_at, p.superseded_at
  from pending_signups p
  where p.token_hash = p_token_hash
$$;

-- A fresta de ESCRITA. Consome a pendencia e cria organizacao + admin num atomo so.
-- Mesmo formato de retorno das outras cinco funcoes que devolvem conta, para o
-- mapeamento linha->Account ser um so.
create function auth_confirm_signup(p_token_hash text, p_password_hash text)
returns table (
  id uuid, tenant_id uuid, email text, password_hash text, role text,
  session_epoch integer
)
language plpgsql
security definer
set search_path = public
as $$
declare
  v_organization_name text;
  v_email             text;
begin
  -- Aqui a licao da 3c VALE: o consumo acontece dentro do update, e um update que
  -- perde a corrida reavalia o `where` na linha travada e nao encontra mais nada. Dois
  -- cliques simultaneos no mesmo link criam UMA organizacao.
  update pending_signups set consumed_at = now()
  where pending_signups.token_hash = p_token_hash
    and pending_signups.consumed_at is null
    and pending_signups.superseded_at is null
    and pending_signups.expires_at > now()
  returning pending_signups.organization_name, pending_signups.email
  into v_organization_name, v_email;

  if v_email is null then
    return;
  end if;

  -- A criacao nao e reescrita: e a mesma funcao da 006, e so muda quem a dispara.
  -- Se o endereco ganhou conta entre o pedido e o clique (alguem aceitou um convite),
  -- o 23505 sobe daqui e desfaz a transacao INTEIRA -- inclusive o consumo acima.
  -- Nada meio-feito, nenhuma organizacao orfa, e a pendencia continua pendente.
  return query
  select * from auth_create_organization_with_admin(
    v_organization_name, v_email, p_password_hash
  );
end
$$;

-- Privilegios. As funcoes nascem com `anon`, `authenticated` e `service_role` na ACL
-- do Supabase, e `revoke from public` NAO os remove, porque o grant nao veio de
-- PUBLIC. Estes grants nominais sao a autorizacao inteira -- e aqui pesam o mesmo que
-- na 007: uma fresta que CRIA CONTA ADMIN alcancavel por `anon` e buraco aberto.
grant execute on function
  auth_request_signup(text, text, text, timestamptz, integer) to sensus_app;
grant execute on function auth_pending_signup_by_token_hash(text) to sensus_app;
grant execute on function auth_confirm_signup(text, text) to sensus_app;

revoke execute on function
  auth_request_signup(text, text, text, timestamptz, integer) from public;
revoke execute on function auth_pending_signup_by_token_hash(text) from public;
revoke execute on function auth_confirm_signup(text, text) from public;

-- Os tres papeis sao do Supabase e podem nao existir num Postgres nu; sem o guarda, a
-- migracao quebraria fora dele.
do $$
declare
  papel text;
  alvo  text;
begin
  foreach papel in array array['anon', 'authenticated', 'service_role'] loop
    if exists (select 1 from pg_roles where rolname = papel) then
      foreach alvo in array array[
        'auth_request_signup(text, text, text, timestamptz, integer)',
        'auth_pending_signup_by_token_hash(text)',
        'auth_confirm_signup(text, text)'
      ] loop
        execute format('revoke execute on function %s from %I', alvo, papel);
      end loop;
    end if;
  end loop;
end
$$;
