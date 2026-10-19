import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";
import { Sources } from "./Sources";

const sources = [
  { id: "8231", excerpt: "primeiro trecho", document: null, score: 0.31 },
  { id: "8232", excerpt: "segundo trecho", document: null, score: 0.28 },
];

describe("Sources", () => {
  it("anuncia em quantos trechos a resposta se fundamenta", () => {
    render(<Sources sources={sources} />);

    expect(screen.getByText(/fundamentado em 2 trechos/i)).toBeInTheDocument();
  });

  it("mostra os excerpts ao expandir", async () => {
    render(<Sources sources={sources} />);

    await userEvent.click(screen.getByRole("button", { name: /fundamentado em 2 trechos/i }));

    expect(screen.getByText("primeiro trecho")).toBeInTheDocument();
    expect(screen.getByText("segundo trecho")).toBeInTheDocument();
  });

  it("não apresenta o score como confiança", () => {
    render(<Sources sources={sources} />);

    expect(screen.queryByText(/confian/i)).toBeNull();
    expect(screen.queryByText(/31%/)).toBeNull();
  });

  it("não renderiza nada sem fontes", () => {
    const { container } = render(<Sources sources={[]} />);

    expect(container).toBeEmptyDOMElement();
  });
});
