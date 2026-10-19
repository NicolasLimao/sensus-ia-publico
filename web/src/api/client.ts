import type { ChatEvent, Message, Rating, Source, Stage } from "../types";
import { parseSseStream } from "./sse";

const BASE = "/api/v1";

// Acima do pior caso medido (71 s). Timeout curto não protege ninguém: só descarta
// resposta boa que ia chegar. Quem arma o relógio é o useChat, que tem o controller.
export const CLIENT_TIMEOUT_MS = 90_000;

const NETWORK_MESSAGE = "Não foi possível falar com o serviço. Tente de novo.";

// O 422 de validação do pydantic traz `detail` como LISTA de erros de campo, e não
// como o `{code, message}` das recusas de regra de negócio. Sem distinguir os dois, um
// formulário recusado por tamanho virava "falha de rede": o admin ia procurar problema
// de conexão em vez de corrigir o campo. A frase não repete o texto do pydantic, que
// vem em inglês e fala de `loc`/`body`.
const VALIDATION_MESSAGE = "O serviço recusou os campos enviados. Confira e tente de novo.";

/** A sessão como o front a enxerga. Vem de `/auth/me`, nunca do cookie: o cookie é
 * httpOnly de propósito, e quem manda no papel é o servidor a cada requisição. */
export interface Session {
  email: string;
  role: string;
  suggestions: string[];
}

export async function fetchMe(): Promise<Session | null> {
  try {
    const response = await fetch(`${BASE}/auth/me`, { credentials: "same-origin" });
    if (!response.ok) return null;
    return (await response.json()) as Session;
  } catch {
    return null;
  }
}

/** A mensagem vem do servidor: é lá que mora a decisão de não revelar se o e-mail
 * existe, e de distinguir os motivos de um convite não valer. Reescrevê-la aqui abriria
 * a chance de o front contar o que o back cala — ou calar o que ele diz. */
async function errorMessage(response: Response): Promise<string> {
  try {
    const payload = (await response.json()) as { detail?: { message?: string } | unknown[] };
    if (Array.isArray(payload.detail)) return VALIDATION_MESSAGE;
    return payload.detail?.message ?? NETWORK_MESSAGE;
  } catch {
    return NETWORK_MESSAGE;
  }
}

/** `null` é sucesso; string é a mensagem a mostrar. */
async function postAuth(path: string, body: unknown): Promise<string | null> {
  let response: Response;
  try {
    response = await fetch(`${BASE}${path}`, {
      method: "POST",
      credentials: "same-origin",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
  } catch {
    return NETWORK_MESSAGE;
  }
  if (response.ok) return null;
  return errorMessage(response);
}

export function login(email: string, password: string): Promise<string | null> {
  return postAuth("/auth/login", { email, password });
}

/** `null` é sucesso. Desde a fatia 3f o cadastro NÃO cria conta e NÃO devolve sessão:
 * responde 202 igual para endereço livre e endereço que já tem conta, e o link vai por
 * e-mail. A senha não passa por aqui — ela nasce na tela de confirmação. */
export function signup(
  organizationName: string,
  email: string,
): Promise<string | null> {
  return postAuth("/auth/signup", { organization_name: organizationName, email });
}

export async function logout(): Promise<void> {
  try {
    await fetch(`${BASE}/auth/logout`, { method: "POST", credentials: "same-origin" });
  } catch {
    // Sair é melhor-esforço: se a rede caiu, o front esquece a sessão de qualquer jeito.
  }
}

export async function* streamChat(
  message: string,
  signal: AbortSignal,
): AsyncGenerator<ChatEvent> {
  let response: Response;
  try {
    response = await fetch(`${BASE}/chat`, {
      method: "POST",
      credentials: "same-origin",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ message }),
      signal,
    });
  } catch {
    yield { type: "error", code: "service_unavailable", message: NETWORK_MESSAGE };
    return;
  }

  if (!response.ok || !response.body) {
    yield { type: "error", code: "service_unavailable", message: NETWORK_MESSAGE };
    return;
  }

  for await (const raw of parseSseStream(response.body)) {
    const event = toChatEvent(raw.event, raw.data);
    if (event) yield event;
  }
}

function toChatEvent(name: string, data: string): ChatEvent | null {
  const payload = JSON.parse(data) as Record<string, unknown>;
  switch (name) {
    case "stage":
      return { type: "stage", stage: payload.stage as Stage };
    case "token":
      return { type: "token", text: String(payload.text) };
    case "sources":
      return { type: "sources", sources: payload.sources as Source[] };
    case "done":
      return {
        type: "done",
        messageId: String(payload.message_id),
        chunksUsed: Number(payload.chunks_used),
        score: payload.score === null ? null : Number(payload.score),
        elapsedMs: Number(payload.elapsed_ms),
      };
    case "error":
      return { type: "error", code: String(payload.code), message: String(payload.message) };
    default:
      return null;
  }
}

export async function fetchConversation(): Promise<Message[]> {
  const response = await fetch(`${BASE}/conversation`, { credentials: "same-origin" });
  if (!response.ok) return [];
  const body = (await response.json()) as {
    messages: Array<{
      id: string;
      role: "user" | "assistant";
      content: string;
      sources: Source[];
      chunks_used: number;
      feedback: Rating | null;
    }>;
  };
  return body.messages.map((m) => ({
    id: m.id,
    role: m.role,
    content: m.content,
    sources: m.sources,
    chunksUsed: m.chunks_used,
    feedback: m.feedback,
  }));
}

export async function sendFeedback(messageId: string, rating: Rating): Promise<void> {
  const response = await fetch(`${BASE}/messages/${encodeURIComponent(messageId)}/feedback`, {
    method: "POST",
    credentials: "same-origin",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ rating }),
  });
  if (!response.ok) throw new Error(`feedback recusado: ${response.status}`);
}

export interface InvitePreview {
  organizationName: string;
  /** Para quem o convite foi feito. Não revela nada novo: quem apresenta o token o
   * recebeu naquela caixa. */
  email: string;
  expiresAt: string;
}

export interface PendingInvite {
  id: string;
  email: string;
  createdAt: string;
  expiresAt: string;
}

/** Sem `token`: desde a 3e ele existe só dentro do e-mail. */
export interface CreatedInvite {
  id: string;
  email: string;
  expiresAt: string;
}

export type PreviewResult =
  | { ok: true; preview: InvitePreview }
  | { ok: false; message: string };

/** A prévia é POST, e o token vai no CORPO: num path ou query ele entraria em todo log
 * de acesso e proxy do caminho, e no `Referer`. */
export async function previewInvite(token: string): Promise<PreviewResult> {
  let response: Response;
  try {
    response = await fetch(`${BASE}/invites/preview`, {
      method: "POST",
      credentials: "same-origin",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ token }),
    });
  } catch {
    return { ok: false, message: NETWORK_MESSAGE };
  }
  if (!response.ok) return { ok: false, message: await errorMessage(response) };
  const body = (await response.json()) as {
    organization_name: string;
    email: string;
    expires_at: string;
  };
  return {
    ok: true,
    preview: {
      organizationName: body.organization_name,
      email: body.email,
      expiresAt: body.expires_at,
    },
  };
}

/** `null` é sucesso, como em `login` e `signup` — e, como eles, esta rota emite a
 * sessão. Sem e-mail: o endereço vem do convite, não de quem aceita. */
export function acceptInvite(token: string, password: string): Promise<string | null> {
  return postAuth("/invites/accept", { token, password });
}

export type CreateInviteResult =
  | { ok: true; invite: CreatedInvite }
  | { ok: false; message: string };

/** O token NÃO volta mais: desde a 3e ele existe só dentro do e-mail.
 *
 * Resultado discriminado, como `previewInvite`, e não `null` em toda falha: a frase do
 * `already_member` é a única coisa útil que o admin recebe nesse caso, e um `null` a
 * jogaria fora. */
export async function createInvite(email: string): Promise<CreateInviteResult> {
  let response: Response;
  try {
    response = await fetch(`${BASE}/invites`, {
      method: "POST",
      credentials: "same-origin",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ email }),
    });
  } catch {
    return { ok: false, message: NETWORK_MESSAGE };
  }
  if (!response.ok) return { ok: false, message: await errorMessage(response) };
  const body = (await response.json()) as {
    id: string;
    email: string;
    expires_at: string;
  };
  return {
    ok: true,
    invite: { id: body.id, email: body.email, expiresAt: body.expires_at },
  };
}

export async function listInvites(): Promise<PendingInvite[]> {
  try {
    const response = await fetch(`${BASE}/invites`, { credentials: "same-origin" });
    if (!response.ok) return [];
    const body = (await response.json()) as Array<{
      id: string;
      email: string;
      created_at: string;
      expires_at: string;
    }>;
    return body.map((invite) => ({
      id: invite.id,
      email: invite.email,
      createdAt: invite.created_at,
      expiresAt: invite.expires_at,
    }));
  } catch {
    return [];
  }
}

export async function revokeInvite(id: string): Promise<void> {
  try {
    await fetch(`${BASE}/invites/${encodeURIComponent(id)}`, {
      method: "DELETE",
      credentials: "same-origin",
    });
  } catch {
    // Revogar é idempotente no servidor; se a rede caiu, a próxima listagem mostra a
    // verdade — e ela é a autoridade, não este estado local.
  }
}

export type ResetPreviewResult =
  | { ok: true; email: string }
  | { ok: false; message: string };

/** `null` é sucesso, como em `login` e `signup`. O servidor responde 202 tanto para
 * e-mail com conta quanto sem — a tela não tem como distinguir, e é esse o desenho. */
export function requestPasswordReset(email: string): Promise<string | null> {
  return postAuth("/auth/forgot-password", { email });
}

/** A prévia é POST, e o token vai no CORPO — como a do convite: num path ou query ele
 * entraria em todo log de acesso e proxy do caminho, e no `Referer`. */
export async function previewReset(token: string): Promise<ResetPreviewResult> {
  let response: Response;
  try {
    response = await fetch(`${BASE}/auth/reset-password/preview`, {
      method: "POST",
      credentials: "same-origin",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ token }),
    });
  } catch {
    return { ok: false, message: NETWORK_MESSAGE };
  }
  if (!response.ok) return { ok: false, message: await errorMessage(response) };
  const body = (await response.json()) as { email: string };
  return { ok: true, email: body.email };
}

/** `null` é sucesso — e, como o aceite de convite, esta rota emite a sessão. */
export function resetPassword(token: string, password: string): Promise<string | null> {
  return postAuth("/auth/reset-password", { token, password });
}

export type SignupPreviewResult =
  | { ok: true; organizationName: string; email: string }
  | { ok: false; message: string };

/** A prévia é POST, e o token vai no CORPO — como as do convite e do reset: num path ou
 * query ele entraria em todo log de acesso e proxy do caminho, e no `Referer`. */
export async function previewSignup(token: string): Promise<SignupPreviewResult> {
  let response: Response;
  try {
    response = await fetch(`${BASE}/auth/confirm-signup/preview`, {
      method: "POST",
      credentials: "same-origin",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ token }),
    });
  } catch {
    return { ok: false, message: NETWORK_MESSAGE };
  }
  if (!response.ok) return { ok: false, message: await errorMessage(response) };
  const body = (await response.json()) as { organization_name: string; email: string };
  return { ok: true, organizationName: body.organization_name, email: body.email };
}

/** `null` é sucesso — e, como o aceite de convite, esta rota emite a sessão. */
export function confirmSignup(token: string, password: string): Promise<string | null> {
  return postAuth("/auth/confirm-signup", { token, password });
}

/** Uma versão da config de IA, como o servidor a devolve. */
export interface AiConfigValues {
  briefing: string;
  generationModel: string;
  generationTopK: number;
  maxContextChars: number;
  suggestions: string[];
}

export interface AiConfigVersion {
  id: string;
  version: number;
  isActive: boolean;
  createdAt: string;
  createdByEmail: string | null;
  config: AiConfigValues;
}

export interface AiConfigView {
  active: AiConfigValues;
  versions: AiConfigVersion[];
  /** Existe versão mais antiga do que a última desta página. */
  hasMoreVersions: boolean;
  allowedModels: string[];
}

interface AiConfigPayload {
  briefing: string;
  generation_model: string;
  generation_top_k: number;
  max_context_chars: number;
  suggestions: string[];
}

function toValues(payload: AiConfigPayload): AiConfigValues {
  return {
    briefing: payload.briefing,
    generationModel: payload.generation_model,
    generationTopK: payload.generation_top_k,
    maxContextChars: payload.max_context_chars,
    suggestions: payload.suggestions,
  };
}

function toPayload(values: AiConfigValues): AiConfigPayload {
  return {
    briefing: values.briefing,
    generation_model: values.generationModel,
    generation_top_k: values.generationTopK,
    max_context_chars: values.maxContextChars,
    // O campo é uma por linha, e terminar a lista com Enter é o gesto natural: a linha
    // em branco chegava como item e estourava o teto de dez. Sai daqui, e não do
    // `onChange`, porque enquanto se digita a linha vazia precisa existir.
    suggestions: values.suggestions.map((s) => s.trim()).filter((s) => s !== ""),
  };
}

/** `null` quando não foi possível ler — a tela mostra o erro e não inventa defaults:
 * um formulário preenchido com palpite faria o admin salvar o palpite. */
export async function fetchAiConfig(offset = 0): Promise<AiConfigView | null> {
  try {
    // Só o `offset` vai na query: o tamanho da página é decisão do servidor, e repeti-lo
    // aqui criaria dois números para manter iguais. A tela pede "o que vem depois do que
    // já tenho", que continua certo qualquer que seja o tamanho lá.
    const response = await fetch(`${BASE}/ai-config?offset=${offset}`, {
      credentials: "same-origin",
    });
    if (!response.ok) return null;
    const body = (await response.json()) as {
      active: AiConfigPayload;
      versions: Array<{
        id: string;
        version: number;
        is_active: boolean;
        created_at: string;
        created_by_email: string | null;
        config: AiConfigPayload;
      }>;
      has_more_versions: boolean;
      allowed_models: string[];
    };
    return {
      active: toValues(body.active),
      versions: body.versions.map((v) => ({
        id: v.id,
        version: v.version,
        isActive: v.is_active,
        createdAt: v.created_at,
        createdByEmail: v.created_by_email,
        config: toValues(v.config),
      })),
      hasMoreVersions: body.has_more_versions,
      allowedModels: body.allowed_models,
    };
  } catch {
    return null;
  }
}

/** `null` é sucesso, como nas outras rotas de escrita. Salvar PUBLICA: cria a versão
 * seguinte e a ativa. */
export function saveAiConfig(values: AiConfigValues): Promise<string | null> {
  return postAuth("/ai-config", toPayload(values));
}

/** Rollback: volta o comportamento para uma versão anterior, sem apagar nenhuma. */
export async function activateAiConfig(id: string): Promise<string | null> {
  let response: Response;
  try {
    response = await fetch(`${BASE}/ai-config/${encodeURIComponent(id)}/activate`, {
      method: "POST",
      credentials: "same-origin",
    });
  } catch {
    return NETWORK_MESSAGE;
  }
  return response.ok ? null : errorMessage(response);
}
