import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import { ConfirmScreen } from "./ConfirmScreen";

function comLink(hash: string) {
  window.history.replaceState({}, "", `/confirmar${hash}`);
}

afterEach(() => {
  vi.unstubAllGlobals();
  window.history.replaceState({}, "", "/");
});

const PREVIA_OK = () =>
  new Response(
    JSON.stringify({
      organization_name: "Acme",
      email: "nova@exemplo.invalido",
    }),
  );

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

describe("ConfirmScreen", () => {
  it("mostra a organização e o endereço antes de pedir a senha", async () => {
    comLink("#um-token");
    vi.stubGlobal("fetch", vi.fn(async () => PREVIA_OK()));

    render(<ConfirmScreen onConfirmado={() => {}} />);

    expect(await screen.findByText(/Acme/)).toBeInTheDocument();
    expect(screen.getByText(/nova@exemplo.invalido/)).toBeInTheDocument();
    expect(screen.getByLabelText("Senha")).toBeInTheDocument();
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
                code: "signup_expired",
                message: "Este link venceu. Comece o cadastro de novo.",
              },
            }),
            { status: 410 },
          ),
      ),
    );

    render(<ConfirmScreen onConfirmado={() => {}} />);

    expect(await screen.findByText(/venceu/i)).toBeInTheDocument();
    expect(screen.queryByLabelText("Senha")).toBeNull();
  });

  it("avisa quando o endereço não traz token, sem perguntar ao servidor", async () => {
    comLink("");
    const espiao = vi.fn();
    vi.stubGlobal("fetch", espiao);

    render(<ConfirmScreen onConfirmado={() => {}} />);

    expect(await screen.findByText(/link incompleto/i)).toBeInTheDocument();
    expect(espiao).not.toHaveBeenCalled();
  });

  it("confirma e avisa quem chamou", async () => {
    comLink("#um-token");
    previaEDepois(
      PREVIA_OK,
      () =>
        new Response(
          JSON.stringify({ email: "nova@exemplo.invalido", role: "admin" }),
          { status: 201 },
        ),
    );
    const onConfirmado = vi.fn();

    render(<ConfirmScreen onConfirmado={onConfirmado} />);
    await screen.findByLabelText("Senha");
    await userEvent.type(screen.getByLabelText("Senha"), "senha-bem-longa");
    await userEvent.click(screen.getByRole("button", { name: "Criar organização" }));

    expect(onConfirmado).toHaveBeenCalled();
  });

  it("mostra o erro do servidor sem sair da tela", async () => {
    comLink("#um-token");
    previaEDepois(
      PREVIA_OK,
      () =>
        new Response(
          JSON.stringify({
            detail: { code: "email_taken", message: "Este e-mail já tem conta." },
          }),
          { status: 409 },
        ),
    );

    render(<ConfirmScreen onConfirmado={() => {}} />);
    await screen.findByLabelText("Senha");
    await userEvent.type(screen.getByLabelText("Senha"), "senha-bem-longa");
    await userEvent.click(screen.getByRole("button", { name: "Criar organização" }));

    expect(await screen.findByRole("alert")).toHaveTextContent(/já tem conta/i);
  });
});
