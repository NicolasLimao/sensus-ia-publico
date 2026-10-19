import { afterEach, describe, expect, it, vi } from "vitest";
import { fetchMe, login, sendFeedback, streamChat } from "./client";

function sseResponse(body: string): Response {
  const encoder = new TextEncoder();
  const stream = new ReadableStream({
    start(controller) {
      controller.enqueue(encoder.encode(body));
      controller.close();
    },
  });
  return new Response(stream, {
    status: 200,
    headers: { "Content-Type": "text/event-stream" },
  });
}

afterEach(() => vi.unstubAllGlobals());

describe("client", () => {
  it("fetchMe devolve a sessão do adaptador", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(
        async () =>
          new Response(
            JSON.stringify({ email: "ana@acme.com", role: "admin", suggestions: ["A?"] }),
          ),
      ),
    );

    expect(await fetchMe()).toEqual({
      email: "ana@acme.com",
      role: "admin",
      suggestions: ["A?"],
    });
  });

  it("fetchMe devolve null sem sessão, em vez de estourar", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => new Response("{}", { status: 401 })),
    );

    expect(await fetchMe()).toBeNull();
  });

  it("login devolve a mensagem do servidor, sem reescrevê-la", async () => {
    // É o servidor que decide não revelar se o e-mail existe; o front repete.
    vi.stubGlobal(
      "fetch",
      vi.fn(
        async () =>
          new Response(
            JSON.stringify({
              detail: { code: "invalid_credentials", message: "E-mail ou senha inválidos." },
            }),
            { status: 401 },
          ),
      ),
    );

    expect(await login("ana@acme.com", "errada")).toBe("E-mail ou senha inválidos.");
  });

  it("login devolve null quando dá certo", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => new Response(JSON.stringify({ email: "ana@acme.com", role: "admin" }))),
    );

    expect(await login("ana@acme.com", "certa")).toBeNull();
  });

  it("streamChat converte SSE em eventos tipados em camelCase", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () =>
        sseResponse(
          'event: stage\ndata: {"stage":"retrieving"}\n\n' +
            'event: token\ndata: {"text":"oi"}\n\n' +
            'event: sources\ndata: {"sources":[{"id":"1","excerpt":"t","document":null,"score":0.3}]}\n\n' +
            'event: done\ndata: {"message_id":"m1","chunks_used":1,"score":0.3,"elapsed_ms":42}\n\n',
        ),
      ),
    );

    const events = [];
    for await (const event of streamChat("pergunta?", new AbortController().signal)) {
      events.push(event);
    }

    expect(events[0]).toEqual({ type: "stage", stage: "retrieving" });
    expect(events[1]).toEqual({ type: "token", text: "oi" });
    expect(events[3]).toEqual({
      type: "done",
      messageId: "m1",
      chunksUsed: 1,
      score: 0.3,
      elapsedMs: 42,
    });
  });

  it("streamChat emite evento de erro quando a resposta não é 200", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => new Response("nao", { status: 503 })),
    );

    const events = [];
    for await (const event of streamChat("q", new AbortController().signal)) events.push(event);

    expect(events).toEqual([
      { type: "error", code: "service_unavailable", message: expect.any(String) },
    ]);
  });

  it("sendFeedback rejeita quando o servidor recusa", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => new Response(null, { status: 404 })),
    );

    await expect(sendFeedback("m1", "up")).rejects.toThrow();
  });
});
