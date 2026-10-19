import { useState } from "react";
import { login, signup } from "../api/client";

/**
 * Entrar e pedir a criação de uma organização na mesma tela.
 *
 * Desde a fatia 3f os dois ramos são diferentes em natureza: entrar abre sessão na
 * hora; criar organização NÃO cria nada — manda um pedido, e a conta só nasce quando
 * a pessoa abre o link que chegou no e-mail dela. Por isso o ramo de criação não tem
 * campo de senha (ela nasce na `ConfirmScreen`) e não chama `onAuthenticated`.
 *
 * O erro exibido é sempre o que o servidor mandou. É lá que mora a decisão de não
 * revelar se um e-mail tem conta — reescrever a mensagem aqui abriria a chance de o
 * front contar o que o back cala de propósito.
 */
export function AuthScreen({
  onAuthenticated,
  onEsqueciSenha,
}: {
  onAuthenticated: () => void;
  onEsqueciSenha: () => void;
}) {
  const [criando, setCriando] = useState(false);
  // O pedido de cadastro foi aceito: a tela vira confirmação, não sessão.
  const [pedido, setPedido] = useState(false);
  const [organizacao, setOrganizacao] = useState("");
  const [email, setEmail] = useState("");
  const [senha, setSenha] = useState("");
  const [erro, setErro] = useState<string | null>(null);
  const [enviando, setEnviando] = useState(false);

  async function enviar(evento: React.FormEvent) {
    evento.preventDefault();
    setEnviando(true);
    setErro(null);
    const falha = criando
      ? await signup(organizacao, email)
      : await login(email, senha);
    setEnviando(false);
    if (falha) {
      setErro(falha);
      return;
    }
    if (criando) {
      // Não há sessão a abrir: nenhuma conta foi criada. A tela passa a esperar o
      // e-mail, como a `ForgotScreen` faz depois do 202 dela.
      setPedido(true);
      return;
    }
    onAuthenticated();
  }

  if (pedido) {
    return (
      <div className="auth-screen">
        <h1>Verifique seu e-mail</h1>
        {/* A frase é a MESMA do servidor, e a tela não repete o endereço digitado:
            ecoá-lo não revelaria nada ao servidor, mas transformaria a tela num oráculo
            de conveniência para quem testa endereços alheios num computador emprestado.
            Mesma decisão da `ForgotScreen`. */}
        <p>Se o endereço estiver livre, o link de confirmação está a caminho.</p>
        <p>O link vale por 24 horas e só pode ser usado uma vez.</p>
        <button
          type="button"
          className="auth-toggle"
          onClick={() => {
            setPedido(false);
            setCriando(false);
          }}
        >
          Voltar para a entrada
        </button>
      </div>
    );
  }

  return (
    <div className="auth-screen">
      <h1>Sensus IA</h1>
      <form onSubmit={(evento) => void enviar(evento)}>
        {criando && (
          <label>
            Organização
            <input
              type="text"
              value={organizacao}
              required
              onChange={(e) => setOrganizacao(e.target.value)}
            />
          </label>
        )}

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

        {/* Só no ramo de entrar: quem cria organização escolhe a senha na tela de
            confirmação, depois de provar que a caixa de entrada é dele. */}
        {!criando && (
          <label>
            Senha
            <input
              type="password"
              value={senha}
              required
              autoComplete="current-password"
              onChange={(e) => setSenha(e.target.value)}
            />
          </label>
        )}

        {erro && (
          <p className="error" role="alert">
            {erro}
          </p>
        )}

        <button type="submit" disabled={enviando}>
          {criando ? "Criar organização" : "Entrar"}
        </button>
      </form>

      <button
        type="button"
        className="auth-toggle"
        onClick={() => {
          setCriando(!criando);
          setErro(null);
        }}
      >
        {criando ? "Já tenho conta" : "Criar uma organização"}
      </button>

      {/* Só no modo de entrar: quem está criando organização ainda não tem senha a
          esquecer. */}
      {!criando && (
        <button type="button" className="auth-toggle" onClick={onEsqueciSenha}>
          Esqueci minha senha
        </button>
      )}
    </div>
  );
}
