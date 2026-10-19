from fastapi import FastAPI
from fastapi.testclient import TestClient

from conftest import cadastrar, tls_client


def test_conversa_vazia_no_inicio(session_client: TestClient) -> None:
    response = session_client.get("/api/v1/conversation")

    assert response.status_code == 200
    assert response.json() == {"messages": []}


def test_conversa_restaura_pergunta_e_resposta_com_fontes(session_client: TestClient) -> None:
    with session_client.stream("POST", "/api/v1/chat", json={"message": "pergunta?"}) as response:
        response.read()

    messages = session_client.get("/api/v1/conversation").json()["messages"]

    assert [m["role"] for m in messages] == ["user", "assistant"]
    assert messages[1]["content"] == "Uma resposta."
    assert messages[1]["chunks_used"] == 1
    assert messages[1]["sources"][0]["excerpt"] == "trecho"
    assert messages[1]["feedback"] is None


def test_conversa_de_uma_sessao_nao_aparece_em_outra(
    session_client: TestClient, app: FastAPI
) -> None:
    with session_client.stream("POST", "/api/v1/chat", json={"message": "pergunta?"}) as response:
        response.read()

    # Mesma aplicação, mesmo store — OUTRA pessoa, de outra organização. Isolamento é
    # requisito, não detalhe.
    with tls_client(app) as other:
        cadastrar(other, app, organization_name="Globex", email="bob@globex.com")

        assert other.get("/api/v1/conversation").json() == {"messages": []}
