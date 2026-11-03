/* flynn app AI: an /api/ai endpoint inside this app's own dev server, running the Claude Agent SDK on the
   signed-in Claude account of this computer. Text and JSON answers only: no tools, no file access, no user settings.
   Each request has a cost cap (FLYNN_AI_CAP_USD, default $0.50); every call is logged to .flynn/ai-usage.jsonl,
   which flynn Studio reads to show the app's AI spend. Call it from the app through src/lib/ai.ts. */
import type { Plugin } from "vite";
import type { IncomingMessage, ServerResponse } from "node:http";
import { appendFileSync, mkdirSync } from "node:fs";
import { query } from "@anthropic-ai/claude-agent-sdk";

const CAP = Number(process.env.FLYNN_AI_CAP_USD || "0.5");
const MODELS = new Set(["haiku", "sonnet", "opus"]);
const DEFAULT_SYSTEM =
  "You are the AI feature inside a small web app. Do exactly what the request asks, briefly. " +
  "Answer in the language of the request.";

type Body = { prompt?: string; system?: string; schema?: Record<string, unknown>; stream?: boolean; model?: string };

function readBody(req: IncomingMessage): Promise<Body> {
  return new Promise((resolve, reject) => {
    let raw = "";
    req.on("data", (c) => { raw += c; if (raw.length > 200_000) reject(new Error("request too large")); });
    req.on("end", () => { try { resolve(raw ? JSON.parse(raw) : {}); } catch { reject(new Error("bad JSON")); } });
  });
}

function sameOrigin(req: IncomingMessage): boolean {
  const o = req.headers.origin;
  if (!o) return true;
  try { const u = new URL(o); return u.hostname === "127.0.0.1" || u.hostname === "localhost"; } catch { return false; }
}

function logUsage(entry: Record<string, unknown>): void {
  try {
    mkdirSync(".flynn", { recursive: true });
    appendFileSync(".flynn/ai-usage.jsonl", JSON.stringify({ at: new Date().toISOString(), ...entry }) + "\n");
  } catch { /* usage logging never breaks a request */ }
}

async function handle(req: IncomingMessage, res: ServerResponse): Promise<void> {
  if (req.method !== "POST") { res.statusCode = 405; res.end(); return; }
  if (!sameOrigin(req)) { res.statusCode = 403; res.end(JSON.stringify({ error: "origin not allowed" })); return; }
  let body: Body;
  try { body = await readBody(req); } catch (e) { res.statusCode = 400; res.end(JSON.stringify({ error: String(e) })); return; }
  const prompt = String(body.prompt || "").trim();
  if (!prompt) { res.statusCode = 400; res.end(JSON.stringify({ error: "prompt is required" })); return; }
  const model = MODELS.has(String(body.model)) ? String(body.model) : "sonnet";
  const stream = !!body.stream && !body.schema;
  const started = Date.now();
  if (stream) {
    res.setHeader("Content-Type", "text/event-stream");
    res.setHeader("Cache-Control", "no-store");
  }
  const send = (o: unknown) => res.write(`data: ${JSON.stringify(o)}\n\n`);
  let text = "", json: unknown = undefined, cost = 0, ok = false, error = "";
  try {
    for await (const m of query({
      prompt,
      options: {
        model, tools: [], maxTurns: body.schema ? 3 : 1, maxBudgetUsd: CAP, settingSources: [], persistSession: false,
        systemPrompt: String(body.system || DEFAULT_SYSTEM).slice(0, 8000), includePartialMessages: stream,
        ...(body.schema ? { outputFormat: { type: "json_schema" as const, schema: body.schema } } : {}),
      },
    })) {
      const msg = m as any;
      if (stream && msg.type === "stream_event" && msg.event?.type === "content_block_delta" && msg.event.delta?.type === "text_delta") {
        send({ t: "delta", text: msg.event.delta.text });
      }
      if (msg.type === "result") {
        cost = Number(msg.total_cost_usd || 0);
        ok = msg.subtype === "success";
        text = typeof msg.result === "string" ? msg.result : "";
        json = msg.structured_output;
        if (!ok) error = msg.subtype === "error_max_budget_usd" ? `stopped at the $${CAP} cap for one request` : String(msg.subtype);
      }
    }
  } catch (e) {
    error = e instanceof Error ? e.message : String(e);
  }
  logUsage({ model, cost, ok, ms: Date.now() - started, kind: body.schema ? "json" : stream ? "stream" : "text" });
  if (stream) { send(ok ? { t: "done", text, cost } : { t: "error", error: error || "no answer" }); res.end(); return; }
  res.setHeader("Content-Type", "application/json");
  if (!ok) { res.statusCode = 502; res.end(JSON.stringify({ error: error || "no answer" })); return; }
  res.end(JSON.stringify({ text, json, cost }));
}

export function flynnAi(): Plugin {
  return {
    name: "flynn-ai",
    configureServer(server) { server.middlewares.use("/api/ai", (req, res) => { void handle(req, res); }); },
    configurePreviewServer(server) { server.middlewares.use("/api/ai", (req, res) => { void handle(req, res); }); },
  };
}
