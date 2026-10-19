"""Rotas de documento. O tenant vem da identidade autenticada, e escrever exige admin."""

from dataclasses import replace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from sensus_api.routes.documents import READ_CHUNK, read_within
from sensus_rag.errors import DocumentTooLarge


class _CountingSource:
    """Upload de mentira que registra quantos bytes chegou a servir."""

    def __init__(self, data: bytes) -> None:
        self._data = data
        self._position = 0
        self.served = 0

    async def read(self, size: int = -1) -> bytes:
        end = len(self._data) if size < 0 else self._position + size
        piece = self._data[self._position : end]
        self._position += len(piece)
        self.served += len(piece)
        return piece


def test_upload_indexa_e_devolve_a_contagem(session_client: TestClient) -> None:
    response = session_client.post(
        "/api/v1/documents",
        files={"file": ("nota.txt", b"Primeiro trecho.\n\nSegundo trecho.", "text/plain")},
    )

    assert response.status_code == 201
    body = response.json()
    assert body["name"] == "nota.txt"
    assert body["chunk_count"] >= 1
    assert body["document_id"]


def test_formato_nao_suportado_devolve_415(session_client: TestClient) -> None:
    response = session_client.post(
        "/api/v1/documents",
        files={"file": ("planilha.xlsx", b"qualquer", "application/vnd.ms-excel")},
    )

    assert response.status_code == 415
    assert response.json()["detail"]["code"] == "unsupported_document"


def test_documento_sem_texto_devolve_400(session_client: TestClient) -> None:
    response = session_client.post(
        "/api/v1/documents",
        files={"file": ("vazio.txt", b"   \n\n  ", "text/plain")},
    )

    assert response.status_code == 400
    assert response.json()["detail"]["code"] == "empty_document"


def test_arquivo_grande_demais_devolve_413(session_client: TestClient) -> None:
    response = session_client.post(
        "/api/v1/documents",
        files={"file": ("grande.txt", b"x" * 5000, "text/plain")},
    )

    assert response.status_code == 413
    assert response.json()["detail"]["code"] == "document_too_large"


async def test_upload_para_de_ler_assim_que_passa_do_teto() -> None:
    """O teto tem de cortar a leitura, não só decidir o código de resposta.

    Antes, a rota fazia `await file.read()` do corpo inteiro e só então comparava com o
    limite: um arquivo de gigabytes já estava na memória do processo quando o 413 saía.
    """
    source = _CountingSource(b"x" * (4 * 1024 * 1024))

    with pytest.raises(DocumentTooLarge):
        await read_within(source, name="grande.txt", max_bytes=1024)

    assert source.served <= 1024 + READ_CHUNK


async def test_upload_dentro_do_teto_e_lido_por_inteiro() -> None:
    source = _CountingSource(b"conteudo pequeno")

    assert await read_within(source, name="a.txt", max_bytes=1024) == b"conteudo pequeno"


def test_listar_devolve_os_documentos_do_tenant(session_client: TestClient) -> None:
    session_client.post("/api/v1/documents", files={"file": ("a.txt", b"conteudo a", "text/plain")})

    response = session_client.get("/api/v1/documents")

    assert response.status_code == 200
    assert [d["name"] for d in response.json()["documents"]] == ["a.txt"]


def test_apagar_remove_o_documento(session_client: TestClient) -> None:
    created = session_client.post(
        "/api/v1/documents", files={"file": ("a.txt", b"conteudo a", "text/plain")}
    ).json()

    deleted = session_client.delete(f"/api/v1/documents/{created['document_id']}")

    assert deleted.status_code == 204
    assert session_client.get("/api/v1/documents").json()["documents"] == []


@pytest.mark.parametrize(
    "document_id",
    [
        pytest.param("11111111-1111-4111-8111-111111111111", id="uuid-inexistente"),
        pytest.param("nao-existe", id="id-malformado"),
    ],
)
def test_apagar_documento_inexistente_devolve_404(
    session_client: TestClient, document_id: str
) -> None:
    """Malformado e inexistente dão o mesmo 404 — no duplo E no Postgres.

    O id malformado é o caso que divergia: o Postgres levantava erro de sintaxe de UUID
    e a borda devolvia 503. A guarda que igualou os dois está em
    `tests/rag/test_repository_contract.py`; aqui se verifica o código HTTP que sai.
    """
    response = session_client.delete(f"/api/v1/documents/{document_id}")

    assert response.status_code == 404


def test_documentos_nao_atravessam_entre_tenants(
    session_client: TestClient, other_session_client: TestClient
) -> None:
    """As DUAS metades: o outro tenant não vê, e o dono continua vendo.

    Só a metade negativa é satisfeita por uma implementação que devolva sempre lista
    vazia. Foi exatamente o defeito corrigido em 618f251 na camada de repositório — a
    lição não tinha atravessado para a borda HTTP.
    """
    session_client.post(
        "/api/v1/documents",
        files={"file": ("a.txt", b"conteudo do tenant um", "text/plain")},
    )

    from_other = other_session_client.get("/api/v1/documents")
    from_owner = session_client.get("/api/v1/documents")

    assert from_other.json()["documents"] == []
    assert [d["name"] for d in from_owner.json()["documents"]] == ["a.txt"]


def test_apagar_documento_de_outro_tenant_nao_o_remove(
    session_client: TestClient, other_session_client: TestClient
) -> None:
    """404 para quem não é dono, e o documento continua lá para quem é. Sem a segunda
    metade, um `delete` que ignorasse o tenant e apagasse mesmo assim passaria."""
    created = session_client.post(
        "/api/v1/documents",
        files={"file": ("a.txt", b"conteudo do tenant um", "text/plain")},
    ).json()

    deleted = other_session_client.delete(f"/api/v1/documents/{created['document_id']}")
    from_owner = session_client.get("/api/v1/documents")

    assert deleted.status_code == 404
    assert [d["name"] for d in from_owner.json()["documents"]] == ["a.txt"]


UM_UUID = "11111111-1111-1111-1111-111111111111"


def test_membro_nao_apaga_documento(session_client: TestClient, app: FastAPI) -> None:
    """Papel é checado na borda: a policy do banco olha só o tenant (spec §5.3)."""
    # O cadastro sempre cria admin; rebaixar no duplo é como se exercita o membro sem
    # inventar uma rota de troca de papel que esta fatia não tem.
    directory = app.state.users
    directory.accounts["ana@acme.com"] = replace(directory.accounts["ana@acme.com"], role="member")

    resposta = session_client.delete(f"/api/v1/documents/{UM_UUID}")

    assert resposta.status_code == 403
    assert resposta.json()["detail"]["code"] == "forbidden_role"


def test_admin_passa_pela_mesma_rota(session_client: TestClient) -> None:
    """A metade positiva. Sem ela, uma checagem que negasse para TODO MUNDO passaria
    no teste acima."""
    resposta = session_client.delete(f"/api/v1/documents/{UM_UUID}")

    assert resposta.status_code == 404  # documento não existe, mas o papel passou


def test_membro_nao_faz_upload(session_client: TestClient, app: FastAPI) -> None:
    """Upload muda o que a organização inteira lê — mesma regra do delete."""
    directory = app.state.users
    directory.accounts["ana@acme.com"] = replace(directory.accounts["ana@acme.com"], role="member")

    resposta = session_client.post(
        "/api/v1/documents",
        files={"file": ("a.txt", b"conteudo", "text/plain")},
    )

    assert resposta.status_code == 403


def test_membro_ainda_lista_documentos(session_client: TestClient, app: FastAPI) -> None:
    """Ler é de todo mundo da organização: só escrever exige admin."""
    directory = app.state.users
    directory.accounts["ana@acme.com"] = replace(directory.accounts["ana@acme.com"], role="member")

    assert session_client.get("/api/v1/documents").status_code == 200
