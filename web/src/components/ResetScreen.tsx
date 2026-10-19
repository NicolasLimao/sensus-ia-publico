import { useEffect, useState } from "react";
import { previewReset, resetPassword } from "../api/client";

/**
 * A tela de quem clicou no link do e-mail.
 *
 * O token vem do FRAGMENTO (`/redefinir#<token>`), que não é enviado a servidor nenhum:
 * fica fora do log do host estático, do `Referer` e de qualquer preview de link. É por
 * isso que o e-mail monta o link com `#`, e não com `?`.
 *
 * A prévia roda antes do formulário para a tela não mentir por omissão: sem ela, a
 * pessoa digitaria a senha nova e só então descobriria que o link venceu.
 */
export function ResetScreen({ onRedefinida }: { onRedefinida: () => void }) {
  // Lido uma vez, na montagem: o token não muda enquanto a tela está aberta.
  const [token] = useState(() => window.location.hash.replace(/^#/, ""));
  const [email, setEmail] = useState<string | null>(null);
  const [recusa, setRecusa] = useState<string | null>(null);
  const [senha, setSenha] = useState("");
  const [erro, setErro] = useState<string | null>(null);
  const [enviando, setEnviando] = useState(false);

  useEffect(() => {
    // Sem token não há o que perguntar: o servidor responderia 422, e a mensagem dele
    // seria sobre validação de campo, não sobre o link que a pessoa recebeu. A frase
    // desse caso é derivada no render, abaixo — um `setState` síncrono aqui daria um
    // ciclo de renderização a mais para um valor já conhecido na montagem.
    if (!token) return;
    void previewReset(token).then((resultado) => {
      if (resultado.ok) setEmail(resultado.email);
      else setRecusa(resultado.message);
    });
  }, [token]);

  async function enviar(evento: React.FormEvent) {
    evento.preventDefault();
    setEnviando(true);
    setErro(null);
    const falha = await resetPassword(token, senha);
    setEnviando(false);
    if (falha) {
      setErro(falha);
      return;
    }
    onRedefinida();
  }

  const mensagem = token ? recusa : "Link incompleto: falta o código no endereço.";

  if (mensagem) {
    return (
      <div className="auth-screen">
        <h1>Redefinir senha</h1>
        <p className="error" role="alert">
          {mensagem}
        </p>
      </div>
    );
  }

  // A prévia ainda não voltou. Nada na tela é melhor que um formulário que pode sumir.
  if (email === null) return null;

  return (
    <div className="auth-screen">
      <h1>Redefinir senha</h1>
      <p>
        Escolhendo uma senha nova para <strong>{email}</strong>.
      </p>
      <form onSubmit={(evento) => void enviar(evento)}>
        <label>
          Nova senha
          <input
            type="password"
            value={senha}
            required
            minLength={12}
            autoComplete="new-password"
            onChange={(e) => setSenha(e.target.value)}
          />
        </label>

        {erro && (
          <p className="error" role="alert">
            {erro}
          </p>
        )}

        <button type="submit" disabled={enviando}>
          Redefinir senha
        </button>
      </form>
    </div>
  );
}
