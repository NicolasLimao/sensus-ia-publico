"""Configuração do adaptador — tudo por variável de ambiente, nada versionado."""

from functools import lru_cache

from pydantic import model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Configuração lida do ambiente com o prefixo `SENSUS_`."""

    model_config = SettingsConfigDict(env_prefix="SENSUS_", env_file=".env", extra="ignore")

    # Segredo que assina o cookie de sessão. Obrigatório.
    session_secret: str

    # Prazo do token de sessão. Vale para o token, não só para o cookie.
    session_max_age_seconds: int = 7 * 24 * 60 * 60

    # Prazo do convite. Espelha o da sessão de propósito: é um relógio a menos no
    # produto, e deixa o prazo ajustável sem migração — `expires_at` é calculado na
    # criação, com este valor.
    invite_max_age_seconds: int = 7 * 24 * 60 * 60

    # Prazo do link de reset. Uma hora, e não os 7 dias do convite: quem pede reset
    # está na tela agora, e o link vale mais que o do convite — troca senha e abre
    # sessão. Prazo curto é o que o caso de uso permite pagar.
    password_reset_max_age_seconds: int = 60 * 60

    # Janela do freio por conta. Pedido novo dentro dela não gera e-mail — e a resposta
    # continua sendo o mesmo 202, para o freio não virar oráculo de quem tem conta.
    password_reset_throttle_seconds: int = 120

    # Prazo do link de confirmação de cadastro. 24 h: entre a 1 h do reset — de quem
    # está na tela agora — e os 7 dias do convite. Quem se cadastra costuma confirmar na
    # hora, mas o e-mail pode cair numa caixa que a pessoa só abre à noite.
    signup_confirmation_max_age_seconds: int = 60 * 60 * 24

    # Janela do freio por endereço. O mesmo do reset, pela mesma razão: o pedido é
    # anônimo, e sem teto a rota vira amplificador de e-mail contra um endereço
    # conhecido. A resposta continua sendo o mesmo 202, para o freio não virar oráculo.
    signup_throttle_seconds: int = 120

    # SMTP. `smtp_host` vazio escolhe o `LoggingEmailSender` (desenvolvimento sem SMTP).
    smtp_host: str = ""
    smtp_port: int = 587
    smtp_username: str = ""
    smtp_password: str = ""
    smtp_starttls: bool = True
    email_from: str = ""

    # De onde saem os links dos e-mails. A 3c não precisou disto porque quem montava o
    # link do convite era o FRONT, com `window.location.origin` — o servidor não tem
    # `window`, e o e-mail de reset é montado aqui.
    public_base_url: str = ""

    # `Secure` no cookie de sessão. O default é o seguro: sem ele, uma requisição a
    # `http://` entrega o token inteiro em claro, e o token É a sessão. Desligar só
    # para o dev local em `http://localhost`, onde não há TLS e o navegador
    # descartaria o cookie.
    session_cookie_secure: bool = True

    # Upstream: só o adaptador conhece. O navegador nunca vê estes valores.
    upstream_base_url: str = ""
    upstream_api_key: str = ""
    upstream_timeout_seconds: float = 90.0

    # Sugestões da tela vazia. São específicas da base do cliente, por isso vêm daqui
    # e nunca do código do front.
    suggestions: list[str] = []

    # Modelos que um tenant pode escolher (fatia 4a). Texto livre viraria 4xx do
    # provedor em produção, descoberto pelo usuário final no meio de uma resposta —
    # e a borda recusa antes, no único lugar onde o erro ainda tem a quem ser mostrado.
    # É configuração de OPERAÇÃO: quem a muda somos nós, não o cliente.
    allowed_generation_models: list[str] = ["gpt-4o-mini", "gpt-4o"]

    cors_origins: list[str] = ["http://localhost:5173"]

    @model_validator(mode="after")
    def _exige_remetente_e_base_com_smtp(self) -> "Settings":
        """Com SMTP configurado, `email_from` e `public_base_url` viram obrigatórios.

        Sem eles a aplicação sobe e manda e-mail sem remetente ou com link apontando
        para lugar nenhum — e o único lugar onde isso aparece é a caixa de entrada de
        quem precisava do link. Recusar subir é o barulho no lugar certo.
        """
        if self.smtp_host and not self.email_from:
            raise ValueError("SENSUS_EMAIL_FROM é obrigatório quando há SENSUS_SMTP_HOST")
        if self.smtp_host and not self.public_base_url:
            raise ValueError("SENSUS_PUBLIC_BASE_URL é obrigatório quando há SENSUS_SMTP_HOST")
        return self


@lru_cache
def get_settings() -> Settings:
    return Settings()  # type: ignore[call-arg]  # campos obrigatórios vêm do ambiente
