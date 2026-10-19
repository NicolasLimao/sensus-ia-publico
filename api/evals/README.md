# Harness de medição

Mede a qualidade da recuperação e da recusa da geração.

## Para que serve, já que ainda não há cliente

Este corpus **não** é o de nenhum cliente, e não pretende ser. Ele responde a uma
pergunta diferente da que o cliente faz:

- **O cliente pergunta:** "o Sensus está bom para a MINHA base?" — isso se mede no
  onboarding, com os documentos e as perguntas reais dele.
- **Este corpus pergunta:** "eu piorei o motor?" — mudar chunking, trocar o modelo de
  embedding, mexer na normalização ou acrescentar rerank pode degradar a recuperação, e
  sem uma referência fixa a degradação só aparece quando um cliente reclama.

É um **proxy**: documentos públicos com a mesma forma do que um cliente traria — seções,
prazos, números, listas, termos definidos. Vale como **detector de regressão**, não como
nota absoluta de qualidade.

O mesmo harness roda depois por tenant, com o dataset do cliente. Muda o dataset, não a
máquina.

## Corpus

Legislação federal brasileira. A escolha de licença é deliberada: a **Lei nº 9.610/1998,
art. 8º, inciso IV**, diz que leis, decretos, regulamentos e demais atos oficiais **não
são objeto de proteção como direitos autorais**. Não há zona cinzenta.

Os três se sobrepõem no vocabulário — todos falam de "prazo", "dados",
"responsabilidade". Isso é proposital: boa parte das perguntas só acerta se a
recuperação escolher o **documento** certo, não apenas o trecho com as palavras certas.

| Arquivo | Origem | Licença |
|---|---|---|
| `cdc.md` | Lei nº 8.078/1990 (Código de Defesa do Consumidor) — `planalto.gov.br/ccivil_03/leis/l8078compilado.htm` | Domínio público (Lei 9.610/98, art. 8º, IV) |
| `lgpd.md` | Lei nº 13.709/2018 (LGPD) — `planalto.gov.br/ccivil_03/_ato2015-2018/2018/lei/l13709.htm` | Domínio público (Lei 9.610/98, art. 8º, IV) |
| `marcocivil.md` | Lei nº 12.965/2014 (Marco Civil da Internet) — `planalto.gov.br/ccivil_03/_ato2011-2014/2014/lei/l12965.htm` | Domínio público (Lei 9.610/98, art. 8º, IV) |

**Como foram preparados:** HTML do Planalto convertido para Markdown — tags removidas,
entidades decodificadas, um parágrafo por artigo/inciso, e as anotações de alteração
legislativa do Planalto ("(Redação dada pela Lei nº …)") retiradas por serem metadado do
site, não texto da lei. Os `.md` são **versionados** de propósito: linha de base só é
comparável entre rodadas se o corpus for exatamente o mesmo.

**Guardrail do projeto:** nada de origem de cliente ou empregador anterior. Ao
acrescentar arquivo, anote origem e licença na tabela acima.

## Dataset

`dataset.yaml`, 26 perguntas escritas à mão, com dois tipos misturados em proporção
parecida: **literais** (usam as palavras do documento, exercitam a perna lexical) e
**parafraseadas** (mesma resposta, outras palavras, exercitam a semântica). Medir só com
literais esconde uma perna semântica quebrada, e vice-versa.

A ancoragem é por trecho de texto, não por id de chunk — assim o dataset sobrevive a
mudanças de chunking, que é exatamente o que vamos variar.

`tests/rag/test_evals_dataset.py` roda na suíte padrão e verifica que **toda âncora é
alcançável**: existe no documento e cabe inteira num chunk, na configuração default. Uma
âncora partida entre dois chunks é uma pergunta que nenhuma recuperação acerta — nem uma
perfeita — e rebaixaria a linha de base em silêncio.

### Viés conhecido

As perguntas foram redigidas por quem também escreveu o sistema, a partir do texto cru
(não dos chunks que o código produz). Ainda assim há viés: quem conhece a recuperação
tende a formular do jeito que ela lida bem. Por isso a linha de base vale como
**referência para comparação entre rodadas**, não como nota absoluta.

### Falso positivo, agora medido

O `dataset.yaml` só expressa perguntas **com** resposta na base, porque `load_dataset`
exige `trecho_esperado` e `recall_at_k` contaria uma pergunta sem resposta como erro de
recuperação — rebaixando a métrica sem que nada estivesse errado.

Por isso o falso positivo tem dataset e métricas próprios. O dataset está descrito na
seção **Dataset de perguntas sem resposta**, abaixo. As métricas são duas, e elas **só
valem em par**:

| Métrica | Sobre | Ideal | O erro que expõe |
|---|---|---|---|
| `taxa_de_recusa` | as 12 sem resposta | 1,000 | **Falso positivo** — o sistema inventou |
| `taxa_de_resposta` | as 26 com resposta | 1,000 | **Falso negativo** — recusou tendo a resposta |

Sem a segunda, a primeira se engana sozinha: um sistema que recusasse tudo teria falso
positivo perfeito. As duas saem com a flag `--generation` (ver **Rodar**), desligada por
padrão porque gasta chamadas do provedor de geração.

## Dataset de perguntas sem resposta

`dataset_sem_resposta.yaml`, 12 perguntas, é o instrumento que fecha a pendência acima:
mede falso positivo, não recall. A resposta certa para toda entrada é a recusa.

Duas famílias, medidas em separado — reportá-las juntas esconderia a primeira atrás da
segunda:

- **`ausente_no_dominio`** (8 perguntas) — plausível sobre consumidor, dados ou
  responsabilidade, mas a resposta não está nestas três leis. É o caso **perigoso**: a
  busca traz trechos de score alto, sobre o tema certo, que não respondem a pergunta.
- **`fora_do_dominio`** (4 perguntas) — sem relação nenhuma com o corpus. Caso fácil;
  serve de piso.

`load_unanswerable` (`sensus_rag/evals/dataset.py`) exige `tipo` em `UNANSWERED_TYPES` e
não aceita entrada sem `pergunta` ou `tipo` — as mesmas mensagens de erro e o mesmo
formato de validação de `load_dataset`, de propósito.

### Como as perguntas sem resposta foram verificadas

Cada uma das 8 perguntas de `ausente_no_dominio` foi checada no corpus antes de entrar
no dataset: `grep -rin "<termo>" evals/corpus/`, seguido da leitura de todo trecho
encontrado. Critério de decisão: o corpus **responde** a pergunta (mesmo que por
exclusão/negação implícita de um artigo), ou só **menciona o tema**? Responder
desqualifica a pergunta — inverteria o sinal da métrica, contando uma recusa correta
como erro.

| Pergunta (no dataset) | Termo(s) buscado(s) | O que o corpus tem | Conclusão |
|---|---|---|---|
| "Qual é a garantia legal para produtos usados comprados de pessoa física?" | `usado`, `usados` | Só ocorrência irrelevante: Art. 70 CDC fala de "peça ou componentes de reposição usados" (contexto de reparo, não de compra de produto usado entre particulares). Nenhuma menção a garantia para produto usado nem a transação entre pessoas físicas (fora do escopo de "fornecedor" do CDC). | Mantida — tema nem mencionado. |
| "Quanto custa registrar uma reclamação no Procon?" | `procon` | Zero ocorrências nas três leis. | Mantida — sem menção. |
| "Qual o prazo para o fornecedor responder a uma reclamação aberta no Procon?" | `procon` | Zero ocorrências (mesma busca acima). | Mantida — sem menção. |
| "O CDC obriga o fornecedor a dar desconto para pagamento à vista, em dinheiro?" | `desconto`, `à vista` | Zero ocorrências nas três leis. | **Pergunta trocada** — ver nota abaixo. |
| "Empresa com menos de dez funcionários é obrigada a nomear encarregado de dados?" | `encarregado` | Art. 41 LGPD: "O controlador deverá indicar encarregado..."; § 3º: "A autoridade nacional **poderá** estabelecer normas complementares... inclusive hipóteses de **dispensa** da necessidade de sua indicação, conforme a natureza e o porte da entidade...". A lei delega a decisão à ANPD; não define limiar de porte nem cita "dez funcionários". | Mantida — tema mencionado, pergunta específica (dispensa para empresa pequena) não respondida. |
| "Qual o formato do relatório de impacto que a ANPD exige que seja enviado?" | `relatório de impacto` | Art. 5º XVII LGPD define o conceito. Art. 38, caput: a autoridade nacional pode exigir o relatório "nos termos de **regulamento**" — não define formato. Art. 38, parágrafo único: lista **conteúdo mínimo** ("descrição dos tipos de dados coletados, a metodologia... e a análise do controlador"), não formato de envio. | Mantida — tema mencionado (caput e parágrafo único), formato não definido no texto legal. |
| "Em quantos dias a ANPD precisa responder a uma denúncia de titular?" | `denúncia`, `denuncia` | Zero ocorrências em toda a LGPD — a palavra não aparece. | Mantida — sem menção. |
| "Qual a velocidade mínima de internet que o provedor é obrigado a entregar?" | `velocidade` | Zero ocorrências no Marco Civil — a lei trata de neutralidade de rede e princípios, não de regulação técnica de qualidade de serviço de telecom. | Mantida — sem menção. |

**Pergunta trocada:** a candidata original para o item 4 era "Comprei numa loja física e
me arrependi. Posso devolver por arrependimento?". `grep -rin "arrepend" evals/corpus/`
encontrou o Art. 49 do CDC:

> "O consumidor pode desistir do contrato, no prazo de 7 dias a contar de sua assinatura
> ou do ato de recebimento do produto ou serviço, **sempre que a contratação de
> fornecimento de produtos e serviços ocorrer fora do estabelecimento comercial**,
> especialmente por telefone ou a domicílio."

O artigo escopa o direito de arrependimento a compras **fora** do estabelecimento
comercial. Compra em loja física é o caso contrário — o texto **responde a pergunta por
exclusão**: não, o CDC não dá direito de arrependimento para compra em loja física. Um
sistema de RAG que recuperasse este artigo e respondesse "não" estaria correto e
fundamentado, não alucinando; contá-lo como falso positivo inverteria o sinal da
métrica. Por isso a pergunta foi trocada por "O CDC obriga o fornecedor a dar desconto
para pagamento à vista, em dinheiro?", verificada na tabela acima.

As 4 perguntas de `fora_do_dominio` (capital da Mongólia, receita de risoto, Copa do
Mundo de 2002, lista vs. tupla em Python) não passam por esta verificação: nenhuma tem
qualquer relação com direito do consumidor, proteção de dados ou marco civil da
internet.

## Linha de base registrada

Primeira medição em **2026-09-01**: `recall@1 0,654` · `recall@3 0,962` · `recall@5 0,962`
· `recall@10 1,000` · `MRR 0,801`, com `voyage-4-large` (1024d), `chunk_target_chars=1200`,
`chunk_overlap_chars=150`, `rrf_k=60` e 40 candidatos por perna.

Os números, como lê-los e os limites deles estão no **[SDD](../../docs/SDD.md)**, seção
"Linha de base de medição" — é lá que a referência vive, e é lá que a próxima rodada
entra para comparação.

## Cota do provedor

A conta gratuita da Voyage, **sem método de pagamento**, fica em **3 RPM e 10K TPM**. Nela
o cliente precisa *espaçar* as requisições: retentar depois do `429` não basta, porque a
requisição recusada também consome a cota. Configure antes de rodar:

```
SENSUS_EMBEDDING_REQUESTS_PER_MINUTE=3
SENSUS_EMBEDDING_TOKENS_PER_MINUTE=9500
SENSUS_EMBEDDING_MAX_BATCH_TOKENS=7000
```

Com essas margens a rodada leva cerca de 20 minutos: a indexação é limitada por tokens
por minuto, e as 26 perguntas, por requisições por minuto. Numa chave com cota padrão,
deixe as três em `0` (ou fora do ambiente) — aí não há espaçamento nenhum.

O espaçamento muda o **tempo** da rodada, nunca o resultado: o limitador só adia
requisições, não altera o que é enviado nem o que volta.

## Rodar

```bash
export SENSUS_DATABASE_URL=...
export SENSUS_VOYAGE_API_KEY=...
uv run python -m sensus_rag.migrate
uv run python -m sensus_rag.evals.runner --tenant eval-local
```

No PowerShell, o `.env` não basta — o runner lê `os.environ`:

```powershell
$line = Get-Content .env | Where-Object { $_ -match '^\s*SENSUS_DATABASE_URL\s*=' } | Select-Object -First 1
$env:SENSUS_DATABASE_URL = ($line -split '=', 2)[1].Trim()
```

Cada rodada indexa o corpus (idempotente por hash) e imprime recall@1/3/5/10 e MRR,
junto da configuração que os produziu. **Anote a linha de base no SDD** — é contra ela
que chunking, RRF e, depois, rerank serão comparados.

Para varrer uma configuração, mude a variável de ambiente e rode de novo, por exemplo
`SENSUS_CHUNK_TARGET_CHARS=600`.

### Rerank (opcional)

| Variável | Default | Para que serve |
|---|---|---|
| `SENSUS_RERANK_MODEL` | `rerank-2.5` | modelo de rerank; **vazio desliga**. A chave é a mesma do embedding. |
| `SENSUS_RERANK_CANDIDATES` | `20` | quantos da lista fundida vão ao reranker |
| `SENSUS_RERANK_MAX_REQUEST_TOKENS` | `8000` | teto por requisição; precisa ser menor que a cota por minuto |
| `SENSUS_RERANK_MAX_DOCUMENT_CHARS` | `0` (sem truncagem) | trunca o texto ENVIADO ao reranker; o chunk devolvido continua íntegro |

Rodada de A/B da recuperação (não usa geração, não gasta OpenAI):

```bash
uv run python -m sensus_rag.evals.runner --tenant eval-local --rerank
```

Custo: uma chamada de rerank por pergunta. Com 20 candidatos de ~1200 chars a
requisição fica em ~8000 tokens estimados, e a conta sem método de pagamento tem 10K
TPM — na prática **uma chamada por minuto**, ~26 min para as 26 perguntas.

`--rerank` e `--generation` não rodam juntos, de propósito.

### Medir também a geração

```bash
export SENSUS_OPENAI_API_KEY=...
export SENSUS_GENERATION_MODEL=gpt-4o-mini
uv run python -m sensus_rag.evals.runner --tenant eval-local --generation
```

Gasta **38 chamadas** do provedor de geração por rodada (26 perguntas com resposta e 12
sem), além das de embedding. A flag é desligada por padrão justamente por isso: a medição
de recuperação continua custando o que sempre custou.

A saída traz `taxa_de_resposta` e `taxa_de_recusa` — esta última **separada por família**
— e destaca a lista de perguntas que o sistema respondeu sem ter base. Essa lista é o
achado mais importante de uma rodada: cada linha ali é uma alucinação.

As 26 respostas fundamentadas são gravadas em `evals/out/respostas-<data>.md`
(gitignored). É esse arquivo que se lê **uma vez**; o veredito vira a linha de base
qualitativa no SDD, ao lado dos dois números.

Se a chave de geração também tiver cota apertada, preencha
`SENSUS_GENERATION_REQUESTS_PER_MINUTE` — o mesmo limitador dos embeddings entra no
caminho e espaça as requisições.
