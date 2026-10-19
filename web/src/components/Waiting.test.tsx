import { act, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import { Waiting } from "./Waiting";

afterEach(() => vi.useRealTimers());

describe("Waiting", () => {
  it("descreve o estágio em português", () => {
    render(<Waiting stage="retrieving" startedAt={Date.now()} onStop={() => {}} />);

    expect(screen.getByText(/procurando na base/i)).toBeInTheDocument();
  });

  it("mostra o tempo decorrido depois de 10 s sem token", () => {
    vi.useFakeTimers();
    const startedAt = Date.now();
    render(<Waiting stage="generating" startedAt={startedAt} onStop={() => {}} />);

    act(() => {
      vi.advanceTimersByTime(23_000);
    });

    expect(screen.getByText(/ainda trabalhando · 23 s/i)).toBeInTheDocument();
  });

  it("oferece parar", async () => {
    const onStop = vi.fn();
    render(<Waiting stage="retrieving" startedAt={Date.now()} onStop={onStop} />);

    await userEvent.click(screen.getByRole("button", { name: /parar/i }));

    expect(onStop).toHaveBeenCalled();
  });
});
