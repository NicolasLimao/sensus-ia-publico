// Espelha o contrato do adaptador (api/). O snake_case do JSON vira camelCase na
// fronteira — daí para dentro o front só conhece camelCase.
export type Stage = "retrieving" | "reranking" | "generating";
export type Rating = "up" | "down";

export interface Source {
  id: string;
  excerpt: string;
  document: string | null;
  score: number;
}

export type ChatEvent =
  | { type: "stage"; stage: Stage }
  | { type: "token"; text: string }
  | { type: "sources"; sources: Source[] }
  | {
      type: "done";
      messageId: string;
      chunksUsed: number;
      score: number | null;
      elapsedMs: number;
    }
  | { type: "error"; code: string; message: string };

export interface Message {
  id: string;
  role: "user" | "assistant";
  content: string;
  sources: Source[];
  chunksUsed: number;
  feedback: Rating | null;
  interrupted?: boolean;
}
