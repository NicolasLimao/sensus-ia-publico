import { useEffect, useState } from "react";
import {
  activateAiConfig,
  fetchAiConfig,
  saveAiConfig,
  type AiConfigValues,
  type AiConfigVersion,
} from "../api/client";

/**
 * O painel de configuração de IA do admin.
 *
 * O que NÃO está aqui, e é a decisão central da fatia 4a: as regras anti-alucinação. O
 * campo é um **briefing** — quem é a organização, como falar, o que priorizar — que o
 * servidor insere depois das regras fixas e subordinado a elas. Não há como editar a
 * regra que manda recusar sem fundamento, e é isso que mantém a promessa do produto
 * sendo uma promessa.
 *
 * A lista de modelos vem do servidor, e não daqui: repetir os nomes neste código
 * garantiria divergência no dia em que um deles saísse.
 *
 * Tudo é relido do servidor depois de cada ação — ele é a autoridade sobre qual versão
 * está ativa, e um estado local otimista discordaria dele na primeira falha. Mesma
 * escolha do `InvitePanel`.
 */
export function AiConfigPanel({
  onClose,
  onSalvo,
}: {
  onClose: () => void;
  onSalvo: () => void;
}) {
  const [valores, setValores] = useState<AiConfigValues | null>(null);
  const [versoes, setVersoes] = useState<AiConfigVersion[]>([]);
  const [temMais, setTemMais] = useState(false);
  const [modelos, setModelos] = useState<string[]>([]);
  const [erro, setErro] = useState<string | null>(null);
  const [salvando, setSalvando] = useState(false);

  function aplicar(lido: Awaited<ReturnType<typeof fetchAiConfig>>): void {
    if (lido === null) {
      // Sem preencher o formulário com palpite: um default inventado aqui faria o
      // admin salvar o palpite achando que era o que estava valendo.
      setErro("Não foi possível carregar a configuração.");
      return;
    }
    setValores(lido.active);
    setVersoes(lido.versions);
    setTemMais(lido.hasMoreVersions);
    setModelos(lido.allowedModels);
  }

  /** A página seguinte do histórico, pedida a partir do que já está na tela.
   *
   * O `offset` é quantas versões a lista já tem, e não um número de página: assim a
   * tela não precisa conhecer o tamanho da página do servidor. Salvar e ativar voltam
   * para a primeira página de propósito — depois de mudar o que está valendo, a
   * verdade é o que o servidor devolve agora, e não a colagem do que foi carregado
   * antes. */
  async function carregarMais(): Promise<void> {
    const proxima = await fetchAiConfig(versoes.length);
    if (proxima === null) {
      setErro("Não foi possível carregar mais versões.");
      return;
    }
    // Uma versão nova criada entre as duas leituras desloca a janela, e a página
    // seguinte pode repetir o que já está na tela. Repetir chave em lista é defeito de
    // renderização; descartar o repetido é o que mantém a lista honesta.
    const conhecidas = new Set(versoes.map((v) => v.id));
    setVersoes([...versoes, ...proxima.versions.filter((v) => !conhecidas.has(v.id))]);
    setTemMais(proxima.hasMoreVersions);
  }

  async function recarregar(): Promise<void> {
    aplicar(await fetchAiConfig());
  }

  useEffect(() => {
    // Encadeado, e não `await` dentro do efeito: é a forma que o `InvitePanel` já usa,
    // e a que deixa explícito que nenhum `setState` acontece de forma síncrona aqui.
    void fetchAiConfig().then(aplicar);
  }, []);

  async function salvar(evento: React.FormEvent) {
    evento.preventDefault();
    if (valores === null) return;
    setSalvando(true);
    setErro(null);
    const falha = await saveAiConfig(valores);
    setSalvando(false);
    if (falha) {
      // A frase vem do servidor: é ele que sabe quais modelos valem hoje.
      setErro(falha);
      return;
    }
    await recarregar();
    // As sugestões da tela vazia saem desta config: sem avisar, o chat ficaria com as
    // antigas até a próxima carga da página.
    onSalvo();
  }

  async function ativar(id: string) {
    const falha = await activateAiConfig(id);
    if (falha) {
      setErro(falha);
      return;
    }
    await recarregar();
    onSalvo();
  }

  if (valores === null) {
    return (
      <section className="invite-panel" aria-label="Configuração de IA">
        <header>
          <h2>Configuração de IA</h2>
          <button type="button" onClick={onClose}>
            Fechar
          </button>
        </header>
        {erro && (
          <p className="error" role="alert">
            {erro}
          </p>
        )}
      </section>
    );
  }

  return (
    <section className="invite-panel" aria-label="Configuração de IA">
      <header>
        <h2>Configuração de IA</h2>
        <button type="button" onClick={onClose}>
          Fechar
        </button>
      </header>

      <form onSubmit={(evento) => void salvar(evento)}>
        <label>
          Briefing
          <textarea
            value={valores.briefing}
            maxLength={4000}
            rows={6}
            onChange={(e) => setValores({ ...valores, briefing: e.target.value })}
          />
        </label>
        <p className="hint">
          {valores.briefing.length}/4000 · descreve a organização e o tom das respostas.
          As regras que impedem o assistente de responder sem base nos documentos são
          fixas e não podem ser alteradas aqui.
        </p>

        <label>
          Modelo
          <select
            value={valores.generationModel}
            onChange={(e) =>
              setValores({ ...valores, generationModel: e.target.value })
            }
          >
            {modelos.map((modelo) => (
              <option key={modelo} value={modelo}>
                {modelo}
              </option>
            ))}
          </select>
        </label>

        <label>
          Trechos por resposta
          <input
            type="number"
            min={1}
            max={20}
            value={valores.generationTopK}
            onChange={(e) =>
              setValores({ ...valores, generationTopK: Number(e.target.value) })
            }
          />
        </label>

        <label>
          Limite de contexto (caracteres)
          <input
            type="number"
            min={1000}
            max={50000}
            step={500}
            value={valores.maxContextChars}
            onChange={(e) =>
              setValores({ ...valores, maxContextChars: Number(e.target.value) })
            }
          />
        </label>

        <label>
          Sugestões da tela inicial
          <textarea
            value={valores.suggestions.join("\n")}
            rows={4}
            onChange={(e) =>
              setValores({ ...valores, suggestions: e.target.value.split("\n") })
            }
          />
        </label>
        <p className="hint">Uma por linha, no máximo dez.</p>

        {erro && (
          <p className="error" role="alert">
            {erro}
          </p>
        )}

        {/* Dito ANTES de salvar: "salvar" que publica e "salvar" que guarda rascunho
            são coisas diferentes, e confundir as duas vira suporte. */}
        <p className="hint">Salvar publica: a nova versão passa a valer no chat.</p>
        <button type="submit" disabled={salvando}>
          Salvar e publicar
        </button>
      </form>

      <h3>Versões</h3>
      <ul aria-label="Versões">
        {versoes.map((versao) => (
          <li key={versao.id}>
            <span>
              Versão {versao.version}
              {versao.isActive ? " · ativa" : ""}
              {versao.createdByEmail ? ` · ${versao.createdByEmail}` : ""}
            </span>
            {!versao.isActive && (
              <button type="button" onClick={() => void ativar(versao.id)}>
                Ativar
              </button>
            )}
          </li>
        ))}
      </ul>
      {temMais && (
        <button type="button" onClick={() => void carregarMais()}>
          Carregar mais versões
        </button>
      )}
    </section>
  );
}
