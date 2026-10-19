import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import { Composer } from "./Composer";

describe("Composer", () => {
  it("Enter envia e limpa o campo", async () => {
    const onSend = vi.fn();
    render(<Composer disabled={false} onSend={onSend} />);
    const field = screen.getByRole("textbox", { name: /pergunta/i });

    await userEvent.type(field, "minha pergunta{Enter}");

    expect(onSend).toHaveBeenCalledWith("minha pergunta");
    expect(field).toHaveValue("");
  });

  it("Shift+Enter quebra linha sem enviar", async () => {
    const onSend = vi.fn();
    render(<Composer disabled={false} onSend={onSend} />);
    const field = screen.getByRole("textbox", { name: /pergunta/i });

    await userEvent.type(field, "linha um{Shift>}{Enter}{/Shift}linha dois");

    expect(onSend).not.toHaveBeenCalled();
    expect(field).toHaveValue("linha um\nlinha dois");
  });

  it("não envia enquanto uma resposta está em curso", async () => {
    const onSend = vi.fn();
    render(<Composer disabled onSend={onSend} />);

    await userEvent.type(screen.getByRole("textbox", { name: /pergunta/i }), "x{Enter}");

    expect(onSend).not.toHaveBeenCalled();
  });
});
