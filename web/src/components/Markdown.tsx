import DOMPurify from "dompurify";
import { marked } from "marked";
import { useMemo } from "react";

marked.setOptions({ gfm: true, breaks: true });

/**
 * Renderiza markdown vindo do modelo.
 *
 * Duas exigências: tolerar bloco de código ainda aberto (o texto chega token a token)
 * e sanitizar sempre — texto de LLM sobre documento de terceiro é superfície de XSS.
 */
export function Markdown({ text }: { text: string }) {
  const html = useMemo(() => {
    // Uma cerca ímpar significa bloco em aberto; fechar aqui evita que o resto da
    // resposta seja engolido enquanto ela ainda está chegando.
    const fences = (text.match(/```/g) ?? []).length;
    const balanced = fences % 2 === 1 ? `${text}\n\`\`\`` : text;
    const raw = marked.parse(balanced, { async: false });
    return DOMPurify.sanitize(raw, { USE_PROFILES: { html: true } });
  }, [text]);

  return <div className="markdown" dangerouslySetInnerHTML={{ __html: html }} />;
}
