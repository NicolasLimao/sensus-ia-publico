import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import { ResetScreen } from "./ResetScreen";

function comLink(hash: string) {
  window.history.replaceState({}, "", `/redefinir${hash}`);
}

afterEach(() => {
  vi.unstubAllGlobals();
  window.history.replaceState({}, "", "/");
});

const PREVIA_OK = () =>
  new Response(JSON.stringify({ email: "dona@exemplo.invalido" }));

function previaEDepois(previa: () => Response, depois: () => Response) {
  let primeira = true;
  vi.stubGlobal(
    "fetch",
    vi.fn(async () => {
      if (primeira) {
        primeira = false;
        return previa();
      }
      return depois();
    }),
  );
}

describe("ResetScreen", () => {
  it("mostra de quem é a conta antes de pedir a senha", async () => {
    comLink("#um-token");
    vi.stubGlobal("fetch", vi.fn(async () => PREVIA_OK()));

    render(<ResetScreen onRedefinida={() => {}} />);

    expect(await screen.findByText(/dona@exemplo.invalido/)).toBeInTheDocument();
    expect(screen.getByLabelText("Nova senha")).toBeInTheDocument();
  });

  it("mostra o motivo do servidor quando o link não vale, e não pede senha", async () => {
    comLink("#um-token");
    vi.stubGlobal(
      "fetch",
      vi.fn(
        async () =>
          new Response(
            JSON.stringify({
              detail: {
                code: "reset_superseded",
                message: "Este link foi substituído. Abra o e-mail mais recente.",
              },
            }),
            { status: 410 },
          ),
      ),
    );

    render(<ResetScreen onRedefinida={() => {}} />);

    expect(await screen.findByText(/substituído/i)).toBeInTheDocument();
    expect(screen.queryByLabelText("Nova senha")).toBeNull();
  });

  it("avisa quando o endereço não traz token, sem perguntar ao servidor", async () => {
    comLink("");
    const espiao = vi.fn();
    vi.stubGlobal("fetch", espiao);

    render(<ResetScreen onRedefinida={() => {}} />);

    expect(await screen.findByText(/link incompleto/i)).toBeInTheDocument();
    expect(espiao).not.toHaveBeenCalled();
  });

  it("redefine e avisa quem chamou", async () => {
    comLink("#um-token");
    previaEDepois(
      PREVIA_OK,
      () =>
        new Response(JSON.stringify({ email: "dona@exemplo.invalido", role: "admin" })),
    );
    const onRedefinida = vi.fn();

    render(<ResetScreen onRedefinida={onRedefinida} />);
    await screen.findByLabelText("Nova senha");
    await userEvent.type(screen.getByLabelText("Nova senha"), "senha-bem-longa");
    await userEvent.click(screen.getByRole("button", { name: "Redefinir senha" }));

    expect(onRedefinida).toHaveBeenCalled();
  });

  it("mostra o erro do servidor sem sair da tela", async () => {
    comLink("#um-token");
    previaEDepois(
      PREVIA_OK,
      () =>
        new Response(
          JSON.stringify({
            detail: {
              code: "reset_used",
              message: "Este link já foi usado. Faça login com a senha nova.",
            },
          }),
          { status: 410 },
        ),
    );

    render(<ResetScreen onRedefinida={() => {}} />);
    await screen.findByLabelText("Nova senha");
    await userEvent.type(screen.getByLabelText("Nova senha"), "senha-bem-longa");
    await userEvent.click(screen.getByRole("button", { name: "Redefinir senha" }));

    expect(await screen.findByRole("alert")).toHaveTextContent(/já foi usado/i);
  });
});
