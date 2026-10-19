def test_a_aplicacao_prefere_o_papel_sem_bypass_de_rls() -> None:
    """Conectando como `postgres`, a policy é ignorada e a RLS vira decoração."""
    from sensus_rag.config import RagSettings, connection_url

    settings = RagSettings(
        database_url="postgresql://postgres@host/db",
        app_database_url="postgresql://sensus_app@host/db",
    )

    assert connection_url(settings) == "postgresql://sensus_app@host/db"


def test_sem_o_papel_a_aplicacao_ainda_sobe() -> None:
    """Fallback explícito: quem ainda não criou `sensus_app` continua rodando, só que
    sem a segunda camada de isolamento."""
    from sensus_rag.config import RagSettings, connection_url

    settings = RagSettings(database_url="postgresql://postgres@host/db", app_database_url="")

    assert connection_url(settings) == "postgresql://postgres@host/db"
