import type { Message, Rating } from "../types";
import { Feedback } from "./Feedback";
import { Markdown } from "./Markdown";
import { Sources } from "./Sources";

export function AssistantMessage({
  message,
  onRate,
}: {
  message: Message;
  onRate: (messageId: string, rating: Rating) => void;
}) {
  const grounded = message.chunksUsed > 0;

  return (
    <article
      className={grounded ? "message assistant" : "message assistant ungrounded"}
      data-testid="assistant-message"
      data-grounded={String(grounded)}
    >
      <Markdown text={message.content} />
      {/* Sem fundamento é um estado, não um erro: nada de faixa de fontes. */}
      {grounded && <Sources sources={message.sources} />}
      {message.interrupted ? (
        // Sem `message_id` não há o que avaliar.
        <p className="message-interrupted">Resposta interrompida.</p>
      ) : (
        <Feedback rating={message.feedback} onRate={(rating) => onRate(message.id, rating)} />
      )}
    </article>
  );
}
