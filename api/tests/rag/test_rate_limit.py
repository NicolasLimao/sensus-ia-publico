"""Espaçamento de requisições ao provedor de embeddings.

Relógio e espera são falsos: o limitador é feito de esperas de dezenas de segundos, e
um teste que as cumpre de verdade não cabe na suíte padrão. O que se verifica é
**quanto ele mandaria esperar**, e que o tempo simulado avança de acordo.
"""

from sensus_rag.rate_limit import WINDOW_SECONDS, SlidingWindowLimiter


class Relogio:
    """Relógio falso que só anda quando o limitador manda esperar."""

    def __init__(self) -> None:
        self.agora = 1000.0
        self.esperas: list[float] = []

    def now(self) -> float:
        return self.agora

    async def sleep(self, seconds: float) -> None:
        self.esperas.append(seconds)
        self.agora += seconds


def limitador(relogio: Relogio, *, requests: int, tokens: int) -> SlidingWindowLimiter:
    return SlidingWindowLimiter(
        max_requests=requests, max_tokens=tokens, sleep=relogio.sleep, now=relogio.now
    )


async def test_dentro_da_cota_nao_espera() -> None:
    relogio = Relogio()
    limite = limitador(relogio, requests=3, tokens=10_000)

    for _ in range(3):
        await limite.acquire(1_000)

    assert relogio.esperas == []


async def test_a_quarta_requisicao_espera_a_primeira_sair_da_janela() -> None:
    """Com 3 RPM, a quarta só pode sair quando a primeira completa um minuto."""
    relogio = Relogio()
    limite = limitador(relogio, requests=3, tokens=10_000)
    for _ in range(3):
        await limite.acquire(1_000)

    await limite.acquire(1_000)

    assert relogio.esperas == [WINDOW_SECONDS]


async def test_o_teto_de_tokens_segura_mesmo_com_requisicoes_sobrando() -> None:
    """As duas cotas valem juntas: estourar qualquer uma derruba a chamada.

    Duas requisições de 6K tokens somam 12K contra um teto de 10K — mesmo com o limite
    de requisições folgado, a segunda tem de esperar.
    """
    relogio = Relogio()
    limite = limitador(relogio, requests=100, tokens=10_000)
    await limite.acquire(6_000)

    await limite.acquire(6_000)

    assert relogio.esperas == [WINDOW_SECONDS]


async def test_o_tempo_que_passou_sozinho_conta_a_favor() -> None:
    relogio = Relogio()
    limite = limitador(relogio, requests=1, tokens=10_000)
    await limite.acquire(500)
    relogio.agora += WINDOW_SECONDS  # a janela virou sem ninguém pedir nada

    await limite.acquire(500)

    assert relogio.esperas == []


async def test_espera_so_o_suficiente_para_o_excesso_caber() -> None:
    """Não espera a janela inteira esvaziar — só até liberar o que falta.

    Três requisições de 3K (9K usados, teto 10K); a próxima de 3K precisa de 2K livres,
    e a primeira sozinha já libera 3K. A espera é a da PRIMEIRA saindo, não a da última.
    """
    relogio = Relogio()
    limite = limitador(relogio, requests=100, tokens=10_000)
    await limite.acquire(3_000)
    relogio.agora += 10.0
    await limite.acquire(3_000)
    relogio.agora += 10.0
    await limite.acquire(3_000)

    await limite.acquire(3_000)

    # A primeira entrou 20s atrás: faltam 40s para ela sair da janela.
    assert relogio.esperas == [WINDOW_SECONDS - 20.0]


async def test_cota_zerada_significa_sem_limite() -> None:
    """Produção com chave paga não deve ser penalizada pelo default do harness."""
    relogio = Relogio()
    limite = limitador(relogio, requests=0, tokens=0)

    for _ in range(50):
        await limite.acquire(100_000)

    assert relogio.esperas == []
