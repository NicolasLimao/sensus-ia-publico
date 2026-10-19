import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import { Feedback } from "./Feedback";

describe("Feedback", () => {
  it("dispara o voto", async () => {
    const onRate = vi.fn();
    render(<Feedback rating={null} onRate={onRate} />);

    await userEvent.click(screen.getByRole("button", { name: /resposta útil/i }));

    expect(onRate).toHaveBeenCalledWith("up");
  });

  it("marca o voto atual como pressionado", () => {
    render(<Feedback rating="down" onRate={() => {}} />);

    expect(screen.getByRole("button", { name: /não ajudou/i })).toHaveAttribute(
      "aria-pressed",
      "true",
    );
  });
});
