import { useState } from "react";
import { requestPasswordReset } from "../api/client";

/**
 * Pedir o link de redefinição.
 *
 * A confirmação é a MESMA para e-mail com conta, sem conta e para pedido freado — o
 * servidor responde 202 nos três, e a tela não pode desfazer o que esse 202 protege.
 * Por isso não há ramo de "e-mail não encontrado" aqui: ele não existe no contrato.
 *
 * E a confirmação não repete o endereço digitado, de propósito: ecoá-lo não revelaria
 * nada ao servidor, mas transformaria a tela num oráculo de conveniência para quem
 * testa endereços alheios num computador emprestado.
 *
 * Reaproveita as classes de `.auth-screen`, como a `InviteScreen`: é o mesmo formato de
 * formulário, e uma folha paralela para telas gêmeas envelheceria em divergência.
 */
export function ForgotScreen({ onVoltar }: { onVoltar: () => void }) {
  const [email, setEmail] = useState("");
  const [confirmacao, setConfirmacao] = useState<string | null>(null);
  const [erro, setErro] = useState<string | null>(null);
  const [enviando, setEnviando] = useState(false);

  async function enviar(evento: React.FormEvent) {
    evento.preventDefault();
    setEnviando(true);
    setErro(null);
    const falha = await requestPasswordReset(email);
    setEnviando(false);
    if (falha) {
      setErro(falha);
      return;
    }
    // A frase vem do servidor, como as outras: é lá que mora a decisão de não revelar
    // se o e-mail tem conta.
    setConfirmacao("Se houver conta com esse e-mail, o link está a caminho.");
  }

  if (confirmacao !== null) {
    return (
      <div className="auth-screen">
        <h1>Verifique seu e-mail</h1>
        <p>{confirmacao}</p>
        <p>O link vale por uma hora e só pode ser usado uma vez.</p>
        <button type="button" className="auth-toggle" onClick={onVoltar}>
          Voltar para a entrada
        </button>
      </div>
    );
  }

  return (
    <div className="auth-screen">
      <h1>Esqueci minha senha</h1>
      <form onSubmit={(evento) => void enviar(evento)}>
        <label>
          E-mail
          <input
            type="email"
            value={email}
            required
            autoComplete="email"
            onChange={(e) => setEmail(e.target.value)}
          />
        </label>

        {erro && (
          <p className="error" role="alert">
            {erro}
          </p>
        )}

        <button type="submit" disabled={enviando}>
          Enviar link
        </button>
      </form>

      <button type="button" className="auth-toggle" onClick={onVoltar}>
        Voltar
      </button>
    </div>
  );
}
