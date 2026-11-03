/* AI features for this app, through its own /api/ai endpoint (flynn-ai.ts: the Claude Agent SDK on this computer's
   Claude sign-in). Text or JSON answers only; each request has a cost cap.

     const summary = await ask("Summarise this note in one line: " + note);
     const tags = await askJSON<{ tags: string[] }>("Suggest tags for: " + note,
       { type: "object", properties: { tags: { type: "array", items: { type: "string" } } }, required: ["tags"] });
     for await (const chunk of stream("Write a short reply to: " + text)) setReply((r) => r + chunk);
*/
export type AIOptions = { system?: string; model?: "haiku" | "sonnet" | "opus"; signal?: AbortSignal };

async function call(body: Record<string, unknown>, signal?: AbortSignal): Promise<Response> {
  const r = await fetch("/api/ai", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body), signal });
  if (!r.ok) {
    let msg = `AI request failed (${r.status})`;
    try { msg = (await r.json()).error || msg; } catch { /* keep the status message */ }
    throw new Error(msg);
  }
  return r;
}

export async function ask(prompt: string, opts: AIOptions = {}): Promise<string> {
  const r = await call({ prompt, system: opts.system, model: opts.model }, opts.signal);
  return (await r.json()).text as string;
}

export async function askJSON<T>(prompt: string, schema: Record<string, unknown>, opts: AIOptions = {}): Promise<T> {
  const r = await call({ prompt, schema, system: opts.system, model: opts.model }, opts.signal);
  const j = await r.json();
  return (j.json ?? JSON.parse(j.text)) as T;
}

export async function* stream(prompt: string, opts: AIOptions = {}): AsyncGenerator<string> {
  const r = await call({ prompt, stream: true, system: opts.system, model: opts.model }, opts.signal);
  const reader = r.body!.getReader();
  const dec = new TextDecoder();
  let buf = "";
  for (;;) {
    const { done, value } = await reader.read();
    if (done) return;
    buf += dec.decode(value, { stream: true });
    let i: number;
    while ((i = buf.indexOf("\n\n")) >= 0) {
      const line = buf.slice(0, i).replace(/^data: /, "");
      buf = buf.slice(i + 2);
      if (!line) continue;
      const ev = JSON.parse(line);
      if (ev.t === "delta") yield ev.text as string;
      if (ev.t === "error") throw new Error(ev.error);
    }
  }
}
