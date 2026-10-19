import { useEffect, useRef } from "react";
import type { Message, Rating } from "../types";
import { AssistantMessage } from "./AssistantMessage";
import { Markdown } from "./Markdown";

export function Thread({
  messages,
  streamingText,
  onRate,
}: {
  messages: Message[];
  streamingText: string;
  onRate: (messageId: string, rating: Rating) => void;
}) {
  const endRef = useRef<HTMLDivElement>(null);
  const stickRef = useRef(true);

  // O autoscroll gruda no fim e solta quando a pessoa rola para cima.
  useEffect(() => {
    function onScroll() {
      const distance =
        document.documentElement.scrollHeight - window.scrollY - window.innerHeight;
      stickRef.current = distance < 80;
    }
    window.addEventListener("scroll", onScroll, { passive: true });
    return () => window.removeEventListener("scroll", onScroll);
  }, []);

  useEffect(() => {
    if (stickRef.current) endRef.current?.scrollIntoView({ block: "end" });
  }, [messages, streamingText]);

  const last = messages[messages.length - 1];

  return (
    <div className="thread">
      {messages.map((message) =>
        message.role === "user" ? (
          <article key={message.id} className="message user">
            {message.content}
          </article>
        ) : (
          <AssistantMessage key={message.id} message={message} onRate={onRate} />
        ),
      )}
      {streamingText && (
        <article className="message assistant streaming">
          <Markdown text={streamingText} />
        </article>
      )}
      {/* A resposta é anunciada uma vez, ao completar — não token a token. */}
      <div aria-live="polite" className="visually-hidden">
        {!streamingText && last?.role === "assistant" ? last.content : ""}
      </div>
      <div ref={endRef} />
    </div>
  );
}
