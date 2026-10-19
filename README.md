# Sensus IA

> Assistente de IA que **entende os seus documentos**. Você traz a sua base (manuais, políticas, docs de API, PDFs) e o Sensus responde perguntas com respostas rastreáveis à fonte — por chat, sem alucinar o que não está na base.

*Sensus* (latim: sentido, percepção, entendimento) — o produto dá **sentido** a bases de conhecimento.

> Projeto em desenvolvimento. A implementação inclui API, chat web, ingestão, autenticação e configuração de IA; testes de integração real e avaliação de qualidade são etapas separadas. A visão inicial abaixo registra a evolução do produto.

---

## O problema

Toda empresa acumula conhecimento em documentos que ninguém acha na hora certa. Buscar em manuais, PDFs e docs de API é lento, e ferramentas de busca por palavra-chave não entendem a intenção. O resultado é tempo perdido e respostas inconsistentes.

## A proposta

Um produto RAG **genérico e multi-tenant**: cada cliente sobe sua própria base, e o Sensus vira um especialista naquele conteúdo — acessível por um **chat web** (e, futuramente, outros canais). Respostas fundamentadas, com citação da fonte, e um painel para curar a base e medir a qualidade.

---

## Fundação de engenharia

O Sensus é construído sobre padrões de RAG já comprovados em produção (num projeto anterior), reconstruídos aqui de forma **genérica e independente de qualquer cliente**:

- **Pipeline RAG** — busca híbrida (semântica + full-text), reescrita de query, reranking, memória multi-turno.
- **Chunking por tipo de documento** — contextual para prosa, determinístico para dados estruturados; fonte crua durável para re-chunkar sem reprocessar.
- **Avaliação mensurável** — harness com juiz LLM e detecção de instabilidade: nada vai a produção sem medição.
- **Segurança por padrão** — isolamento de dados por tenant, pseudonimização de PII, segredos fora do build.

---

## Visão de produto (rascunho)

- **Traga seus documentos** — upload + ingestão por tenant (PDF, texto, docs de API).
- **Chat web** — conversa com histórico e citação de fontes. *(primeira fatia)*
- **Multi-tenant** — bases isoladas por cliente.
- **Config por tenant** — prompt/modelo/parâmetros versionados, com sandbox de teste antes de publicar.
- **Painel de curadoria** — métricas de feedback, gaps da base, controle de qualidade.
- *(depois)* contas/billing, limites de uso, outros canais (Slack/Teams/API).

---

## Roadmap (fatias)

1. **Front-end de chat** — a peça que dá cara de produto, sobre a API RAG. *(próxima)*
2. Ingestão "traga seus docs" por tenant.
3. Multi-tenancy + auth próprios.
4. Config de IA versionada por tenant.
5. Curadoria + billing.

Cada fatia entra pelo ciclo: desenho → spec → implementação com testes.

---

## Desenvolvimento

Duas peças sobem juntas. O front chama `/api` no mesmo origin e o Vite faz proxy para o adaptador — assim o cookie httpOnly de sessão viaja sem CORS.

```bash
# Terminal 1 — adaptador (porta 8001)
cd api
cp .env.example .env        # preencha SENSUS_SESSION_SECRET
uv run uvicorn --factory sensus_api.app:create_app --reload --port 8001

# Terminal 2 — front (porta 5173)
cd web
npm run dev
```

Sem banco e sem chaves, o adaptador sobe com o pipeline em memória e o diretório de
usuários em memória — dá para criar uma organização, entrar e conversar, mas nada
persiste entre reinícios. Ver "Pipeline de RAG" abaixo para o modo real.

### Qualidade

```bash
cd api && uv run pytest && uv run ruff check . && uv run mypy
cd web && npm test && npm run typecheck
```

O teste de contrato (`-m contract`) fica de fora por padrão; ele roda contra os provedores
reais e é o que impede o duplo de divergir em silêncio. O de integração (`-m integration`)
também fica de fora e exige o Postgres — os dois leem chave e URL das MESMAS settings da
aplicação (o `api/.env`), então não é preciso exportar nada.

### Pipeline de RAG (fatia 2a)

Sem `SENSUS_DATABASE_URL` e `SENSUS_VOYAGE_API_KEY` a aplicação sobe com o pipeline em
memória: a base não persiste e os embeddings são falsos. Serve para mexer no front.

Para rodar de verdade:

```bash
export SENSUS_DATABASE_URL=...      # Postgres do Supabase, papel administrativo
export SENSUS_VOYAGE_API_KEY=...
uv run python -m sensus_rag.migrate
uv run uvicorn sensus_api.app:create_app --factory --reload
```

**Passo de operação depois da migração 003 — a senha do papel da aplicação.** A migração
cria o papel `sensus_app` **sem senha**: senha não entra em SQL versionado. Defina-a e
aponte a aplicação para ela:

```sql
alter role sensus_app password 'uma-senha-forte';
```

```bash
# api/.env — gitignored, nunca versionado
SENSUS_APP_DATABASE_URL=postgresql://sensus_app:uma-senha-forte@HOST:5432/postgres
```

No pooler do Supabase o usuário leva o sufixo do projeto: `sensus_app.<project-ref>`.

Isto **não é opcional para valer o isolamento**: `SENSUS_DATABASE_URL` costuma ser o papel
`postgres`, que ignora policy — conectando com ele, a RLS da migração 003 é decoração. A
aplicação prefere `SENSUS_APP_DATABASE_URL` e só cai para a outra se ela faltar.

**Variáveis de identidade e sessão:**

| Variável | Default | Uso |
|---|---|---|
| `SENSUS_SESSION_SECRET` | — (obrigatória) | assina o cookie e o token de sessão |
| `SENSUS_SESSION_MAX_AGE_SECONDS` | `604800` (7 dias) | prazo do **token**, não só do cookie |
| `SENSUS_SESSION_COOKIE_SECURE` | `true` | `Secure` no cookie; **`false` só no dev local**, que não tem TLS |
| `SENSUS_APP_DATABASE_URL` | vazio | conexão com o papel `sensus_app`, sem `BYPASSRLS` |
| `SENSUS_INVITE_MAX_AGE_SECONDS` | `604800` (7 dias) | prazo do convite; `expires_at` é calculado na criação |
| `SENSUS_SMTP_HOST` | vazio | **vazio manda o e-mail para o log**, com link e tudo |
| `SENSUS_SMTP_PORT` | `587` | |
| `SENSUS_SMTP_USERNAME` / `_PASSWORD` | vazio | vazio = relay sem autenticação |
| `SENSUS_SMTP_STARTTLS` | `true` | |
| `SENSUS_EMAIL_FROM` | vazio | **obrigatório** quando há `SENSUS_SMTP_HOST` |
| `SENSUS_PUBLIC_BASE_URL` | vazio | de onde saem os links dos e-mails; **obrigatório** com SMTP |
| `SENSUS_PASSWORD_RESET_MAX_AGE_SECONDS` | `3600` (1 hora) | prazo do link de reset |
| `SENSUS_PASSWORD_RESET_THROTTLE_SECONDS` | `120` | pedido novo dentro da janela não gera e-mail |

**Convites (fatias 3c e 3e).** `POST /auth/signup` cria uma organização **nova**. Para
trazer mais gente para uma que já existe, quem é `admin` convida por e-mail:

1. No chat, botão **Convidar** → digite o **endereço** → **Enviar convite**. O link **não
   aparece na tela**: ele vai por e-mail, e só existe dentro daquela caixa de entrada.
2. O convite vale **7 dias**, serve **uma vez** e pode ser revogado antes do uso.
   Convidar o mesmo endereço de novo **substitui** o convite pendente anterior.
3. Quem recebe abre o link (`/convite#<token>`), vê para qual organização e **para qual
   endereço** foi convidado, escolhe só a senha, e entra como `member`.

O token viaja no **fragmento** da URL (depois do `#`), que o navegador não envia a
servidor nenhum, e no **corpo** do POST — nunca na query. Desde a 3e é o link existir
**somente** na caixa de entrada que faz o convite provar posse do endereço: o aceite cria
a conta com o e-mail **do convite**, e não há campo por onde outro entraria. Convidar quem
já faz parte da organização responde **409** sem mandar nada.

**Convidar depende de SMTP configurado.** Sem `SENSUS_SMTP_HOST` o link vai para o log do
servidor, o que serve em desenvolvimento (copie do terminal e abra); em produção seria
falha silenciosa — por isso a aplicação avisa ao subir. Se o envio falhar, a resposta é
**502** e o convite é revogado: ninguém recebeu nada.

As **migrações 006 e 008** são obrigatórias para isto funcionar.

**Reset de senha (fatia 3d).** Na tela de entrada, **Esqueci minha senha** → e-mail → o
link chega por e-mail e leva a `/redefinir#<token>`. O link vale **1 hora**, serve **uma
vez**, e redefinir **derruba todas as sessões abertas** (inclusive a de quem tivesse
tomado a conta). Um pedido novo substitui o anterior. A **migração 007** é obrigatória.

Sem `SENSUS_SMTP_HOST`, **o e-mail vai para o log do servidor** — corpo inteiro, link
incluído. É assim que se testa o fluxo localmente: peça o link, copie do terminal, abra.
A aplicação avisa no log ao subir que está nesse modo.

A resposta de `POST /auth/forgot-password` é **202 em todos os casos** — e-mail com conta,
sem conta ou pedido freado. É de propósito: distinguir entregaria quais endereços têm
conta. Não espere a tela dizer se o e-mail existe.

`/convite` e `/redefinir` são rotas do SPA, não arquivos: o `vite dev` já devolve o
`index.html` para elas, mas **quem servir o front construído precisa do mesmo fallback**
(qualquer caminho → `index.html`). Sem isso, o link chega como 404 para quem clicar — e o
sintoma aparece só em produção.

`SENSUS_TENANT_ID` **deixou de existir** na fatia 3a: o tenant vem da conta de quem está
logado, não da configuração.

**Rerank (opcional):**

| Variável | Default | Uso |
|---|---|---|
| `SENSUS_RERANK_MODEL` | `rerank-2.5` | modelo de rerank; **vazio desliga** |
| `SENSUS_RERANK_CANDIDATES` | `20` | quantos candidatos vão ao reranker |
| `SENSUS_RERANK_MAX_REQUEST_TOKENS` | `8000` | teto por requisição |
| `SENSUS_RERANK_MAX_DOCUMENT_CHARS` | `0` | trunca o texto enviado (chunk devolvido íntegro) |

Subir um documento e buscar. O tenant vem da identidade de quem está logado, não de
cabeçalho: é preciso ter uma sessão, e subir documento exige papel `admin`.

```bash
# 1. Criar organização (o primeiro usuário é o admin dela) e guardar o cookie
curl -c cookies.txt -X POST -H "Content-Type: application/json" \
  -d '{"organization_name":"Acme","email":"ana@acme.com","password":"senha-bem-longa"}' \
  http://localhost:8000/api/v1/auth/signup

# 2. Usar a sessão
curl -b cookies.txt -F file=@manual.pdf http://localhost:8000/api/v1/documents
curl -b cookies.txt -X POST -H "Content-Type: application/json" \
  -d '{"question":"qual o prazo?","top_k":5}' http://localhost:8000/api/v1/search
```

Em `http://localhost` isto só funciona com `SENSUS_SESSION_COOKIE_SECURE=false`: o
default do cookie de sessão é `Secure`, e sem TLS o cliente o descarta.

Testes de integração (exigem o banco):

```bash
uv run pytest -m integration -v
```

Os testes de RLS (`tests/rag/test_rls.py`) exigem também `SENSUS_APP_DATABASE_URL` — sem o
papel restrito não há o que verificar, e eles pulam.

**Limitação conhecida no Windows.** `psycopg` em modo assíncrono não roda sob o
`ProactorEventLoop`, que é o padrão do asyncio no Windows. O sintoma é sempre o mesmo:

```
psycopg.InterfaceError: Psycopg cannot use the 'ProactorEventLoop' to run in async mode
```

Dois dos três processos que tocam o Postgres já contornam isso trocando a política para
`WindowsSelectorEventLoopPolicy` antes de abrir qualquer conexão:

- `python -m sensus_rag.migrate` — a troca está no próprio módulo, que é um ponto de
  entrada de linha de comando: quem o chama é o interpretador, não outro módulo, então
  mexer na política global ali é seguro. **Roda no Windows.**
- os testes de integração — a troca está em `api/tests/conftest.py`, decisão de processo
  de teste. **Rodam no Windows.**

O que **não** foi verificado é o terceiro: `uvicorn` servindo uma rota que toque o banco.
O `uvicorn` monta o próprio event loop, e código de biblioteca (`sensus_api`) não deve
impor uma política global de asyncio ao processo de quem o hospeda. O contorno provável
é subir o `uvicorn` por um script de entrada que troque a policy antes, ou rodar dentro
do WSL — nenhum dos dois foi exercitado nesta fatia. Fica como risco aberto, registrado
no SDD.

---

<sub>Projeto pessoal e independente. Sem vínculo, dados, marca ou credenciais de qualquer empregador anterior — construído do zero como produto genérico.</sub>
