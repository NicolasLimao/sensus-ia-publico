/**
 * As sugestões vêm da config do adaptador. Nunca hardcoded aqui: sugestão boa é
 * específica da base, e base é do cliente — chumbá-las no front faria conteúdo de
 * cliente entrar no repositório pela porta dos fundos.
 */
export function EmptyState({
  suggestions,
  onPick,
}: {
  suggestions: string[];
  onPick: (text: string) => void;
}) {
  return (
    <div className="empty-state">
      <p>Pergunte o que quiser sobre os documentos desta base.</p>
      {suggestions.length > 0 && (
        <ul className="suggestions">
          {suggestions.map((suggestion) => (
            <li key={suggestion}>
              <button type="button" onClick={() => onPick(suggestion)}>
                {suggestion}
              </button>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
