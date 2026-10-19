import { useState, type KeyboardEvent } from "react";

export function Composer({
  disabled,
  onSend,
}: {
  disabled: boolean;
  onSend: (text: string) => void;
}) {
  const [text, setText] = useState("");

  function handleKeyDown(event: KeyboardEvent<HTMLTextAreaElement>) {
    if (event.key !== "Enter" || event.shiftKey) return;
    event.preventDefault();
    if (disabled) return;
    const value = text.trim();
    if (!value) return;
    onSend(value);
    setText("");
  }

  return (
    <div className="composer">
      <textarea
        aria-label="Sua pergunta"
        placeholder="Pergunte alguma coisa sobre a base…"
        rows={2}
        value={text}
        onChange={(event) => setText(event.target.value)}
        onKeyDown={handleKeyDown}
      />
    </div>
  );
}
