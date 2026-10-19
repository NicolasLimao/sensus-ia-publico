-- Fatia 4a: config de IA versionada por tenant.
--
-- Ate aqui tudo que decide COMO o produto responde era global e de processo: o system
-- prompt era constante de modulo, e modelo, top_k, teto de contexto e sugestoes saiam
-- de variaveis de ambiente lidas uma vez no build_rag. Dois clientes com bases e
-- publicos diferentes recebiam o mesmo comportamento, e mudar o de um exigia deploy.
create table if not exists ai_configs (
  id                uuid primary key default gen_random_uuid(),
  tenant_id         uuid not null references organizations(id) on delete cascade,
  -- Por TENANT, nao global: o admin le "versao 3" como a terceira da organizacao dele.
  version           integer not null,
  briefing          text not null default '',
  generation_model  text not null,
  generation_top_k  integer not null,
  max_context_chars integer not null,
  suggestions       text[] not null default '{}',
  created_at        timestamptz not null default now(),
  -- `set null`, nao `cascade`: apagar a pessoa nao pode apagar a historia da
  -- configuracao. A versao continua valendo, e "quem criou" passa a ser desconhecido,
  -- que e a verdade.
  created_by        uuid references users(id) on delete set null,
  -- Booleano, e nao carimbo derivado como em invitations/password_resets/pending_signups.
  -- Aquelas derivam estado de carimbos porque os estados sao TERMINAIS (usado, revogado,
  -- vencido). "Ativa" nao e terminal: vai e volta no rollback, e um carimbo
  -- `activated_at` obrigaria toda leitura a calcular "a de maior carimbo", que e uma
  -- invariante mais fraca do que a que o indice abaixo garante.
  is_active         boolean not null default false,
  unique (tenant_id, version)
);

create index if not exists ai_configs_tenant_idx on ai_configs (tenant_id);

-- UMA ativa por tenant, garantida pelo BANCO: se algum caminho futuro ativar sem
-- desativar, o update falha em vez de criar a segunda verdade em silencio.
--
-- Consequencia que custou uma sondagem: a troca NAO pode ser um `update` unico
-- (`set is_active = (id = %s)`). Este indice e parcial e e verificado linha a linha, e
-- se a linha a ativar vier antes da que esta ativa, as duas ficam `true` por um
-- instante e o comando estoura. O adaptador troca em dois comandos dentro da mesma
-- transacao -- o intervalo sem nenhuma ativa e invisivel de fora, por MVCC.
create unique index if not exists ai_configs_uma_ativa_por_tenant
  on ai_configs (tenant_id) where is_active;

grant select, insert, update on ai_configs to sensus_app;

-- Sem `delete`: versoes sao imutaveis e nao sao apagadas. Rollback e ativar uma
-- anterior, e o historico e o que da sentido a isso. Nao conceder o privilegio e mais
-- barato que confiar em ninguem escrever o delete.

-- Mesmo desenho das policies da 003 e da 006, inclusive as duas defesas contra conexao
-- sem contexto: o segundo argumento `true` de current_setting e o `nullif(..., '')`.
--
-- NAO ha funcao `security definer` nesta migracao, e isso e a diferenca em relacao a
-- 006/007/009: ali havia caminhos sem tenant (o convidado, quem esqueceu a senha, quem
-- se cadastra). Aqui TODO caminho tem tenant -- o admin editando e o chat resolvendo --
-- e e exatamente a situacao para a qual a policy existe.
alter table ai_configs enable row level security;

create policy ai_configs_por_tenant on ai_configs
  using (tenant_id = nullif(current_setting('sensus.tenant_id', true), '')::uuid)
  with check (tenant_id = nullif(current_setting('sensus.tenant_id', true), '')::uuid);
