"""Configuração do pipeline — mesmo prefixo `SENSUS_` do adaptador, classe própria.

Classe separada porque `sensus_rag` não importa `sensus_api`. As duas leem o mesmo
ambiente sem se conhecerem.
"""

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class RagSettings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="SENSUS_", env_file=".env", extra="ignore")

    database_url: str = ""

    # Conexão da aplicação, com o papel `sensus_app` (sem BYPASSRLS). A
    # `database_url` fica para migração e administração.
    app_database_url: str = ""
    voyage_api_key: str = ""
    embedding_model: str = "voyage-4-large"
    embedding_dimensions: int = 1024

    # Cota do provedor de embeddings. Zero = sem limite, que é o certo para uma chave
    # com cota padrão: não faz sentido a aplicação se auto-estrangular. Quem estiver na
    # conta gratuita da Voyage (sem método de pagamento: 3 RPM e 10K TPM) preenche estas
    # duas, e o cliente passa a ESPAÇAR as requisições em vez de descobrir a cota
    # levando 429 — retentativa não resolve, porque a requisição recusada também
    # consome cota.
    embedding_requests_per_minute: int = 0
    embedding_tokens_per_minute: int = 0

    # Teto de tokens por requisição. Precisa ser menor que a cota por minuto, senão uma
    # única requisição nunca cabe na janela.
    embedding_max_batch_tokens: int = 8_000

    max_upload_bytes: int = 20 * 1024 * 1024

    chunk_target_chars: int = 1200
    chunk_overlap_chars: int = 150

    # Cada perna da busca traz este tanto antes da fusão; o RRF corta no top_k final.
    retrieval_candidates: int = 40
    rrf_k: int = 60

    # --- Rerank ---------------------------------------------------------------
    # Ligado por default desde a medição de 2026-09-05: recall@1 de 0,654 para 0,846
    # (+0,192, quase 5x a margem de ruído de ~0,04) — ver `docs/SDD.md`, seção 5. Vazio
    # desliga, mesmo padrão de `uses_generation`, mas agora desligar é a escolha
    # explícita. A chave é a MESMA do embedding (`SENSUS_VOYAGE_API_KEY`): é a mesma
    # conta, e por isso os dois clientes dividem um único limitador de cota.
    rerank_model: str = "rerank-2.5"

    # Quantos da lista fundida vão ao reranker. Reordenar 20 para escolher 5 é o
    # espaço que o rerank tem para mudar alguma coisa; com 10 quase não há espaço.
    rerank_candidates: int = 20

    # Teto por requisição. Precisa ser menor que a cota por minuto, senão uma única
    # requisição nunca cabe na janela — mesma armadilha do `embedding_max_batch_tokens`.
    rerank_max_request_tokens: int = 8_000

    # Trunca o texto ENVIADO ao reranker; o chunk devolvido continua íntegro. Zero =
    # sem truncagem, que é o certo enquanto os candidatos couberem no orçamento.
    rerank_max_document_chars: int = 0

    # --- Geração -------------------------------------------------------------
    openai_api_key: str = ""
    generation_model: str = "gpt-4o-mini"

    # Quantos trechos vão ao prompt. Independente do `top_k` da rota `/search`, que
    # serve à depuração e à medição de recuperação.
    generation_top_k: int = 5

    # Teto de caracteres de contexto; o corte é pela cauda (o pior colocado sai
    # primeiro). Existe para que aumentar `generation_top_k` um dia não estoure o
    # contexto do provedor com o erro aparecendo longe da causa. Precisa ser positivo:
    # um valor <= 0 não quebra (o contexto vira vazio, sem exceção), mas também não faz
    # sentido operacionalmente.
    generation_max_context_chars: int = 12_000

    # Menor que `SENSUS_UPSTREAM_TIMEOUT_SECONDS` (90s) de propósito: assim o estouro
    # é atribuível à geração, e não ao adaptador inteiro.
    generation_timeout_seconds: float = 60.0

    # Zero = sem limite, o certo para o chat (uma chamada por pergunta). Quem sente
    # cota é o harness, que roda dezenas de perguntas em série.
    generation_requests_per_minute: int = 0


@lru_cache
def get_rag_settings() -> RagSettings:
    return RagSettings()


def connection_url(settings: RagSettings) -> str:
    """A URL que a APLICAÇÃO usa para ler e escrever.

    Prefere o papel `sensus_app`, sem BYPASSRLS: conectando como `postgres`, a policy
    da migração 003 é ignorada e a RLS vira decoração. O fallback para `database_url`
    existe para quem ainda não criou o papel — a aplicação sobe, mas sem a segunda
    camada de isolamento.
    """
    return settings.app_database_url or settings.database_url
