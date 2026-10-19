import { useEffect, useState } from "react";
import { acceptInvite, previewInvite, type InvitePreview } from "../api/client";

/**
 * A tela de quem foi convidado.
 *
 * O token vem do FRAGMENTO da URL (`/convite#<token>`), que não é enviado a servidor
 * nenhum: fica fora do log do host estático, do `Referer` e de qualquer preview de
 * link. É por isso que o painel do admin monta o link com `#`, e não com `?`.
 *
 * Reaproveita as classes de `.auth-screen`: é o mesmo formato de formulário, e uma
 * folha de estilo paralela para duas telas gêmeas envelheceria em divergência.
 */
export function InviteScreen({ onAccepted }: { onAccepted: () => void }) {
  // Lido uma vez, na montagem: o token não muda enquanto a tela está aberta.
  const [token] = useState(() => window.location.hash.replace(/^#/, ""));
  const [preview, setPreview] = useState<InvitePreview | null>(null);
  const [recusa, setRecusa] = useState<string | null>(null);
  const [senha, setSenha] = useState("");
  const [erro, setErro] = useState<string | null>(null);
  const [enviando, setEnviando] = useState(false);

  useEffect(() => {
    // Sem token não há o que perguntar: o servidor responderia 422, e a mensagem dele
    // seria sobre validação de campo, não sobre o link que a pessoa recebeu. A frase
    // desse caso é derivada no render, abaixo — pôr um `setState` síncrono aqui daria
    // um ciclo de renderização a mais, para um valor que já se conhece na montagem.
    if (!token) return;
    void previewInvite(token).then((resultado) => {
      if (resultado.ok) setPreview(resultado.preview);
      else setRecusa(resultado.message);
    });
  }, [token]);

  async function enviar(evento: React.FormEvent) {
    evento.preventDefault();
    setEnviando(true);
    setErro(null);
    const falha = await acceptInvite(token, senha);
    setEnviando(false);
    if (falha) {
      setErro(falha);
      return;
    }
    onAccepted();
  }

  const linkMorto = token
    ? recusa
    : "Este link de convite está incompleto. Peça outro a quem convidou.";

  if (linkMorto) {
    return (
      <div className="auth-screen">
        <h1>Sensus IA</h1>
        <p className="error" role="alert">
          {linkMorto}
        </p>
      </div>
    );
  }

  // `null` é "ainda perguntando ao servidor" — mesmo contrato do `session` em App.tsx.
  if (!preview) return null;

  return (
    <div className="auth-screen">
      <h1>Sensus IA</h1>
      {/* O endereço vem do convite, e a tela nem pergunta: não existe entrada por onde
          outro entraria. Mostrá-lo é o que separa uma tela que confirma de uma que pede
          fé. */}
      <p className="invite-note">
        Você foi convidado para <strong>{preview.organizationName}</strong> como{" "}
        <strong>{preview.email}</strong>.
      </p>
      <form onSubmit={(evento) => void enviar(evento)}>
        <label>
          Senha
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
          Entrar na organização
        </button>
      </form>
    </div>
  );
}
