import { useCallback, useEffect, useRef, useState } from "react";
import {
  CLIENT_TIMEOUT_MS,
  fetchConversation,
  sendFeedback,
  streamChat,
} from "../api/client";
import type { Message, Rating, Source, Stage } from "../types";

interface ChatError {
  code: string;
  message: string;
}

const TIMEOUT_MESSAGE = "A resposta passou de 90 segundos e foi interrompida.";
const CONNECTION_MESSAGE = "A conexão caiu no meio da resposta.";

export function useChat() {
  const [messages, setMessages] = useState<Message[]>([]);
  const [stage, setStage] = useState<Stage | null>(null);
  const [isStreaming, setIsStreaming] = useState(false);
  const [streamingText, setStreamingText] = useState("");
  const [error, setError] = useState<ChatError | null>(null);
  const [lastQuestion, setLastQuestion] = useState<string | null>(null);
  const abortRef = useRef<AbortController | null>(null);
  // Distingue quem cortou o stream: a pessoa (parar) ou o relógio (90 s).
  const stoppedRef = useRef(false);
  const timedOutRef = useRef(false);

  useEffect(() => {
    void (async () => {
      const restored = await fetchConversation();
      // Se a pessoa já começou a perguntar enquanto a restauração vinha, a conversa
      // viva manda: restaurar nunca pode apagar o que já está na tela.
      setMessages((current) => (current.length === 0 ? restored : current));
    })();
  }, []);

  const send = useCallback(async (text: string) => {
    const question = text.trim();
    if (!question) return;

    const controller = new AbortController();
    abortRef.current = controller;
    stoppedRef.current = false;
    timedOutRef.current = false;
    // Timeout do cliente: 90 s, acima do pior caso medido.
    const timeoutId = setTimeout(() => {
      timedOutRef.current = true;
      controller.abort();
    }, CLIENT_TIMEOUT_MS);

    setError(null);
    setLastQuestion(question);
    setIsStreaming(true);
    setStreamingText("");
    setStage(null);
    setMessages((current) => [
      ...current,
      {
        id: `local-${Date.now()}`,
        role: "user",
        content: question,
        sources: [],
        chunksUsed: 0,
        feedback: null,
      },
    ]);

    let answer = "";
    let sources: Source[] = [];

    const finish = (message: Message) => {
      setMessages((current) => [...current, message]);
      setStreamingText("");
      setStage(null);
      setIsStreaming(false);
    };

    const finishInterrupted = () =>
      finish({
        id: `interrupted-${Date.now()}`,
        role: "assistant",
        content: answer,
        sources,
        chunksUsed: 0,
        feedback: null,
        interrupted: true,
      });

    try {
      for await (const event of streamChat(question, controller.signal)) {
        switch (event.type) {
          case "stage":
            setStage(event.stage);
            break;
          case "token":
            answer += event.text;
            setStreamingText(answer);
            break;
          case "sources":
            sources = event.sources;
            break;
          case "done":
            finish({
              id: event.messageId,
              role: "assistant",
              content: answer,
              sources,
              chunksUsed: event.chunksUsed,
              feedback: null,
            });
            return;
          case "error":
            setError({ code: event.code, message: event.message });
            // Preserva o parcial: apagar em silêncio é pior do que mostrar o que veio.
            finishInterrupted();
            return;
        }
      }
      // Stream terminou sem `done`: trata como interrupção, não como sucesso.
      finishInterrupted();
    } catch {
      // Abortar levanta aqui. Parar foi escolha da pessoa e não é erro; estourar o
      // tempo é.
      if (timedOutRef.current) {
        setError({ code: "client_timeout", message: TIMEOUT_MESSAGE });
      } else if (!stoppedRef.current) {
        setError({ code: "service_unavailable", message: CONNECTION_MESSAGE });
      }
      finishInterrupted();
    } finally {
      clearTimeout(timeoutId);
      abortRef.current = null;
    }
  }, []);

  const stop = useCallback(() => {
    stoppedRef.current = true;
    abortRef.current?.abort();
  }, []);

  const retry = useCallback(async () => {
    if (lastQuestion) await send(lastQuestion);
  }, [lastQuestion, send]);

  const rate = useCallback(
    async (messageId: string, rating: Rating) => {
      const apply = (value: Rating | null) =>
        setMessages((current) =>
          current.map((m) => (m.id === messageId ? { ...m, feedback: value } : m)),
        );
      const previous = messages.find((m) => m.id === messageId)?.feedback ?? null;
      apply(rating);
      try {
        await sendFeedback(messageId, rating);
      } catch {
        apply(previous);
      }
    },
    [messages],
  );

  return {
    messages,
    stage,
    isStreaming,
    streamingText,
    error,
    lastQuestion,
    send,
    stop,
    retry,
    rate,
  };
}
