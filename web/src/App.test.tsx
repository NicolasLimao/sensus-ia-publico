import { cleanup, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import App from "./App";

function sseResponse(body: string): Response {
  const encoder = new TextEncoder();
  const stream = new ReadableStream({
    start(controller) {
      controller.enqueue(encoder.encode(body));
      controller.close();
    },
  });
  return new Response(stream, { status: 200, headers: { "Content-Type": "text/event-stream" } });
}

function stubApi(sse: string) {
  vi.stubGlobal(
    "fetch",
    vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input);
      if (url.endsWith("/auth/me")) {
        return new Response(
          JSON.stringify({ email: "ana@acme.com", role: "admin", suggestions: ["Sugestão da base?"] }),
        );
      }
      if (url.endsWith("/conversation")) return new Response(JSON.stringify({ messages: [] }));
      if (url.endsWith("/chat")) return sseResponse(sse);
      return new Response(null, { status: 204 });
    }),
  );
}

afterEach(() => {
  // Desmonta os efeitos do React enquanto a API ainda está simulada.
  cleanup();
  vi.unstubAllGlobals();
});

describe("App", () => {
  it("mostra as sugestões vindas do adaptador e responde a uma pergunta", async () => {
    stubApi(
      'event: stage\ndata: {"stage":"retrieving"}\n\n' +
        'event: token\ndata: {"text":"Uma resposta."}\n\n' +
        'event: sources\ndata: {"sources":[{"id":"1","excerpt":"trecho","document":null,"score":0.3}]}\n\n' +
        'event: done\ndata: {"message_id":"m1","chunks_used":1,"score":0.3,"elapsed_ms":42}\n\n',
    );
    render(<App />);

    const suggestion = await screen.findByRole("button", { name: /sugestão da base/i });
    await userEvent.click(suggestion);

    // A resposta aparece duas vezes de propósito: na thread e na live region que a
    // anuncia ao leitor de tela. A asserção olha a mensagem em si.
    const answer = await screen.findByTestId("assistant-message");
    await waitFor(() => expect(within(answer).getByText("Uma resposta.")).toBeInTheDocument());
    expect(within(answer).getByText(/fundamentado em 1 trecho/i)).toBeInTheDocument();
  });

  it("mostra a mensagem de erro do adaptador e oferece tentar de novo", async () => {
    stubApi(
      'event: error\ndata: {"code":"upstream_timeout","message":"A busca demorou mais do que o esperado. Tente de novo."}\n\n',
    );
    render(<App />);

    await userEvent.type(
      await screen.findByRole("textbox", { name: /pergunta/i }),
      "pergunta?{Enter}",
    );

    await waitFor(() =>
      expect(screen.getByText(/a busca demorou mais do que o esperado/i)).toBeInTheDocument(),
    );
    expect(screen.getByRole("button", { name: /tentar de novo/i })).toBeInTheDocument();
  });

  it("mostra a tela de entrada quando /auth/me responde 401", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => new Response("{}", { status: 401 })),
    );

    render(<App />);

    expect(await screen.findByLabelText("E-mail")).toBeInTheDocument();
    expect(screen.queryByRole("textbox", { name: /pergunta/i })).not.toBeInTheDocument();
  });

  it("mostra o chat quando há sessão", async () => {
    stubApi("");

    render(<App />);

    expect(await screen.findByRole("textbox", { name: /pergunta/i })).toBeInTheDocument();
    expect(screen.queryByLabelText("E-mail")).not.toBeInTheDocument();
  });

  it("entrar leva ao chat", async () => {
    let autenticado = false;
    vi.stubGlobal(
      "fetch",
      vi.fn(async (input: RequestInfo | URL) => {
        const url = String(input);
        if (url.endsWith("/auth/login")) {
          autenticado = true;
          return new Response(JSON.stringify({ email: "ana@acme.com", role: "member" }));
        }
        if (url.endsWith("/auth/me")) {
          return autenticado
            ? new Response(
                JSON.stringify({ email: "ana@acme.com", role: "member", suggestions: [] }),
              )
            : new Response("{}", { status: 401 });
        }
        if (url.endsWith("/conversation")) return new Response(JSON.stringify({ messages: [] }));
        return new Response(null, { status: 204 });
      }),
    );
    render(<App />);

    await userEvent.type(await screen.findByLabelText("E-mail"), "ana@acme.com");
    await userEvent.type(screen.getByLabelText("Senha"), "senha-bem-longa");
    await userEvent.click(screen.getByRole("button", { name: /^entrar$/i }));

    expect(await screen.findByRole("textbox", { name: /pergunta/i })).toBeInTheDocument();
  });

  it("credencial errada mostra o erro sem dizer se o e-mail existe", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async (input: RequestInfo | URL) => {
        const url = String(input);
        if (url.endsWith("/auth/login")) {
          return new Response(
            JSON.stringify({
              detail: { code: "invalid_credentials", message: "E-mail ou senha inválidos." },
            }),
            { status: 401 },
          );
        }
        return new Response("{}", { status: 401 });
      }),
    );
    render(<App />);

    await userEvent.type(await screen.findByLabelText("E-mail"), "ana@acme.com");
    await userEvent.type(screen.getByLabelText("Senha"), "senha-errada-longa");
    await userEvent.click(screen.getByRole("button", { name: /^entrar$/i }));

    expect(await screen.findByText("E-mail ou senha inválidos.")).toBeInTheDocument();
  });

  it("o painel de convites só aparece para quem é admin", async () => {
    stubApi(""); // o /auth/me deste stub responde role: "admin"

    render(<App />);

    expect(await screen.findByRole("button", { name: /convidar/i })).toBeInTheDocument();
  });

  it("member não vê o botão de convidar", async () => {
    /* A checagem que VALE é a da borda (403 forbidden_role). Esta é conveniência de
       interface: esconder o que a pessoa não pode fazer. */
    vi.stubGlobal(
      "fetch",
      vi.fn(async (input: RequestInfo | URL) => {
        const url = String(input);
        if (url.endsWith("/auth/me")) {
          return new Response(
            JSON.stringify({ email: "bob@acme.com", role: "member", suggestions: [] }),
          );
        }
        if (url.endsWith("/conversation")) return new Response(JSON.stringify({ messages: [] }));
        return new Response(null, { status: 204 });
      }),
    );

    render(<App />);

    await screen.findByRole("textbox", { name: /pergunta/i });
    expect(screen.queryByRole("button", { name: /convidar/i })).not.toBeInTheDocument();
  });

  it("em /convite mostra a tela de convite, e não a de entrada", async () => {
    window.history.replaceState({}, "", "/convite#um-token");
    vi.stubGlobal(
      "fetch",
      vi.fn(async (input: RequestInfo | URL) => {
        if (String(input).endsWith("/invites/preview")) {
          return new Response(
            JSON.stringify({
              organization_name: "Acme",
              email: "bob@acme.com",
              expires_at: "2026-09-20T12:00:00+00:00",
            }),
          );
        }
        return new Response("{}", { status: 401 });
      }),
    );

    render(<App />);

    expect(
      await screen.findByRole("button", { name: /entrar na organização/i }),
    ).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /^entrar$/i })).not.toBeInTheDocument();
    window.history.replaceState({}, "", "/");
  });

  it("em /redefinir mostra a tela de redefinir, e não a de entrada", async () => {
    window.history.replaceState({}, "", "/redefinir#um-token");
    vi.stubGlobal(
      "fetch",
      vi.fn(async (input: RequestInfo | URL) => {
        if (String(input).endsWith("/auth/reset-password/preview")) {
          return new Response(JSON.stringify({ email: "dona@exemplo.invalido" }));
        }
        return new Response("{}", { status: 401 });
      }),
    );

    render(<App />);

    expect(
      await screen.findByRole("button", { name: "Redefinir senha" }),
    ).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /^entrar$/i })).not.toBeInTheDocument();
    window.history.replaceState({}, "", "/");
  });

  it("em /confirmar mostra a tela de confirmação, e não a de entrada", async () => {
    window.history.replaceState({}, "", "/confirmar#um-token");
    vi.stubGlobal(
      "fetch",
      vi.fn(async (input: RequestInfo | URL) => {
        if (String(input).endsWith("/auth/confirm-signup/preview")) {
          return new Response(
            JSON.stringify({
              organization_name: "Acme",
              email: "nova@exemplo.invalido",
            }),
          );
        }
        return new Response("{}", { status: 401 });
      }),
    );

    render(<App />);

    expect(
      await screen.findByRole("button", { name: "Criar organização" }),
    ).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /^entrar$/i })).not.toBeInTheDocument();
    window.history.replaceState({}, "", "/");
  });

  it("esqueci minha senha troca a tela de entrada pela de pedir o link", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => new Response("{}", { status: 401 })));

    render(<App />);
    await userEvent.click(
      await screen.findByRole("button", { name: "Esqueci minha senha" }),
    );

    expect(
      screen.getByRole("button", { name: "Enviar link" }),
    ).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /^entrar$/i })).not.toBeInTheDocument();
  });

  it("aceitar o convite leva ao chat", async () => {
    window.history.replaceState({}, "", "/convite#um-token");
    let aceitou = false;
    vi.stubGlobal(
      "fetch",
      vi.fn(async (input: RequestInfo | URL) => {
        const url = String(input);
        if (url.endsWith("/invites/preview")) {
          return new Response(
            JSON.stringify({
              organization_name: "Acme",
              email: "bob@acme.com",
              expires_at: "2026-09-20T12:00:00+00:00",
            }),
          );
        }
        if (url.endsWith("/invites/accept")) {
          aceitou = true;
          return new Response(JSON.stringify({ email: "bob@acme.com", role: "member" }), {
            status: 201,
          });
        }
        if (url.endsWith("/auth/me")) {
          return aceitou
            ? new Response(
                JSON.stringify({ email: "bob@acme.com", role: "member", suggestions: [] }),
              )
            : new Response("{}", { status: 401 });
        }
        if (url.endsWith("/conversation")) return new Response(JSON.stringify({ messages: [] }));
        return new Response(null, { status: 204 });
      }),
    );
    render(<App />);

    // Sem campo de e-mail desde a 3e: o endereço vem do convite.
    await userEvent.type(await screen.findByLabelText("Senha"), "senha-bem-longa");
    await userEvent.click(screen.getByRole("button", { name: /entrar na organização/i }));

    expect(await screen.findByRole("textbox", { name: /pergunta/i })).toBeInTheDocument();
    window.history.replaceState({}, "", "/");
  });

  it("salvar a config com o /auth/me fora do ar não desloga quem já entrou", async () => {
    // Reler a sessão depois de salvar é o que traz as sugestões novas para a tela
    // vazia. Uma falha de rede nessa releitura não é "não há sessão": o cookie
    // continua valendo, e mandar a pessoa de volta ao login perderia a conversa.
    let mes = 0;
    vi.stubGlobal(
      "fetch",
      vi.fn(async (input: RequestInfo | URL) => {
        const url = String(input);
        if (url.endsWith("/auth/me")) {
          mes += 1;
          if (mes > 1) throw new TypeError("Failed to fetch");
          return new Response(
            JSON.stringify({ email: "ana@acme.com", role: "admin", suggestions: [] }),
          );
        }
        if (url.endsWith("/conversation")) return new Response(JSON.stringify({ messages: [] }));
        // `includes`, e não `endsWith`: a leitura leva `?offset=` desde que o
        // histórico passou a vir paginado.
        if (url.includes("/ai-config")) {
          return new Response(
            JSON.stringify({
              active: {
                briefing: "Somos a Acme.",
                generation_model: "gpt-4o-mini",
                generation_top_k: 5,
                max_context_chars: 12000,
                suggestions: [],
              },
              versions: [],
              has_more_versions: false,
              allowed_models: ["gpt-4o-mini"],
            }),
          );
        }
        return new Response(null, { status: 204 });
      }),
    );
    render(<App />);
    await screen.findByRole("textbox", { name: /pergunta/i });

    await userEvent.click(screen.getByRole("button", { name: /configurar ia/i }));
    await screen.findByLabelText(/briefing/i);
    await userEvent.click(screen.getByRole("button", { name: /salvar e publicar/i }));

    await waitFor(() => expect(mes).toBe(2));
    expect(screen.getByRole("textbox", { name: /pergunta/i })).toBeInTheDocument();
  });

  it("sair devolve à tela de entrada", async () => {
    let autenticado = true;
    vi.stubGlobal(
      "fetch",
      vi.fn(async (input: RequestInfo | URL) => {
        const url = String(input);
        if (url.endsWith("/auth/logout")) {
          autenticado = false;
          return new Response(null, { status: 204 });
        }
        if (url.endsWith("/auth/me")) {
          return autenticado
            ? new Response(
                JSON.stringify({ email: "ana@acme.com", role: "admin", suggestions: [] }),
              )
            : new Response("{}", { status: 401 });
        }
        if (url.endsWith("/conversation")) return new Response(JSON.stringify({ messages: [] }));
        return new Response(null, { status: 204 });
      }),
    );
    render(<App />);
    await screen.findByRole("textbox", { name: /pergunta/i });

    await userEvent.click(screen.getByRole("button", { name: /sair/i }));

    expect(await screen.findByLabelText("E-mail")).toBeInTheDocument();
  });
});
