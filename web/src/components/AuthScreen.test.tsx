import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import { AuthScreen } from "./AuthScreen";

afterEach(() => {
  vi.unstubAllGlobals();
});

async function irParaCriacao() {
  await userEvent.click(
    await screen.findByRole("button", { name: "Criar uma organização" }),
  );
}

describe("AuthScreen, ramo de criar organização", () => {
  it("não pede senha: ela nasce na tela de confirmação", async () => {
    vi.stubGlobal("fetch", vi.fn());

    render(<AuthScreen onAuthenticated={() => {}} onEsqueciSenha={() => {}} />);
    await irParaCriacao();

    expect(screen.getByLabelText("Organização")).toBeInTheDocument();
    expect(screen.getByLabelText("E-mail")).toBeInTheDocument();
    expect(screen.queryByLabelText("Senha")).toBeNull();
  });

  it("troca o formulário pela frase fixa, sem repetir o endereço digitado", async () => {
    // A frase é a MESMA para endereço livre e endereço que já tem conta — o servidor
    // responde 202 nos dois, e ecoar o endereço faria da tela um oráculo de
    // conveniência para quem testa endereços alheios num computador emprestado.
    vi.stubGlobal("fetch", vi.fn(async () => new Response("{}", { status: 202 })));

    render(<AuthScreen onAuthenticated={() => {}} onEsqueciSenha={() => {}} />);
    await irParaCriacao();
    await userEvent.type(screen.getByLabelText("Organização"), "Acme");
    await userEvent.type(screen.getByLabelText("E-mail"), "nova@exemplo.invalido");
    await userEvent.click(screen.getByRole("button", { name: "Criar organização" }));

    expect(await screen.findByText(/link de confirmação está a caminho/i)).toBeInTheDocument();
    expect(screen.queryByText(/nova@exemplo.invalido/)).toBeNull();
  });

  it("manda organização e e-mail, e NENHUMA senha", async () => {
    const espiao = vi.fn(
      async (_entrada: RequestInfo | URL, _init?: RequestInit) =>
        new Response("{}", { status: 202 }),
    );
    vi.stubGlobal("fetch", espiao);

    render(<AuthScreen onAuthenticated={() => {}} onEsqueciSenha={() => {}} />);
    await irParaCriacao();
    await userEvent.type(screen.getByLabelText("Organização"), "Acme");
    await userEvent.type(screen.getByLabelText("E-mail"), "nova@exemplo.invalido");
    await userEvent.click(screen.getByRole("button", { name: "Criar organização" }));

    const corpo = JSON.parse(String(espiao.mock.calls[0]?.[1]?.body));
    expect(corpo).toEqual({
      organization_name: "Acme",
      email: "nova@exemplo.invalido",
    });
  });

  it("não avisa quem chamou: não há sessão a abrir ainda", async () => {
    const onAuthenticated = vi.fn();
    vi.stubGlobal("fetch", vi.fn(async () => new Response("{}", { status: 202 })));

    render(
      <AuthScreen onAuthenticated={onAuthenticated} onEsqueciSenha={() => {}} />,
    );
    await irParaCriacao();
    await userEvent.type(screen.getByLabelText("Organização"), "Acme");
    await userEvent.type(screen.getByLabelText("E-mail"), "nova@exemplo.invalido");
    await userEvent.click(screen.getByRole("button", { name: "Criar organização" }));

    await screen.findByText(/link de confirmação está a caminho/i);
    expect(onAuthenticated).not.toHaveBeenCalled();
  });

  it("entrar continua pedindo senha e abrindo sessão", async () => {
    const onAuthenticated = vi.fn();
    vi.stubGlobal(
      "fetch",
      vi.fn(
        async () =>
          new Response(JSON.stringify({ email: "ana@acme.com", role: "admin" })),
      ),
    );

    render(
      <AuthScreen onAuthenticated={onAuthenticated} onEsqueciSenha={() => {}} />,
    );
    await userEvent.type(screen.getByLabelText("E-mail"), "ana@acme.com");
    await userEvent.type(screen.getByLabelText("Senha"), "senha-bem-longa");
    await userEvent.click(screen.getByRole("button", { name: "Entrar" }));

    expect(onAuthenticated).toHaveBeenCalled();
  });
});
