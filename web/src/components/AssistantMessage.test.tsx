import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import type { Message } from "../types";
import { AssistantMessage } from "./AssistantMessage";

const base: Message = {
  id: "m1",
  role: "assistant",
  content: "Uma resposta.",
  sources: [{ id: "1", excerpt: "trecho", document: null, score: 0.3 }],
  chunksUsed: 1,
  feedback: null,
};

describe("AssistantMessage", () => {
  it("resposta fundamentada mostra fontes e feedback", () => {
    render(<AssistantMessage message={base} onRate={() => {}} />);

    expect(screen.getByText(/fundamentado em 1 trecho/i)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /resposta útil/i })).toBeInTheDocument();
  });

  it("resposta sem fundamento é um estado próprio, sem faixa de fontes", () => {
    render(
      <AssistantMessage
        message={{ ...base, chunksUsed: 0, sources: [], content: "Não encontrei na base." }}
        onRate={() => {}}
      />,
    );

    expect(screen.queryByText(/fundamentado em/i)).toBeNull();
    expect(screen.getByTestId("assistant-message")).toHaveAttribute("data-grounded", "false");
  });

  it("mensagem interrompida não oferece feedback", () => {
    render(<AssistantMessage message={{ ...base, interrupted: true }} onRate={() => {}} />);

    expect(screen.queryByRole("button", { name: /resposta útil/i })).toBeNull();
    expect(screen.getByText(/interrompida/i)).toBeInTheDocument();
  });
});
