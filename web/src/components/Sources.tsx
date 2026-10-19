import { useState } from "react";
import type { Source } from "../types";

/**
 * Citação por trecho: a faixa afirma só o que o sistema sabe — estas são as passagens
 * de onde a resposta saiu. Sem marcador `[1]` por frase, porque o pipeline não produz
 * atribuição por sentença e fingir que produz seria pior do que não citar.
 */
export function Sources({ sources }: { sources: Source[] }) {
  const [open, setOpen] = useState(false);
  if (sources.length === 0) return null;

  const label = `Fundamentado em ${sources.length} ${sources.length === 1 ? "trecho" : "trechos"}`;

  return (
    <div className="sources">
      <button
        type="button"
        className="sources-toggle"
        aria-expanded={open}
        onClick={() => setOpen((value) => !value)}
      >
        {label}
      </button>
      {open && (
        <ul className="sources-list">
          {sources.map((source) => (
            <li key={source.id} className="sources-item">
              {/* Sem título de documento confiável, o excerpt é a citação. */}
              <p className="sources-excerpt">{source.excerpt}</p>
              <span className="sources-meta">
                {source.document ?? `trecho ${source.id}`} · similaridade{" "}
                {source.score.toFixed(2)}
              </span>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
