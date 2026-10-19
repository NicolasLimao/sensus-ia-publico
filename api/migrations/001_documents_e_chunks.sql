-- Fatia 2a: documentos e chunks, com tudo namespaced por tenant.
create extension if not exists vector;
create extension if not exists "pgcrypto";

create table if not exists documents (
  id            uuid primary key default gen_random_uuid(),
  tenant_id     text not null,
  name          text not null,
  content_type  text not null,
  content_hash  text not null,
  raw_text      text not null,
  created_at    timestamptz not null default now(),
  unique (tenant_id, content_hash)
);

create table if not exists chunks (
  id           uuid primary key default gen_random_uuid(),
  tenant_id    text not null,
  document_id  uuid not null references documents(id) on delete cascade,
  content      text not null,
  chunk_index  int not null,
  embedding    vector(1024) not null,
  tsv          tsvector generated always as (to_tsvector('portuguese', content)) stored,
  created_at   timestamptz not null default now()
);

create index if not exists chunks_embedding_idx on chunks using hnsw (embedding vector_cosine_ops);
create index if not exists chunks_tsv_idx on chunks using gin (tsv);
create index if not exists chunks_tenant_document_idx on chunks (tenant_id, document_id);
create index if not exists documents_tenant_idx on documents (tenant_id);
