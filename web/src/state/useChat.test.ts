import { act, renderHook, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { ChatEvent } from "../types";
import { useChat } from "./useChat";

vi.mock("../api/client", () => ({
  fetchConversation: vi.fn(async () => []),
  sendFeedback: vi.fn(async () => undefined),
  streamChat: vi.fn(),
  CLIENT_TIMEOUT_MS: 90_000,
}));

import { fetchConversation, sendFeedback, streamChat } from "../api/client";

function scripted(events: ChatEvent[]) {
  return vi.mocked(streamChat).mockImplementation(async function* () {
    for (const event of events) yield event;
  });
}

afterEach(() => vi.clearAllMocks());

describe("useChat", () => {
  it("restaura a conversa no boot", async () => {
    vi.mocked(fetchConversation).mockResolvedValueOnce([
      { id: "m1", role: "user", content: "antiga?", sources: [], chunksUsed: 0, feedback: null },
    ]);

    const { result } = renderHook(() => useChat());

    // As sugestões saíram daqui na fatia 3a: vêm de `/auth/me`, junto do papel, e
    // quem as segura é o `App`. O boot do chat restaura só a conversa.
    await waitFor(() => expect(result.current.messages).toHaveLength(1));
    expect(result.current.messages[0]?.content).toBe("antiga?");
  });

  it("acumula tokens e fecha a mensagem no done", async () => {
    scripted([
      { type: "stage", stage: "retrieving" },
      { type: "token", text: "Uma " },
      { type: "token", text: "resposta." },
      { type: "sources", sources: [{ id: "1", excerpt: "t", document: null, score: 0.3 }] },
      { type: "done", messageId: "m9", chunksUsed: 1, score: 0.3, elapsedMs: 10 },
    ]);
    const { result } = renderHook(() => useChat());

    await act(async () => {
      await result.current.send("pergunta?");
    });

    const [pergunta, resposta] = result.current.messages;
    expect(pergunta?.content).toBe("pergunta?");
    expect(resposta).toMatchObject({ id: "m9", content: "Uma resposta.", chunksUsed: 1 });
    expect(result.current.isStreaming).toBe(false);
    expect(result.current.streamingText).toBe("");
  });

  it("marca chunksUsed 0 como resposta sem fundamento, não como erro", async () => {
    scripted([
      { type: "token", text: "Não encontrei isso na base." },
      { type: "sources", sources: [] },
      { type: "done", messageId: "m0", chunksUsed: 0, score: null, elapsedMs: 5 },
    ]);
    const { result } = renderHook(() => useChat());

    await act(async () => {
      await result.current.send("pergunta?");
    });

    expect(result.current.error).toBeNull();
    expect(result.current.messages[1]?.chunksUsed).toBe(0);
  });

  it("preserva o texto parcial quando o stream falha no meio", async () => {
    scripted([
      { type: "token", text: "comecei a " },
      { type: "error", code: "upstream_timeout", message: "demorou" },
    ]);
    const { result } = renderHook(() => useChat());

    await act(async () => {
      await result.current.send("pergunta?");
    });

    const parcial = result.current.messages[1];
    expect(parcial?.content).toBe("comecei a ");
    expect(parcial?.interrupted).toBe(true);
    expect(result.current.error?.code).toBe("upstream_timeout");
    expect(result.current.lastQuestion).toBe("pergunta?");
  });

  it("parar preserva o parcial e não mostra erro — desistir não é falha", async () => {
    vi.mocked(streamChat).mockImplementation(async function* (_message, signal) {
      yield { type: "token", text: "comecei" } satisfies ChatEvent;
      await new Promise<void>((_resolve, reject) => {
        signal.addEventListener("abort", () => reject(new Error("AbortError")));
      });
    });
    const { result } = renderHook(() => useChat());

    let sending: Promise<void> = Promise.resolve();
    act(() => {
      sending = result.current.send("pergunta?");
    });
    await waitFor(() => expect(result.current.streamingText).toBe("comecei"));

    act(() => result.current.stop());
    await act(async () => {
      await sending;
    });

    expect(result.current.error).toBeNull();
    expect(result.current.messages[1]).toMatchObject({ content: "comecei", interrupted: true });
    expect(result.current.isStreaming).toBe(false);
  });

  it("rate atualiza otimista e faz rollback quando o servidor recusa", async () => {
    scripted([
      { type: "token", text: "ok" },
      { type: "sources", sources: [] },
      { type: "done", messageId: "m9", chunksUsed: 1, score: null, elapsedMs: 5 },
    ]);
    vi.mocked(sendFeedback).mockRejectedValueOnce(new Error("recusado"));
    const { result } = renderHook(() => useChat());
    await act(async () => {
      await result.current.send("pergunta?");
    });

    await act(async () => {
      await result.current.rate("m9", "up");
    });

    expect(result.current.messages[1]?.feedback).toBeNull();
  });
});
