from conftest import cadastrar, tls_client
from sensus_api.app import create_app
from sensus_api.config import Settings
from sensus_api.email_memory import RecordingEmailSender
from sensus_api.upstream.contract import ResultEvent, StageEvent, TokenEvent
from sensus_api.upstream.demo import DemoUpstream


async def test_demo_emite_stages_tokens_e_termina_com_trechos() -> None:
    upstream = DemoUpstream(delay=0.0)

    events = [event async for event in upstream.ask(question="e aí?", user_id="u", tenant_id="t")]

    assert isinstance(events[0], StageEvent)
    assert any(isinstance(event, TokenEvent) for event in events)
    final = events[-1]
    assert isinstance(final, ResultEvent)
    assert len(final.chunks) > 0


async def test_demo_repete_a_pergunta_para_deixar_claro_que_e_demonstracao() -> None:
    upstream = DemoUpstream(delay=0.0)

    events = [
        event async for event in upstream.ask(question="qual o prazo?", user_id="u", tenant_id="t")
    ]
    texto = "".join(event.text for event in events if isinstance(event, TokenEvent))

    assert "qual o prazo?" in texto
    assert "demonstra" in texto.lower()


def test_app_sem_upstream_configurado_sobe_em_modo_demonstracao() -> None:
    """A fatia tem de rodar ponta a ponta antes de existir upstream real."""
    app = create_app(
        Settings(session_secret="s", suggestions=["Uma sugestão?"]),
        email=RecordingEmailSender(),
    )

    with tls_client(app) as client:
        cadastrar(client, app, organization_name="Acme", email="ana@acme.com")
        with client.stream("POST", "/api/v1/chat", json={"message": "oi?"}) as response:
            body = "".join(response.iter_text())

    names = [line.removeprefix("event: ") for line in body.split("\n") if line.startswith("event:")]
    assert names[-1] == "done"
    assert "token" in names
