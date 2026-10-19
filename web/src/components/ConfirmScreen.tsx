import { useEffect, useState } from "react";
import { confirmSignup, previewSignup } from "../api/client";

/**
 * A tela de quem clicou no link de confirmação do cadastro.
 *
 * O token vem do FRAGMENTO (`/confirmar#<token>`), que não é enviado a servidor nenhum:
 * fica fora do log do host estático, do `Referer` e de qualquer preview de link. É por
 * isso que o e-mail monta o link com `#`, e não com `?`.
 *
 * É aqui — e só aqui — que o nome da organização aparece. No e-mail ele seria texto
 * escolhido por quem digitou o formulário entregue na caixa de outra pessoa; aqui já é
 * a nossa página falando, e a pessoa sabe onde está.
 *
 * A prévia roda antes do formulário para a tela não mentir por omissão: sem ela, a
 * pessoa digitaria a senha e só então descobriria que o link venceu.
 *
 * Gêmea da `InviteScreen`, e escrita como tal em vez de generalizada com ela: os
 * vereditos e os textos são outros, e é o mesmo critério das `verdict_for` do servidor.
 */
export function ConfirmScreen({ onConfirmado }: { onConfirmado: () => void }) {
  // Lido uma vez, na montagem: o token não muda enquanto a tela está aberta.
  const [token] = useState(() => window.location.hash.replace(/^#/, ""));
  const [pendencia, setPendencia] = useState<{
    organizationName: string;
    email: string;
  } | null>(null);
  const [recusa, setRecusa] = useState<string | null>(null);
  const [senha, setSenha] = useState("");
  const [erro, setErro] = useState<string | null>(null);
  const [enviando, setEnviando] = useState(false);

  useEffect(() => {
    // Sem token não há o que perguntar: o servidor responderia 422, e a mensagem dele
    // seria sobre validação de campo, não sobre o link que a pessoa recebeu. A frase
    // desse caso é derivada no render, abaixo.
    if (!token) return;
    void previewSignup(token).then((resultado) => {
      if (resultado.ok) {
        setPendencia({
          organizationName: resultado.organizationName,
          email: resultado.email,
        });
      } else {
        setRecusa(resultado.message);
      }
    });
  }, [token]);

  async function enviar(evento: React.FormEvent) {
    evento.preventDefault();
    setEnviando(true);
    setErro(null);
    const falha = await confirmSignup(token, senha);
    setEnviando(false);
    if (falha) {
      setErro(falha);
      return;
    }
    onConfirmado();
  }

  const mensagem = token ? recusa : "Link incompleto: falta o código no endereço.";

  if (mensagem) {
    return (
      <div className="auth-screen">
        <h1>Confirmar cadastro</h1>
        <p className="error" role="alert">
          {mensagem}
        </p>
      </div>
    );
  }

  // A prévia ainda não voltou. Nada na tela é melhor que um formulário que pode sumir.
  if (pendencia === null) return null;

  return (
    <div className="auth-screen">
      <h1>Confirmar cadastro</h1>
      <p>
        Criando a organização <strong>{pendencia.organizationName}</strong> para{" "}
        <strong>{pendencia.email}</strong>.
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
          Criar organização
        </button>
      </form>
    </div>
  );
}
