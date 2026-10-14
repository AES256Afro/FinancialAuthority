// The chat stream: one POST, server-sent events back, each handed to the page as it arrives.
import { DEMO, demoFetch } from "./demo/shim";

export type ChatEvent =
  | { type: "text"; text: string }
  | { type: "tool"; name: string; arguments: Record<string, unknown>; rows: number }
  | { type: "done"; unverified: string[]; used: { name: string; arguments: Record<string, unknown>; rows: number }[]; usage: { input: number; output: number }; model: string; provider: string }
  | { type: "error"; message: string };

export interface ChatTurn {
  role: "user" | "assistant";
  content: string;
  used?: { name: string; rows: number }[];
  unverified?: string[];
  model?: string;
  error?: string;
}

export async function streamChat(messages: { role: string; content: string }[], onEvent: (e: ChatEvent) => void, signal?: AbortSignal): Promise<void> {
  const init: RequestInit = {
    method: "POST",
    headers: { "Content-Type": "application/json", "X-Requested-With": "FinancialAuthority" },
    credentials: "same-origin",
    body: JSON.stringify({ messages }),
    signal,
  };
  const response = DEMO ? await demoFetch("/api/v1/chat", init) : await fetch("/api/v1/chat", init);
  if (!response.ok || !response.body) {
    let detail = response.statusText;
    try {
      detail = ((await response.json()) as { detail?: string }).detail ?? detail;
    } catch {
      // not JSON
    }
    onEvent({ type: "error", message: detail });
    return;
  }
  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  for (;;) {
    const { value, done } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });
    let cut = buffer.indexOf("\n\n");
    while (cut >= 0) {
      const chunk = buffer.slice(0, cut);
      buffer = buffer.slice(cut + 2);
      for (const line of chunk.split("\n")) {
        if (line.startsWith("data:")) {
          try {
            onEvent(JSON.parse(line.slice(5)) as ChatEvent);
          } catch {
            // a partial line; ignore
          }
        }
      }
      cut = buffer.indexOf("\n\n");
    }
  }
}
