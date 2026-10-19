import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import { InviteScreen } from "./InviteScreen";

function comLink(hash: string) {
  window.history.replaceState({}, "", `/convite${hash}`);
}

/** A prévia desde a 3e: diz também PARA QUEM o convite foi feito. */
function previa(email = "convidada@exemplo.invalido") {
  return new Response(
    JSON.stringify({
      organization_name: "Acme",
      email,
      expires_at: "2026-09-20T12:00:00+00:00",
    }),
  );
}

afterEach(() => {
  vi.unstubAllGlobals();
  window.history.replaceState({}, "", "/");
});

describe("InviteScreen", () => {
  it("mostra para qual organização a pessoa foi convidada", async () => {
    comLink("#um-token");
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => previa()),
    );

    render(<InviteScreen onAccepted={() => {}} />);

    expect(await screen.findByText("Acme")).toBeInTheDocument();
  });

  it("diz para qual endereço o convite foi feito, e não pergunta", async () => {
    /* O campo de e-mail sumiu porque não há mais o que perguntar: o endereço vem do
       convite, e mostrá-lo é a diferença entre uma tela que confirma e uma que pede fé. */
    comLink("#um-token");
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => previa()),
    );

    render(<InviteScreen onAccepted={() => {}} />);

    expect(await screen.findByText(/convidada@exemplo.invalido/)).toBeInTheDocument();
    expect(screen.queryByLabelText("E-mail")).toBeNull();
  });

  it("mostra o motivo do servidor quando o link não vale, e não pede senha", async () => {
    comLink("#um-token");
    vi.stubGlobal(
      "fetch",
      vi.fn(
        async () =>
          new Response(
            JSON.stringify({
              detail: { code: "invite_used", message: "Este convite já foi usado." },
            }),
            { status: 410 },
          ),
      ),
    );

    render(<InviteScreen onAccepted={() => {}} />);

    expect(await screen.findByText("Este convite já foi usado.")).toBeInTheDocument();
    expect(screen.queryByLabelText("Senha")).not.toBeInTheDocument();
  });

  it("link sem token nem chega a perguntar ao servidor", async () => {
    comLink("");
    const fetchSpy = vi.fn(async () => new Response("{}"));
    vi.stubGlobal("fetch", fetchSpy);

    render(<InviteScreen onAccepted={() => {}} />);

    expect(await screen.findByRole("alert")).toBeInTheDocument();
    expect(fetchSpy).not.toHaveBeenCalled();
  });

  it("aceitar manda só o token e a senha, e avisa quem chamou", async () => {
    comLink("#um-token");
    const aceito = vi.fn();
    const fetchSpy = vi.fn(async (input: RequestInfo | URL, _init?: RequestInit) => {
      if (String(input).endsWith("/invites/accept")) {
        return new Response(
          JSON.stringify({ email: "convidada@exemplo.invalido", role: "member" }),
          { status: 201 },
        );
      }
      return previa();
    });
    vi.stubGlobal("fetch", fetchSpy);
    render(<InviteScreen onAccepted={aceito} />);

    await userEvent.type(await screen.findByLabelText("Senha"), "senha-bem-longa");
    await userEvent.click(screen.getByRole("button", { name: /entrar na organização/i }));

    await waitFor(() => expect(aceito).toHaveBeenCalled());
    const aceite = fetchSpy.mock.calls.find(([url]) =>
      String(url).endsWith("/invites/accept"),
    );
    expect(JSON.parse(String(aceite?.[1]?.body))).toEqual({
      token: "um-token",
      password: "senha-bem-longa",
    });
  });

  it("mostra o erro do servidor quando o e-mail já tem conta", async () => {
    /* Sobrou para conta de OUTRA organização: na própria, o convite nem é criado. */
    comLink("#um-token");
    vi.stubGlobal(
      "fetch",
      vi.fn(async (input: RequestInfo | URL) => {
        if (String(input).endsWith("/invites/accept")) {
          return new Response(
            JSON.stringify({
              detail: { code: "email_taken", message: "Este e-mail já tem conta." },
            }),
            { status: 409 },
          );
        }
        return previa();
      }),
    );
    render(<InviteScreen onAccepted={() => {}} />);

    await userEvent.type(await screen.findByLabelText("Senha"), "senha-bem-longa");
    await userEvent.click(screen.getByRole("button", { name: /entrar na organização/i }));

    expect(await screen.findByText("Este e-mail já tem conta.")).toBeInTheDocument();
  });
});
