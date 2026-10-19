import { useEffect, useRef, useState } from "react";
import { fetchMe, logout, type Session } from "./api/client";
import { AuthScreen } from "./components/AuthScreen";
import { Composer } from "./components/Composer";
import { EmptyState } from "./components/EmptyState";
import { AiConfigPanel } from "./components/AiConfigPanel";
import { InvitePanel } from "./components/InvitePanel";
import { ConfirmScreen } from "./components/ConfirmScreen";
import { ForgotScreen } from "./components/ForgotScreen";
import { InviteScreen } from "./components/InviteScreen";
import { ResetScreen } from "./components/ResetScreen";
import { Thread } from "./components/Thread";
import { Waiting } from "./components/Waiting";
import { useChat } from "./state/useChat";

export default function App() {
  // `undefined` é "ainda perguntando ao servidor". Distinguir de `null` evita piscar a
  // tela de entrada para quem já tem sessão.
  const [session, setSession] = useState<Session | null | undefined>(undefined);
  // Desvio de rota antes da lógica de sessão. Sem biblioteca de rotas: `App` já decide
  // a tela pelo estado da sessão, e duas telas não justificam trazer uma. Lido uma vez
  // na montagem; o aceite o desliga.
  const [convite, setConvite] = useState(() => window.location.pathname === "/convite");
  // Segunda rota de SPA do produto. Continua sem biblioteca de rotas: são desvios lidos
  // uma vez na montagem, e três telas não pagam a dependência.
  const [redefinir, setRedefinir] = useState(
    () => window.location.pathname === "/redefinir",
  );
  // Terceira rota, e o mesmo critério: quatro telas ainda não pagam uma biblioteca de
  // rotas. Desde a fatia 3f é por aqui que uma organização nasce — o cadastro em si só
  // pede o link.
  const [confirmar, setConfirmar] = useState(
    () => window.location.pathname === "/confirmar",
  );
  // Esta não é rota: é um estado da tela de entrada. Quem pede o link não chega por
  // URL, chega clicando — e uma rota daria um endereço para alguém marcar.
  const [esqueci, setEsqueci] = useState(false);

  useEffect(() => {
    void fetchMe().then(setSession);
  }, []);

  if (convite) {
    return (
      <InviteScreen
        onAccepted={() => {
          setConvite(false);
          void fetchMe().then(setSession);
        }}
      />
    );
  }
  if (confirmar) {
    return (
      <ConfirmScreen
        onConfirmado={() => {
          setConfirmar(false);
          void fetchMe().then(setSession);
        }}
      />
    );
  }
  if (redefinir) {
    return (
      <ResetScreen
        onRedefinida={() => {
          setRedefinir(false);
          void fetchMe().then(setSession);
        }}
      />
    );
  }
  if (session === undefined) return null;
  if (session === null) {
    return esqueci ? (
      <ForgotScreen onVoltar={() => setEsqueci(false)} />
    ) : (
      <AuthScreen
        onAuthenticated={() => void fetchMe().then(setSession)}
        onEsqueciSenha={() => setEsqueci(true)}
      />
    );
  }
  // `key` remonta o chat quando a pessoa troca: histórico de sessão anterior não pode
  // sobreviver a um login diferente.
  return (
    <Chat
      key={session.email}
      session={session}
      onLogout={() => setSession(null)}
      // A config de IA carrega as sugestões da tela vazia; quem as guarda é a
      // sessão, então salvar precisa relê-la. `null` aqui é falha de rede, e NÃO
      // "não há sessão": o cookie continua valendo, e `setSession(null)` mandaria
      // de volta ao login quem já estava dentro — diferente do `onAuthenticated`,
      // onde `null` significa mesmo que não autenticou.
      onConfigSalva={() =>
        void fetchMe().then((relida) => {
          if (relida !== null) setSession(relida);
        })
      }
    />
  );
}

function Chat({
  session,
  onLogout,
  onConfigSalva,
}: {
  session: Session;
  onLogout: () => void;
  onConfigSalva: () => void;
}) {
  const chat = useChat();
  const startedAtRef = useRef(Date.now());
  const [convites, setConvites] = useState(false);
  const [configIa, setConfigIa] = useState(false);

  function send(text: string) {
    startedAtRef.current = Date.now();
    void chat.send(text);
  }

  return (
    <div className="app">
      <header className="app-header">
        <h1>Sensus IA</h1>
        <div className="app-identity">
          {/* Primeira vez que o papel muda a tela. A checagem que VALE continua na
              borda da API (403 forbidden_role); isto esconde o que o member não pode
              fazer. */}
          {session.role === "admin" && (
            <>
              <button type="button" onClick={() => setConvites(true)}>
                Convidar
              </button>
              <button type="button" onClick={() => setConfigIa(true)}>
                Configurar IA
              </button>
            </>
          )}
          <span>{session.email}</span>
          <button
            type="button"
            onClick={() => {
              void logout().then(onLogout);
            }}
          >
            Sair
          </button>
        </div>
      </header>

      <main className="app-main">
        {convites && <InvitePanel onClose={() => setConvites(false)} />}
        {configIa && (
          <AiConfigPanel
            onClose={() => setConfigIa(false)}
            // As sugestões da tela vazia saem da config: sem reler a sessão, elas
            // ficariam as antigas até a próxima carga da página.
            onSalvo={onConfigSalva}
          />
        )}

        {chat.messages.length === 0 && !chat.isStreaming ? (
          <EmptyState suggestions={session.suggestions} onPick={send} />
        ) : (
          <Thread
            messages={chat.messages}
            streamingText={chat.streamingText}
            onRate={(id, rating) => void chat.rate(id, rating)}
          />
        )}

        {chat.isStreaming && !chat.streamingText && (
          <Waiting stage={chat.stage} startedAt={startedAtRef.current} onStop={chat.stop} />
        )}

        {chat.error && (
          <div className="error" role="alert">
            <p>{chat.error.message}</p>
            <button type="button" onClick={() => void chat.retry()}>
              tentar de novo
            </button>
          </div>
        )}
      </main>

      <footer className="app-footer">
        <Composer disabled={chat.isStreaming} onSend={send} />
      </footer>
    </div>
  );
}
