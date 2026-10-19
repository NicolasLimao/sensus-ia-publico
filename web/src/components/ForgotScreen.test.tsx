import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import { ForgotScreen } from "./ForgotScreen";

afterEach(() => vi.unstubAllGlobals());

function respondeCom(resposta: () => Response) {
  const espiao = vi.fn(async () => resposta());
  vi.stubGlobal("fetch", espiao);
  return espiao;
}

const ACEITO = () =>
  new Response(
    JSON.stringify({
      status: "sent",
      message: "Se houver conta com esse e-mail, o link está a caminho.",
    }),
    { status: 202 },
  );

async function pedirPara(email: string) {
  await userEvent.type(screen.getByLabelText("E-mail"), email);
  await userEvent.click(screen.getByRole("button", { name: "Enviar link" }));
}

describe("ForgotScreen", () => {
  it("mostra a confirmação do servidor quando o e-mail tem conta", async () => {
    respondeCom(ACEITO);
    render(<ForgotScreen onVoltar={() => {}} />);

    await pedirPara("dona@exemplo.invalido");

    expect(
      await screen.findByText(/se houver conta com esse e-mail/i),
    ).toBeInTheDocument();
  });

  it("mostra a MESMA confirmação quando o e-mail não tem conta", async () => {
    // O servidor responde 202 idêntico nos dois casos, e a tela não pode desfazer o
    // que esse 202 protege — por isso não existe um ramo de "não encontrado" aqui.
    respondeCom(ACEITO);
    render(<ForgotScreen onVoltar={() => {}} />);

    await pedirPara("ninguem@exemplo.invalido");

    expect(
      await screen.findByText(/se houver conta com esse e-mail/i),
    ).toBeInTheDocument();
  });

  it("não deixa a tela mostrar o e-mail de volta na confirmação", async () => {
    // Ecoar o endereço digitado transformaria a tela num oráculo de conveniência para
    // quem testa endereços alheios num computador emprestado.
    respondeCom(ACEITO);
    render(<ForgotScreen onVoltar={() => {}} />);

    await pedirPara("dona@exemplo.invalido");
    await screen.findByText(/se houver conta com esse e-mail/i);

    expect(screen.queryByText(/dona@exemplo.invalido/)).toBeNull();
  });

  it("mostra o erro quando a rede falha", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => {
        throw new Error("sem rede");
      }),
    );
    render(<ForgotScreen onVoltar={() => {}} />);

    await pedirPara("dona@exemplo.invalido");

    expect(await screen.findByRole("alert")).toBeInTheDocument();
  });

  it("volta para a tela de entrada", async () => {
    const onVoltar = vi.fn();
    render(<ForgotScreen onVoltar={onVoltar} />);

    await userEvent.click(screen.getByRole("button", { name: "Voltar" }));

    expect(onVoltar).toHaveBeenCalled();
  });
});
