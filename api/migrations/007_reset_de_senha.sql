-- Fatia 3d: reset de senha.
--
-- Ate aqui quem esquecia a senha dependia de intervencao manual no banco. Este e o
-- caminho de volta, e ele traz junto o primeiro provedor de e-mail do produto.
create table if not exists password_resets (
  id            uuid primary key default gen_random_uuid(),
  user_id       uuid not null references users(id) on delete cascade,
  -- sha256 do token, nunca o token. Unico GLOBAL: quem chega com o link nao tem tenant
  -- a informar, e a busca e so pelo hash.
  token_hash    text not null unique,
  created_at    timestamptz not null default now(),
  expires_at    timestamptz not null,
  -- Sem coluna `status`, pela mesma razao da 006: um status gravado pode discordar dos
  -- carimbos, e ai ha duas verdades sobre o mesmo link. Pendente e
  -- `used_at is null and superseded_at is null and expires_at > now()`.
  used_at       timestamptz,
  superseded_at timestamptz
);

create index if not exists password_resets_user_idx on password_resets (user_id);

-- Sem `tenant_id`: o tenant de um reset e o do `user_id`, e NENHUMA rota desta fatia
-- tem tenant -- as tres sao anonimas. Uma coluna que ninguem consulta existiria so
-- para a tabela parecer com as outras.
--
-- E, pela mesma razao, SEM POLICY e SEM GRANT. Na 006 havia um lado com tenant (o
-- painel do admin) e era ele que a policy protegia; aqui nao ha esse lado. Uma policy
-- que nunca casa com ninguem pareceria protecao sem ter teste que a exercitasse. Com
-- RLS ligada e nenhum grant, a tabela e inalcancavel pela aplicacao: so as tres
-- frestas `security definer` abaixo a tocam.
alter table password_resets enable row level security;

-- A fresta de PEDIR. Resolve o e-mail, aplica o freio, insere e substitui os
-- anteriores -- tudo numa chamada.
--
-- Devolve ZERO LINHAS em dois casos que a borda nao distingue: nao ha conta com aquele
-- e-mail, ou ja houve pedido dentro da janela. Os dois respondem 202.
create function auth_request_password_reset(
  p_email text, p_token_hash text, p_expires_at timestamptz, p_throttle_seconds integer
)
returns table (user_id uuid, email text)
language plpgsql
security definer
set search_path = public
as $$
declare
  v_user_id uuid;
  v_email   text;
begin
  -- O `for update` E o freio. A licao da 3c -- "faca o trabalho dentro do update" --
  -- NAO se transporta para o insert: sob READ COMMITTED, um update que perde a corrida
  -- reavalia seu `where` na linha ja travada, mas duas transacoes que avaliam um
  -- `not exists` veem cada uma o proprio snapshot, nenhuma enxerga o insert da outra,
  -- e AS DUAS PASSAM. O freio sairia quebrado no unico caso para o qual existe.
  --
  -- A linha do usuario e o mutex, e ela ja existe: nada de tabela nem de lock novo. O
  -- segundo pedido simultaneo espera, e quando entra ja ENXERGA o pendente recem
  -- criado. A contencao e por conta e dura o que dura a transacao.
  select u.id, u.email into v_user_id, v_email
  from users u where u.email = lower(p_email)
  for update;

  if v_user_id is null then
    return;
  end if;

  insert into password_resets (user_id, token_hash, expires_at)
  select v_user_id, p_token_hash, p_expires_at
  where not exists (
    select 1 from password_resets r
    where r.user_id = v_user_id
      and r.used_at is null
      and r.superseded_at is null
      and r.expires_at > now()
      and r.created_at > now() - make_interval(secs => p_throttle_seconds)
  );

  if not found then
    return;  -- freado: mesma resposta do caso sem conta
  end if;

  -- DEPOIS do insert, nunca antes. Substituir primeiro e ser freado depois deixaria a
  -- pessoa sem link nenhum valido -- pior que qualquer um dos dois estados.
  update password_resets set superseded_at = now()
  where password_resets.user_id = v_user_id
    and password_resets.token_hash <> p_token_hash
    and password_resets.used_at is null
    and password_resets.superseded_at is null;

  return query select v_user_id, v_email;
end
$$;

-- A fresta de LEITURA. Devolve os carimbos crus sempre que o token existir, e nenhuma
-- linha se nao existir. Quem deriva o veredito -- usado, substituido, vencido -- e a
-- borda: uma funcao que devolvesse so "vale ou nao" deixaria a tela sem como dizer o
-- que fazer em seguida.
create function auth_password_reset_by_token_hash(p_token_hash text)
returns table (
  email text, expires_at timestamptz, used_at timestamptz, superseded_at timestamptz
)
language sql
security definer
set search_path = public
as $$
  select u.email, r.expires_at, r.used_at, r.superseded_at
  from password_resets r
  join users u on u.id = r.user_id
  where r.token_hash = p_token_hash
$$;

-- A fresta de ESCRITA. Consome o link, grava a senha nova e incrementa o
-- `session_epoch` -- os tres num atomo so. Mesmo formato de retorno das outras quatro
-- funcoes que devolvem conta, para o mapeamento linha->Account ser um so.
create function auth_consume_password_reset(p_token_hash text, p_password_hash text)
returns table (
  id uuid, tenant_id uuid, email text, password_hash text, role text,
  session_epoch integer
)
language plpgsql
security definer
set search_path = public
as $$
declare
  v_user_id uuid;
begin
  -- Aqui a licao da 3c VALE: o consumo acontece dentro do update, e um update que
  -- perde a corrida reavalia o `where` na linha travada e nao encontra mais nada.
  -- Dois cliques simultaneos no mesmo link: um redefine, o outro nao consome.
  update password_resets set used_at = now()
  where token_hash = p_token_hash
    and used_at is null
    and superseded_at is null
    and expires_at > now()
  returning password_resets.user_id into v_user_id;

  if v_user_id is null then
    return;
  end if;

  -- O incremento do session_epoch derruba TODA sessao aberta -- inclusive a de quem
  -- tomou a conta, que e o caso em que a pessoa usa o reset. A conta devolvida ja traz
  -- a geracao nova, e e com ela que a borda assina o cookie.
  return query
  update users u
  set password_hash = p_password_hash,
      session_epoch = u.session_epoch + 1
  where u.id = v_user_id
  returning u.id, u.tenant_id, u.email, u.password_hash, u.role, u.session_epoch;
end
$$;

-- Privilegios. As funcoes nascem com `anon`, `authenticated` e `service_role` na ACL
-- do Supabase, e `revoke from public` NAO os remove, porque o grant nao veio de
-- PUBLIC. Estes grants nominais sao a autorizacao inteira -- e aqui pesam mais que na
-- 006: uma fresta que TROCA SENHA alcancavel por `anon` e conta de qualquer pessoa.
grant execute on function
  auth_request_password_reset(text, text, timestamptz, integer) to sensus_app;
grant execute on function auth_password_reset_by_token_hash(text) to sensus_app;
grant execute on function auth_consume_password_reset(text, text) to sensus_app;

revoke execute on function
  auth_request_password_reset(text, text, timestamptz, integer) from public;
revoke execute on function auth_password_reset_by_token_hash(text) from public;
revoke execute on function auth_consume_password_reset(text, text) from public;

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
        'auth_request_password_reset(text, text, timestamptz, integer)',
        'auth_password_reset_by_token_hash(text)',
        'auth_consume_password_reset(text, text)'
      ] loop
        execute format('revoke execute on function %s from %I', alvo, papel);
      end loop;
    end if;
  end loop;
end
$$;
