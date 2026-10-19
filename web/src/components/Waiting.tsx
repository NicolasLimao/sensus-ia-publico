import { useEffect, useState } from "react";
import type { Stage } from "../types";

const STAGE_LABEL: Record<Stage, string> = {
  retrieving: "procurando na base…",
  reranking: "lendo os trechos…",
  generating: "escrevendo a resposta…",
};

// Antes disso o texto do estágio basta; depois, o silêncio precisa de um número para
// não parecer travamento — a cauda medida chega a 71 s.
const ELAPSED_THRESHOLD_MS = 10_000;

export function Waiting({
  stage,
  startedAt,
  onStop,
}: {
  stage: Stage | null;
  startedAt: number;
  onStop: () => void;
}) {
  const [elapsedMs, setElapsedMs] = useState(0);

  useEffect(() => {
    const id = setInterval(() => setElapsedMs(Date.now() - startedAt), 1000);
    return () => clearInterval(id);
  }, [startedAt]);

  const label =
    elapsedMs >= ELAPSED_THRESHOLD_MS
      ? `ainda trabalhando · ${Math.round(elapsedMs / 1000)} s`
      : (stage && STAGE_LABEL[stage]) || "pensando…";

  return (
    <div className="waiting">
      <span className="waiting-label">{label}</span>
      <button type="button" className="waiting-stop" onClick={onStop}>
        parar
      </button>
    </div>
  );
}
