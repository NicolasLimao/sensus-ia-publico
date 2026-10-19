import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import { InvitePanel } from "./InvitePanel";

afterEach(() => vi.unstubAllGlobals());

/** Resposta de criação da API desde a 3e: id, endereço e prazo — nunca o token. */
function criado(email = "convidada@exemplo.invalido") {
  return new Response(
    JSON.stringify({ id: "i1", email, expires_at: "2026-09-21T12:00:00+00:00" }),
    { status: 201 },
  );
}

describe("InvitePanel", () => {
  it("manda o endereço digitado e confirma para quem foi", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async (_input: RequestInfo | URL, init?: RequestInit) => {
        if (init?.method === "POST") return criado();
        return new Response("[]");
      }),
    );
    render(<InvitePanel onClose={() => {}} />);

    await userEvent.type(screen.getByLabelText("E-mail"), "convidada@exemplo.invalido");
    await userEvent.click(screen.getByRole("button", { name: "Enviar convite" }));

    expect(
      await screen.findByText(/convite enviado para convidada@exemplo.invalido/i),
    ).toBeInTheDocument();
  });

  it("não mostra link nenhum depois de convidar", async () => {
    /* A propriedade da fatia, do lado do front: o token não existe fora do e-mail. */
    vi.stubGlobal(
      "fetch",
      vi.fn(async (_input: RequestInfo | URL, init?: RequestInit) =>
        init?.method === "POST" ? criado() : new Response("[]"),
      ),
    );
    render(<InvitePanel onClose={() => {}} />);

    await userEvent.type(screen.getByLabelText("E-mail"), "convidada@exemplo.invalido");
    await userEvent.click(screen.getByRole("button", { name: "Enviar convite" }));
    await screen.findByText(/convite enviado/i);

    expect(screen.queryByText(/\/convite#/)).toBeNull();
  });

  it("mostra a mensagem do servidor quando a pessoa já é membro", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async (_input: RequestInfo | URL, init?: RequestInit) =>
        init?.method === "POST"
          ? new Response(
              JSON.stringify({
                detail: {
                  code: "already_member",
                  message: "Essa pessoa já faz parte da organização.",
                },
              }),
              { status: 409 },
            )
          : new Response("[]"),
      ),
    );
    render(<InvitePanel onClose={() => {}} />);

    await userEvent.type(screen.getByLabelText("E-mail"), "ana@acme.com");
    await userEvent.click(screen.getByRole("button", { name: "Enviar convite" }));

    expect(await screen.findByRole("alert")).toHaveTextContent(/já faz parte/i);
  });

  it("lista os pendentes com o endereço de cada um, e revoga", async () => {
    /* O endereço é o que torna a lista útil: a pergunta do admin é "já chamei essa
       pessoa?". */
    let pendentes = [
      {
        id: "i1",
        email: "convidada@exemplo.invalido",
        created_at: "2026-09-12T10:00:00+00:00",
        expires_at: "2026-09-19T10:00:00+00:00",
      },
    ];
    const fetchSpy = vi.fn(async (_input: RequestInfo | URL, init?: RequestInit) => {
      if (init?.method === "DELETE") {
        pendentes = [];
        return new Response(null, { status: 204 });
      }
      return new Response(JSON.stringify(pendentes));
    });
    vi.stubGlobal("fetch", fetchSpy);
    render(<InvitePanel onClose={() => {}} />);

    expect(await screen.findByText(/convidada@exemplo.invalido/)).toBeInTheDocument();
    await userEvent.click(await screen.findByRole("button", { name: /revogar/i }));

    await waitFor(() =>
      expect(screen.getByText(/nenhum convite pendente/i)).toBeInTheDocument(),
    );
    expect(
      fetchSpy.mock.calls.some(
        ([url, init]) => String(url).endsWith("/invites/i1") && init?.method === "DELETE",
      ),
    ).toBe(true);
  });
});
