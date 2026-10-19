import json

from fastapi.testclient import TestClient


def message_id_da_resposta(client: TestClient) -> str:
    with client.stream("POST", "/api/v1/chat", json={"message": "pergunta?"}) as response:
        body = "".join(response.iter_text())
    done = [line for line in body.split("\n") if line.startswith("data:")][-1]
    payload: dict[str, str] = json.loads(done.removeprefix("data: "))
    return payload["message_id"]


def test_feedback_registra_o_voto(session_client: TestClient) -> None:
    message_id = message_id_da_resposta(session_client)

    response = session_client.post(f"/api/v1/messages/{message_id}/feedback", json={"rating": "up"})

    assert response.status_code == 204
    messages = session_client.get("/api/v1/conversation").json()["messages"]
    assert messages[1]["feedback"] == "up"


def test_feedback_em_mensagem_inexistente_responde_404(session_client: TestClient) -> None:
    response = session_client.post("/api/v1/messages/nao-existe/feedback", json={"rating": "down"})

    assert response.status_code == 404


def test_feedback_com_valor_invalido_responde_422(session_client: TestClient) -> None:
    message_id = message_id_da_resposta(session_client)

    response = session_client.post(
        f"/api/v1/messages/{message_id}/feedback", json={"rating": "talvez"}
    )

    assert response.status_code == 422
