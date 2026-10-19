import type { Rating } from "../types";

export function Feedback({
  rating,
  onRate,
}: {
  rating: Rating | null;
  onRate: (rating: Rating) => void;
}) {
  return (
    <div className="feedback">
      <button
        type="button"
        aria-label="Resposta útil"
        aria-pressed={rating === "up"}
        onClick={() => onRate("up")}
      >
        👍
      </button>
      <button
        type="button"
        aria-label="Não ajudou"
        aria-pressed={rating === "down"}
        onClick={() => onRate("down")}
      >
        👎
      </button>
    </div>
  );
}
