"""A brecha desta fatia nasceu de um cabeçalho conveniente.

Nada impede alguém de reintroduzi-lo pela mesma conveniência, e o teste que pegaria
isso não existe em lugar nenhum — os testes de rota passariam felizes. Este varre o
código e roda na suíte padrão.

Varre também o README e o SDD: a conveniência mora tanto na documentação quanto no
código, e um `curl` de exemplo que ainda manda o cabeçalho hoje responde 401 — quem
lê conclui que a API está quebrada, não que o exemplo envelheceu.
"""

from pathlib import Path

RAIZ = Path(__file__).resolve().parents[2]
FONTE = RAIZ / "api" / "src" / "sensus_api"
DOCS = (RAIZ / "README.md", RAIZ / "docs" / "SDD.md")

CABECALHO = "X-Sensus-Tenant"


def _dentro_de_bloco_de_codigo(caminho: Path) -> bool:
    """Só o que é copiável conta.

    Citar o cabeçalho em prosa é o registro de que a brecha existiu e foi fechada, e
    isso tem de continuar podendo ser dito. O que não pode voltar é o comando que o
    leitor cola no terminal — esse hoje responde 401.
    """
    dentro = False
    for linha in caminho.read_text(encoding="utf-8").splitlines():
        if linha.lstrip().startswith("```"):
            dentro = not dentro
        elif dentro and CABECALHO in linha:
            return True
    return False


def test_nenhum_modulo_resolve_tenant_por_cabecalho() -> None:
    culpados = [
        caminho.relative_to(FONTE).as_posix()
        for caminho in FONTE.rglob("*.py")
        if CABECALHO in caminho.read_text(encoding="utf-8")
    ]

    assert culpados == [], (
        f"cabeçalho de tenant reintroduzido em {culpados} — o tenant vem do principal"
    )


def test_nenhuma_doc_ainda_ensina_o_cabecalho() -> None:
    culpados = [
        caminho.relative_to(RAIZ).as_posix()
        for caminho in DOCS
        if _dentro_de_bloco_de_codigo(caminho)
    ]

    assert culpados == [], (
        f"{culpados} ainda ensina o cabeçalho num exemplo copiável — ele responde 401"
    )
