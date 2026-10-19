import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import { AiConfigPanel } from "./AiConfigPanel";

afterEach(() => {
  vi.unstubAllGlobals();
});

const LEITURA = {
  active: {
    briefing: "Somos a Acme.",
    generation_model: "gpt-4o-mini",
    generation_top_k: 5,
    max_context_chars: 12000,
    suggestions: ["Qual é o prazo?"],
  },
  versions: [
    {
      id: "v2",
      version: 2,
      is_active: true,
      created_at: "2026-09-20T12:00:00Z",
      created_by_email: "ana@acme.com",
      config: {
        briefing: "Somos a Acme.",
        generation_model: "gpt-4o-mini",
        generation_top_k: 5,
        max_context_chars: 12000,
        suggestions: ["Qual é o prazo?"],
      },
    },
    {
      id: "v1",
      version: 1,
      is_active: false,
      created_at: "2026-09-19T12:00:00Z",
      created_by_email: "ana@acme.com",
      config: {
        briefing: "versão antiga",
        generation_model: "gpt-4o-mini",
        generation_top_k: 5,
        max_context_chars: 12000,
        suggestions: [],
      },
    },
  ],
  has_more_versions: false,
  allowed_models: ["gpt-4o-mini", "gpt-4o"],
};

function respondeCom(...respostas: Response[]) {
  let chamada = 0;
  const espiao = vi.fn(
    async (_input: RequestInfo | URL, _init?: RequestInit) =>
      respostas[Math.min(chamada++, respostas.length - 1)]!,
  );
  vi.stubGlobal("fetch", espiao);
  return espiao;
}

const leitura = () => new Response(JSON.stringify(LEITURA));

describe("AiConfigPanel", () => {
  it("mostra a config ativa nos campos do formulário", async () => {
    respondeCom(leitura());

    render(<AiConfigPanel onClose={() => {}} onSalvo={() => {}} />);

    expect(await screen.findByLabelText(/briefing/i)).toHaveValue("Somos a Acme.");
    expect(screen.getByLabelText(/trechos/i)).toHaveValue(5);
  });

  it("monta o select de modelo com a lista que o servidor mandou", async () => {
    respondeCom(leitura());

    render(<AiConfigPanel onClose={() => {}} onSalvo={() => {}} />);

    const select = await screen.findByLabelText(/modelo/i);
    expect(within(select).getAllByRole("option").map((o) => o.textContent)).toEqual([
      "gpt-4o-mini",
      "gpt-4o",
    ]);
  });

  it("lista as versões e marca qual está ativa", async () => {
    respondeCom(leitura());

    render(<AiConfigPanel onClose={() => {}} onSalvo={() => {}} />);

    const historico = await screen.findByRole("list", { name: /versões/i });
    const itens = within(historico).getAllByRole("listitem");
    expect(itens[0]).toHaveTextContent(/versão 2/i);
    expect(itens[0]).toHaveTextContent(/ativa/i);
    expect(within(itens[1]!).getByRole("button", { name: /ativar/i })).toBeVisible();
  });

  it("avisa que salvar publica, antes de salvar", async () => {
    respondeCom(leitura());

    render(<AiConfigPanel onClose={() => {}} onSalvo={() => {}} />);

    expect(
      await screen.findByText(/salvar publica|passa a valer|entra no ar/i),
    ).toBeInTheDocument();
  });

  it("salva e avisa quem chamou, para a sessão recarregar as sugestões", async () => {
    // Leitura, POST, e a releitura que vem depois de salvar.
    respondeCom(leitura(), new Response("{}", { status: 201 }), leitura());
    const onSalvo = vi.fn();

    render(<AiConfigPanel onClose={() => {}} onSalvo={onSalvo} />);
    await userEvent.type(await screen.findByLabelText(/briefing/i), " Novo.");
    await userEvent.click(screen.getByRole("button", { name: /salvar/i }));

    expect(onSalvo).toHaveBeenCalled();
  });

  it("mostra a mensagem do servidor quando ele recusa", async () => {
    respondeCom(
      leitura(),
      new Response(
        JSON.stringify({
          detail: {
            code: "model_not_allowed",
            message: "Modelo não permitido. Use um destes: gpt-4o-mini, gpt-4o.",
          },
        }),
        { status: 422 },
      ),
    );

    render(<AiConfigPanel onClose={() => {}} onSalvo={() => {}} />);
    await userEvent.click(await screen.findByRole("button", { name: /salvar/i }));

    expect(await screen.findByRole("alert")).toHaveTextContent(/não permitido/i);
  });

  it("um 422 de validação não vira 'falha de rede'", async () => {
    // O 422 do pydantic traz `detail` como LISTA, e não como o objeto das recusas de
    // regra de negócio. Lido como objeto, virava a mensagem de rede — e o admin ficava
    // sem nada para corrigir, procurando um problema de conexão que não existia.
    respondeCom(
      leitura(),
      new Response(
        JSON.stringify({
          detail: [
            {
              type: "string_too_long",
              loc: ["body", "suggestions", 0],
              msg: "String should have at most 200 characters",
            },
          ],
        }),
        { status: 422 },
      ),
    );

    render(<AiConfigPanel onClose={() => {}} onSalvo={() => {}} />);
    await userEvent.click(await screen.findByRole("button", { name: /salvar/i }));

    const alerta = await screen.findByRole("alert");
    expect(alerta).toHaveTextContent(/campos/i);
    expect(alerta).not.toHaveTextContent(/serviço\. Tente/i);
  });

  it("linha em branco no campo de sugestões não vira sugestão", async () => {
    // Um `\n` final é o jeito natural de terminar a lista, e virava um item vazio: com
    // dez sugestões, o corpo saía com onze e o servidor recusava por quantidade.
    const espiao = respondeCom(leitura(), new Response("{}", { status: 201 }), leitura());

    render(<AiConfigPanel onClose={() => {}} onSalvo={() => {}} />);
    const sugestoes = await screen.findByLabelText(/sugestões/i);
    await userEvent.clear(sugestoes);
    await userEvent.type(sugestoes, "Uma?{enter}{enter}Outra?{enter}");
    await userEvent.click(screen.getByRole("button", { name: /salvar/i }));

    const corpo = JSON.parse(String(espiao.mock.calls[1]![1]!.body));
    expect(corpo.suggestions).toEqual(["Uma?", "Outra?"]);
  });

  it("não oferece carregar mais quando o servidor diz que acabou", async () => {
    respondeCom(leitura());

    render(<AiConfigPanel onClose={() => {}} onSalvo={() => {}} />);

    await screen.findByRole("list", { name: /versões/i });
    expect(screen.queryByRole("button", { name: /carregar mais/i })).toBeNull();
  });

  it("carregar mais pede a página seguinte e acrescenta ao histórico", async () => {
    // O histórico cresce sem teto, e o servidor manda uma página por vez. Sem o botão,
    // as versões mais antigas simplesmente não existiriam para quem usa a tela — e a
    // lista mentiria por omissão, dizendo "estas são as versões".
    const espiao = respondeCom(
      new Response(JSON.stringify({ ...LEITURA, has_more_versions: true })),
      new Response(
        JSON.stringify({
          ...LEITURA,
          versions: [{ ...LEITURA.versions[1]!, id: "v0", version: 0 }],
          has_more_versions: false,
        }),
      ),
    );

    render(<AiConfigPanel onClose={() => {}} onSalvo={() => {}} />);
    await userEvent.click(
      await screen.findByRole("button", { name: /carregar mais/i }),
    );

    const historico = await screen.findByRole("list", { name: /versões/i });
    expect(within(historico).getAllByRole("listitem")).toHaveLength(3);
    expect(String(espiao.mock.calls[1]![0])).toContain("offset=2");
  });

  it("ativar uma versão antiga recarrega a tela", async () => {
    const espiao = respondeCom(
      leitura(),
      new Response(null, { status: 204 }),
      leitura(),
    );

    render(<AiConfigPanel onClose={() => {}} onSalvo={() => {}} />);
    const historico = await screen.findByRole("list", { name: /versões/i });
    const itens = within(historico).getAllByRole("listitem");
    await userEvent.click(within(itens[1]!).getByRole("button", { name: /ativar/i }));

    expect(espiao.mock.calls.length).toBeGreaterThanOrEqual(3);
  });
});
