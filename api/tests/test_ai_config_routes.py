"""As três rotas de config de IA, pela aplicação real.

O que NÃO está aqui: "uma ativa por tenant" sob concorrência e o isolamento pela
policy. São propriedades do banco, e ficam em `test_ai_configs_postgres.py`.
"""

from dataclasses import dataclass
from typing import Any

from fastapi import FastAPI
from fastapi.testclient import TestClient

from sensus_api.ai_configs import AiConfig, AiConfigVersion, ConcurrentConfigWrite

CONFIG = {
    "briefing": "Somos a Acme e atendemos condomínios.",
    "generation_model": "gpt-4o-mini",
    "generation_top_k": 7,
    "max_context_chars": 9_000,
    "suggestions": ["Qual é o prazo?", "Como faço a segunda via?"],
}


class TestLeitura:
    def test_tenant_novo_ve_os_defaults_e_nenhuma_versao(self, session_client: TestClient) -> None:
        """Não é caso especial: é a verdade sobre de onde o comportamento vem hoje."""
        corpo = session_client.get("/api/v1/ai-config").json()

        assert corpo["versions"] == []
        assert corpo["active"]["briefing"] == ""
        assert corpo["active"]["generation_top_k"] == 5
        assert corpo["active"]["suggestions"] == [
            "Pergunta um?",
            "Pergunta dois?",
            "Pergunta três?",
        ]

    def test_a_leitura_traz_os_modelos_permitidos(self, session_client: TestClient) -> None:
        """A tela monta o `select` com isto. Sem a lista, ela teria de repetir os nomes
        no código do front — e divergir do servidor no dia em que um sair."""
        corpo = session_client.get("/api/v1/ai-config").json()

        assert "gpt-4o-mini" in corpo["allowed_models"]

    def test_member_nao_le_a_config(self, member_client: TestClient) -> None:
        assert member_client.get("/api/v1/ai-config").status_code == 403


class TestSalvar:
    def test_salvar_cria_a_versao_um_e_ja_ativa(self, session_client: TestClient) -> None:
        resposta = session_client.post("/api/v1/ai-config", json=CONFIG)

        assert resposta.status_code == 201, resposta.text
        corpo = resposta.json()
        assert corpo["version"] == 1
        assert corpo["is_active"] is True
        assert corpo["config"]["briefing"] == CONFIG["briefing"]

    def test_a_versao_salva_vira_a_ativa_da_leitura(self, session_client: TestClient) -> None:
        session_client.post("/api/v1/ai-config", json=CONFIG)

        ativa = session_client.get("/api/v1/ai-config").json()["active"]

        assert ativa["generation_top_k"] == 7
        assert ativa["briefing"] == CONFIG["briefing"]

    def test_salvar_de_novo_cria_a_versao_dois_sem_apagar_a_um(
        self, session_client: TestClient
    ) -> None:
        session_client.post("/api/v1/ai-config", json=CONFIG)

        segunda = session_client.post("/api/v1/ai-config", json={**CONFIG, "briefing": "outro"})

        assert segunda.json()["version"] == 2
        versoes = session_client.get("/api/v1/ai-config").json()["versions"]
        assert [v["version"] for v in versoes] == [2, 1]

    def test_a_versao_registra_quem_salvou(self, session_client: TestClient) -> None:
        resposta = session_client.post("/api/v1/ai-config", json=CONFIG)

        assert resposta.json()["created_by_email"] == "ana@acme.com"

    def test_modelo_fora_da_lista_e_422_com_a_lista_na_mensagem(
        self, session_client: TestClient
    ) -> None:
        """Sem nomear os permitidos, a tela só pode dizer "inválido" e a pessoa fica
        adivinhando."""
        resposta = session_client.post(
            "/api/v1/ai-config", json={**CONFIG, "generation_model": "modelo-inventado"}
        )

        assert resposta.status_code == 422
        detalhe = resposta.json()["detail"]
        assert detalhe["code"] == "model_not_allowed"
        assert "gpt-4o-mini" in detalhe["message"]

    def test_briefing_gigante_e_recusado(self, session_client: TestClient) -> None:
        """O briefing divide o orçamento de contexto com os trechos: sem teto, ele
        empurraria a base inteira para fora do prompt."""
        resposta = session_client.post(
            "/api/v1/ai-config", json={**CONFIG, "briefing": "x" * 4_001}
        )

        assert resposta.status_code == 422

    def test_top_k_fora_da_faixa_e_recusado(self, session_client: TestClient) -> None:
        assert (
            session_client.post(
                "/api/v1/ai-config", json={**CONFIG, "generation_top_k": 0}
            ).status_code
            == 422
        )
        assert (
            session_client.post(
                "/api/v1/ai-config", json={**CONFIG, "generation_top_k": 21}
            ).status_code
            == 422
        )

    def test_sugestao_em_branco_nao_e_gravada(self, session_client: TestClient) -> None:
        """Um campo vazio esquecido na tela não pode virar sugestão em branco no chat."""
        resposta = session_client.post(
            "/api/v1/ai-config", json={**CONFIG, "suggestions": ["Vale?", "   ", ""]}
        )

        assert resposta.json()["config"]["suggestions"] == ["Vale?"]

    def test_sugestao_gigante_e_recusada(self, session_client: TestClient) -> None:
        """Sugestão é chip de tela vazia, e vai para TODO membro do tenant a cada carga
        de página pelo `/auth/me`. Sem teto por item, o de dez itens não segura nada."""
        resposta = session_client.post(
            "/api/v1/ai-config", json={**CONFIG, "suggestions": ["x" * 201]}
        )

        assert resposta.status_code == 422

    def test_sugestao_e_gravada_sem_os_espacos_das_pontas(self, session_client: TestClient) -> None:
        resposta = session_client.post(
            "/api/v1/ai-config", json={**CONFIG, "suggestions": ["  Qual o prazo?  "]}
        )

        assert resposta.json()["config"]["suggestions"] == ["Qual o prazo?"]

    def test_member_nao_salva(self, member_client: TestClient) -> None:
        resposta = member_client.post("/api/v1/ai-config", json=CONFIG)

        assert resposta.status_code == 403


class TestAtivar:
    def test_ativar_uma_antiga_volta_o_comportamento(self, session_client: TestClient) -> None:
        primeira = session_client.post(
            "/api/v1/ai-config", json={**CONFIG, "briefing": "original"}
        ).json()
        session_client.post("/api/v1/ai-config", json={**CONFIG, "briefing": "ruim"})

        resposta = session_client.post(f"/api/v1/ai-config/{primeira['id']}/activate")

        assert resposta.status_code == 204
        assert session_client.get("/api/v1/ai-config").json()["active"]["briefing"] == ("original")

    def test_rollback_nao_apaga_a_versao_que_saiu(self, session_client: TestClient) -> None:
        primeira = session_client.post(
            "/api/v1/ai-config", json={**CONFIG, "briefing": "original"}
        ).json()
        ruim = session_client.post("/api/v1/ai-config", json={**CONFIG, "briefing": "ruim"}).json()

        session_client.post(f"/api/v1/ai-config/{primeira['id']}/activate")

        versoes = session_client.get("/api/v1/ai-config").json()["versions"]
        assert ruim["id"] in {v["id"] for v in versoes}

    def test_ativar_id_inexistente_e_404(self, session_client: TestClient) -> None:
        resposta = session_client.post("/api/v1/ai-config/nao-existe/activate")

        assert resposta.status_code == 404

    def test_ativar_versao_de_OUTRA_organizacao_e_404(
        self, session_client: TestClient, other_session_client: TestClient
    ) -> None:
        """Mesmo 404 do inexistente: distinguir transformaria a rota num oráculo de
        quais ids existem por aí."""
        do_outro = other_session_client.post("/api/v1/ai-config", json=CONFIG).json()

        resposta = session_client.post(f"/api/v1/ai-config/{do_outro['id']}/activate")

        assert resposta.status_code == 404

    def test_member_nao_ativa(self, member_client: TestClient, session_client: TestClient) -> None:
        criada = session_client.post("/api/v1/ai-config", json=CONFIG).json()

        resposta = member_client.post(f"/api/v1/ai-config/{criada['id']}/activate")

        assert resposta.status_code == 403


class TestIsolamento:
    def test_a_config_de_uma_organizacao_nao_aparece_na_outra(
        self, session_client: TestClient, other_session_client: TestClient
    ) -> None:
        session_client.post("/api/v1/ai-config", json={**CONFIG, "briefing": "da acme"})

        do_outro = other_session_client.get("/api/v1/ai-config").json()

        assert do_outro["versions"] == []
        assert do_outro["active"]["briefing"] == ""


class TestSugestoesNoMe:
    def test_sem_config_o_me_devolve_as_sugestoes_do_ambiente(
        self, session_client: TestClient
    ) -> None:
        corpo = session_client.get("/api/v1/auth/me").json()

        assert corpo["suggestions"] == [
            "Pergunta um?",
            "Pergunta dois?",
            "Pergunta três?",
        ]

    def test_com_config_o_me_devolve_as_do_tenant(self, session_client: TestClient) -> None:
        """As sugestões alcançam `member`, que não pode ver a config — e é esse o
        ponto: a tela vazia dele passa a falar da base dele."""
        session_client.post("/api/v1/ai-config", json=CONFIG)

        corpo = session_client.get("/api/v1/auth/me").json()

        assert corpo["suggestions"] == CONFIG["suggestions"]

    def test_o_member_ve_as_sugestoes_do_tenant(
        self, session_client: TestClient, member_client: TestClient
    ) -> None:
        session_client.post("/api/v1/ai-config", json=CONFIG)

        corpo = member_client.get("/api/v1/auth/me").json()

        assert corpo["suggestions"] == CONFIG["suggestions"]


class TestGravacaoSimultanea:
    """Dois admins do mesmo tenant salvando no mesmo instante.

    Quem recusa a segunda é o banco (`unique (tenant_id, version)`), e o duplo em
    memória **não tem como** recusar: ele roda numa tarefa só e não suspende no meio de
    `create_version`, então as duas gravações nunca se cruzam. O que a borda prova aqui
    é a TRADUÇÃO — a recusa chega como 409 dizendo o que fazer, e não como 500. Quem
    prova que o par real levanta o erro sob concorrência de verdade é
    `test_ai_configs_postgres.py`.
    """

    def test_a_recusa_do_banco_vira_409_com_o_que_fazer(
        self, app: FastAPI, session_client: TestClient
    ) -> None:
        app.state.ai_configs = _PerdeACorrida(app.state.ai_configs)

        resposta = session_client.post("/api/v1/ai-config", json=CONFIG)

        assert resposta.status_code == 409, resposta.text
        detalhe = resposta.json()["detail"]
        assert detalhe["code"] == "config_conflict"
        # A mensagem precisa dizer o que fazer: "conflito" sozinho manda a pessoa
        # tentar de novo por cima de uma config que ela não viu.
        assert "recarregue" in detalhe["message"].lower()


class TestPaginacaoDaListagem:
    """O histórico não tem teto — uma linha por edição, e podar seria o contrário do que
    a fatia entrega. Sem página, quem edita muito recebe a coleção inteira, com o config
    completo de cada versão, a cada abertura do painel."""

    def test_a_pagina_pedida_traz_o_limite_e_avisa_que_ha_mais(
        self, session_client: TestClient
    ) -> None:
        for _ in range(3):
            session_client.post("/api/v1/ai-config", json=CONFIG)

        corpo = session_client.get("/api/v1/ai-config?limit=2").json()

        assert [v["version"] for v in corpo["versions"]] == [3, 2]
        assert corpo["has_more_versions"] is True

    def test_o_offset_alcanca_a_ultima_pagina(self, session_client: TestClient) -> None:
        for _ in range(3):
            session_client.post("/api/v1/ai-config", json=CONFIG)

        corpo = session_client.get("/api/v1/ai-config?limit=2&offset=2").json()

        assert [v["version"] for v in corpo["versions"]] == [1]
        assert corpo["has_more_versions"] is False

    def test_sem_limite_na_query_a_resposta_ainda_tem_teto(
        self, session_client: TestClient
    ) -> None:
        """O default é o que protege quem não pediu nada — que é o painel, hoje."""
        for _ in range(21):
            session_client.post("/api/v1/ai-config", json=CONFIG)

        corpo = session_client.get("/api/v1/ai-config").json()

        assert len(corpo["versions"]) == 20
        assert corpo["has_more_versions"] is True

    def test_limite_acima_do_teto_e_recusado(self, session_client: TestClient) -> None:
        """Sem o teto, `?limit=100000` desfaz a paginação por query string."""
        assert session_client.get("/api/v1/ai-config?limit=101").status_code == 422

    def test_offset_negativo_e_recusado(self, session_client: TestClient) -> None:
        assert session_client.get("/api/v1/ai-config?offset=-1").status_code == 422


class TestRollbackSimultaneo:
    """Dois admins fazendo rollback no mesmo instante.

    A outra corrida da mesma tabela: o índice de "uma ativa por tenant" recusa a segunda
    troca, e sem tradução ela chegaria como 500 — para um caso em que o banco fez
    exatamente o que devia.
    """

    def test_a_recusa_do_banco_vira_409(self, app: FastAPI, session_client: TestClient) -> None:
        criada = session_client.post("/api/v1/ai-config", json=CONFIG).json()
        app.state.ai_configs = _PerdeACorrida(app.state.ai_configs)

        resposta = session_client.post(f"/api/v1/ai-config/{criada['id']}/activate")

        assert resposta.status_code == 409, resposta.text
        assert resposta.json()["detail"]["code"] == "config_conflict"


@dataclass
class _PerdeACorrida:
    """O diretório quando outra gravação chegou primeiro.

    Delega tudo ao diretório de verdade e só troca `create_version` — assim o resto da
    requisição (sessão, papel, leitura) continua sendo o caminho real, e o único ponto
    fabricado é o que o banco faria sozinho sob concorrência.
    """

    real: Any

    def __getattr__(self, nome: str) -> Any:
        return getattr(self.real, nome)

    async def create_version(
        self, *, tenant_id: str, created_by: str, config: AiConfig
    ) -> AiConfigVersion:
        raise ConcurrentConfigWrite

    async def activate(self, *, tenant_id: str, config_id: str) -> bool:
        raise ConcurrentConfigWrite
