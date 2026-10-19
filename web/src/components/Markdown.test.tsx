import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { Markdown } from "./Markdown";

describe("Markdown", () => {
  it("renderiza markdown básico", () => {
    render(<Markdown text="Um **negrito** aqui." />);

    expect(screen.getByText("negrito").tagName).toBe("STRONG");
  });

  it("renderiza bloco de código ainda aberto sem quebrar", () => {
    const { container } = render(<Markdown text={"Veja:\n\n```python\ndef f():"} />);

    expect(container.querySelector("pre code")).not.toBeNull();
    expect(container.textContent).toContain("def f():");
  });

  it("remove script injetado no texto do modelo", () => {
    const { container } = render(
      <Markdown text={"texto <script>window.hacked = true</script> fim"} />,
    );

    expect(container.querySelector("script")).toBeNull();
    expect(container.textContent).toContain("texto");
  });

  it("remove handler inline de evento", () => {
    const { container } = render(<Markdown text={'<img src="x" onerror="alert(1)">'} />);

    expect(container.querySelector("img")?.getAttribute("onerror")).toBeNull();
  });
});
