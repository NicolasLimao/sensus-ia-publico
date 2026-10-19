import { useEffect, useState } from "react";
import { createInvite, listInvites, revokeInvite, type PendingInvite } from "../api/client";

/**
 * O painel de convites do admin.
 *
 * O link não é montado aqui, e não aparece nesta tela: desde a 3e quem o monta é o
 * servidor, de `public_base_url`, e ele sai por e-mail para o endereço convidado. O
 * token não chega a este código — é isso, e não a coluna `email` no banco, que faz o
 * convite provar posse do endereço.
 *
 * A lista vem sempre do servidor depois de cada ação: ele é a autoridade sobre o que
 * ainda está pendente, e um estado local otimista discordaria dele na primeira falha.
 */
export function InvitePanel({ onClose }: { onClose: () => void }) {
  const [pendentes, setPendentes] = useState<PendingInvite[]>([]);
  const [email, setEmail] = useState("");
  const [enviadoPara, setEnviadoPara] = useState<string | null>(null);
  const [erro, setErro] = useState<string | null>(null);
  const [enviando, setEnviando] = useState(false);

  useEffect(() => {
    void listInvites().then(setPendentes);
  }, []);

  async function convidar(evento: React.FormEvent) {
    evento.preventDefault();
    setEnviando(true);
    setErro(null);
    setEnviadoPara(null);
    const resultado = await createInvite(email);
    setEnviando(false);
    if (!resultado.ok) {
      // A frase vem do servidor: só ele sabe distinguir "já faz parte da organização"
      // de "não foi possível enviar".
      setErro(resultado.message);
      return;
    }
    setEnviadoPara(resultado.invite.email);
    setEmail("");
    setPendentes(await listInvites());
  }

  async function revogar(id: string) {
    await revokeInvite(id);
    setPendentes(await listInvites());
  }

  return (
    <section className="invite-panel" aria-label="Convites">
      <header>
        <h2>Convites</h2>
        <button type="button" onClick={onClose}>
          Fechar
        </button>
      </header>

      <form onSubmit={(evento) => void convidar(evento)}>
        <label>
          E-mail
          <input
            type="email"
            value={email}
            required
            autoComplete="off"
            onChange={(e) => setEmail(e.target.value)}
          />
        </label>
        <button type="submit" disabled={enviando}>
          Enviar convite
        </button>
      </form>

      {erro && (
        <p className="error" role="alert">
          {erro}
        </p>
      )}

      {enviadoPara && (
        <p className="invite-sent">
          <strong>Convite enviado para {enviadoPara}.</strong> O link vai só para essa
          caixa de entrada.
        </p>
      )}

      {pendentes.length === 0 ? (
        <p>Nenhum convite pendente.</p>
      ) : (
        <ul>
          {pendentes.map((pendente) => (
            <li key={pendente.id}>
              {/* O endereço primeiro: a pergunta do admin é "já chamei essa pessoa?". */}
              <span>{pendente.email}</span>
              <span>
                convidado em {new Date(pendente.createdAt).toLocaleDateString("pt-BR")}
              </span>
              <button type="button" onClick={() => void revogar(pendente.id)}>
                Revogar
              </button>
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}
