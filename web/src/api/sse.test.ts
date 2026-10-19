import { describe, expect, it } from "vitest";
import { parseSseStream } from "./sse";

function streamOf(...pieces: string[]): ReadableStream<Uint8Array> {
  const encoder = new TextEncoder();
  return new ReadableStream({
    start(controller) {
      for (const piece of pieces) controller.enqueue(encoder.encode(piece));
      controller.close();
    },
  });
}

async function collect(stream: ReadableStream<Uint8Array>) {
  const out = [];
  for await (const event of parseSseStream(stream)) out.push(event);
  return out;
}

describe("parseSseStream", () => {
  it("lê eventos completos", async () => {
    const events = await collect(
      streamOf('event: token\ndata: {"text":"oi"}\n\nevent: done\ndata: {}\n\n'),
    );

    expect(events).toEqual([
      { event: "token", data: '{"text":"oi"}' },
      { event: "done", data: "{}" },
    ]);
  });

  it("junta um evento partido no meio entre dois chunks de rede", async () => {
    const events = await collect(streamOf("event: tok", 'en\ndata: {"text":"', 'oi"}\n\n'));

    expect(events).toEqual([{ event: "token", data: '{"text":"oi"}' }]);
  });

  it("ignora bloco final incompleto", async () => {
    const events = await collect(streamOf('event: token\ndata: {"text":"a"}\n\nevent: tok'));

    expect(events).toHaveLength(1);
  });
});
