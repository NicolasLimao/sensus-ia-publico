-- Fatia 3a: o tenant deixa de ser texto livre e a policy passa a valer.

-- 1. Uma organizacao para cada tenant que ja existe, preservando o nome antigo como
--    slug: `eval-local` continua sendo `eval-local` para o harness, e os chunks ja
--    indexados sobrevivem sem gastar cota de embedding.
insert into organizations (slug, name)
select distinct tenant_id, tenant_id from documents
where tenant_id not in (select slug from organizations)
on conflict (slug) do nothing;

insert into organizations (slug, name)
select distinct tenant_id, tenant_id from chunks
where tenant_id not in (select slug from organizations)
on conflict (slug) do nothing;

-- 2. Reescreve as colunas pelo mapeamento slug -> id.
alter table documents add column tenant_uuid uuid;
alter table chunks    add column tenant_uuid uuid;

update documents d set tenant_uuid = o.id from organizations o where o.slug = d.tenant_id;
update chunks    c set tenant_uuid = o.id from organizations o where o.slug = c.tenant_id;

alter table documents drop constraint if exists documents_tenant_id_content_hash_key;
alter table documents drop column tenant_id;
alter table chunks    drop column tenant_id;
alter table documents rename column tenant_uuid to tenant_id;
alter table chunks    rename column tenant_uuid to tenant_id;

alter table documents alter column tenant_id set not null;
alter table chunks    alter column tenant_id set not null;
alter table documents add constraint documents_tenant_fk
  foreign key (tenant_id) references organizations(id) on delete cascade;
alter table chunks add constraint chunks_tenant_fk
  foreign key (tenant_id) references organizations(id) on delete cascade;
alter table documents add constraint documents_tenant_hash_unique
  unique (tenant_id, content_hash);

create index if not exists documents_tenant_idx on documents (tenant_id);
create index if not exists chunks_tenant_document_idx on chunks (tenant_id, document_id);

-- 3. Papel da aplicacao. SEM bypassrls: o `postgres` de hoje ignora policy, e
--    enquanto a conexao for com ele a RLS e decoracao.
do $$
begin
  if not exists (select 1 from pg_roles where rolname = 'sensus_app') then
    create role sensus_app login;
  end if;
end
$$;

grant usage on schema public to sensus_app;
grant select, insert, update, delete on documents, chunks, users, organizations to sensus_app;
grant execute on function auth_user_by_email(text) to sensus_app;
grant execute on function auth_user_by_id(uuid) to sensus_app;

-- 4. Policies. Duas defesas contra a conexao sem contexto, e as duas sao precisas:
--    o segundo argumento `true` de current_setting faz o GUC NUNCA LIDO virar nulo em
--    vez de erro; e o `nullif(..., '')` cobre o GUC JA LIDO. Depois que uma transacao
--    roda `set_config(..., true)`, o fim da transacao devolve o GUC ao valor anterior,
--    que para um parametro de aplicacao nunca definido antes e STRING VAZIA — nao
--    "ausente". Sem o nullif, a proxima consulta nessa mesma conexao do pool tentaria
--    `''::uuid` e estouraria. Com ele, a comparacao vira nula e o resultado e ZERO
--    linhas, nunca as linhas de todo mundo. A falha e fechada.
alter table documents enable row level security;
alter table chunks    enable row level security;
alter table users     enable row level security;

create policy documents_por_tenant on documents
  using (tenant_id = nullif(current_setting('sensus.tenant_id', true), '')::uuid)
  with check (tenant_id = nullif(current_setting('sensus.tenant_id', true), '')::uuid);

create policy chunks_por_tenant on chunks
  using (tenant_id = nullif(current_setting('sensus.tenant_id', true), '')::uuid)
  with check (tenant_id = nullif(current_setting('sensus.tenant_id', true), '')::uuid);

create policy users_por_tenant on users
  using (tenant_id = nullif(current_setting('sensus.tenant_id', true), '')::uuid)
  with check (tenant_id = nullif(current_setting('sensus.tenant_id', true), '')::uuid);
