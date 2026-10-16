// Settings › Models: the advisor and the reviewer, their providers, keys that are stored encrypted and never
// shown again, the owner's standing rules and goals.
import { AlertTriangle, Check, CheckCircle2 } from "lucide-react";
import { useEffect, useState } from "react";
import { api, useApi } from "../api";
import { Where } from "../components/Where";

interface RoleConf {
  provider: string;
  model: string;
  base_url: string;
  price_in: number | null;
  price_out: number | null;
}

interface ModelsOut {
  roles: Record<string, RoleConf>;
  secrets: Record<string, boolean>;
  providers: { id: string; title: string; url?: string }[];
  can_store_secrets: boolean;
  defaults: Record<string, Record<string, string>>;
}

const PROVIDER_WORD: Record<string, string> = { anthropic: "Anthropic", ollama: "Ollama", openai: "OpenAI-compatible" };

// Anthropic's models are all claude-…; a bare claude-… name belongs to no other server. The server applies the same rule.
function modelFits(provider: string, model: string): boolean {
  const m = model.trim().toLowerCase();
  if (!m) return false;
  return provider === "anthropic" ? m.startsWith("claude") : !m.startsWith("claude-");
}

const ROLE_TEXT: Record<string, { title: string; text: string }> = {
  advisor: { title: "The advisor", text: "Answers in the chat and explains the plan. Local and free by default, through Ollama on the host. The plan is computed before it speaks, so a small model is enough." },
  reviewer: { title: "The reviewer", text: "A second opinion on payday, after a confirmed change, and on request. It reads the plan, your rules and goals, never the mail. Claude by API is recommended; any OpenAI-compatible model works; off by default." },
};

function RoleCard({ role, conf, providers, defaults, onSaved }: { role: string; conf: RoleConf; providers: ModelsOut["providers"]; defaults: ModelsOut["defaults"]; onSaved: () => void }) {
  const [form, setForm] = useState<RoleConf>(conf);
  const [catalog, setCatalog] = useState<string[]>([]);
  const [catalogError, setCatalogError] = useState<string | null>(null);
  const [test, setTest] = useState<{ ok: boolean; reply?: string; ms?: number; error?: string; model?: string } | null>(null);
  const [busy, setBusy] = useState<"save" | "test" | "find" | null>(null);
  useEffect(() => {
    setForm(conf);
  }, [conf]);
  const dirty = JSON.stringify(form) !== JSON.stringify(conf);
  const wantsUrl = form.provider === "openai" || form.provider === "ollama";

  async function save() {
    setBusy("save");
    try {
      await api(`/api/v1/models/${role}`, { method: "PUT", body: JSON.stringify(form) });
      onSaved();
    } finally {
      setBusy(null);
    }
  }
  const suggested = defaults?.[form.provider]?.[role] ?? "";
  const misfit = form.provider !== "none" && form.provider !== "openai" && form.model !== "" && !modelFits(form.provider, form.model);

  // Changing the provider brings that provider's own model and its list, so a model from the old one is never sent to the new.
  function pickProvider(id: string) {
    const next = { ...form, provider: id, model: modelFits(id, form.model) || id === "openai" ? form.model : (defaults?.[id]?.[role] ?? "") };
    setForm(next);
    setCatalog([]);
    setCatalogError(null);
    setTest(null);
    if (id !== "none") void find(next);
  }
  async function find(using: RoleConf = form) {
    setBusy("find");
    setCatalogError(null);
    try {
      const got = await api<{ models: string[]; error?: string }>(`/api/v1/models/catalog?provider=${using.provider}&base_url=${encodeURIComponent(using.base_url)}`);
      setCatalog(got.models);
      if (got.error) setCatalogError(got.error);
      if (got.models.length === 0 && !got.error) setCatalogError("No models listed.");
    } catch (e) {
      setCatalogError((e as Error).message);
    } finally {
      setBusy(null);
    }
  }
  async function runTest() {
    setBusy("test");
    setTest(null);
    try {
      if (dirty) await api(`/api/v1/models/${role}`, { method: "PUT", body: JSON.stringify(form) });
      setTest(await api(`/api/v1/models/${role}/test`, { method: "POST" }));
      onSaved();
    } catch (e) {
      setTest({ ok: false, error: (e as Error).message });
    } finally {
      setBusy(null);
    }
  }

  return (
    <section className="card" style={{ padding: 14, display: "flex", flexDirection: "column", gap: 10 }}>
      <div>
        <strong style={{ fontSize: 15 }}>{ROLE_TEXT[role].title}</strong>
        <div style={{ fontSize: 13, color: "var(--ink-2)", marginTop: 2 }}>{ROLE_TEXT[role].text}</div>
      </div>
      <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(180px, 1fr))", gap: 10 }}>
        <label style={{ display: "flex", flexDirection: "column", gap: 4 }}>
          <span className="lab">Provider</span>
          <select className="input" value={form.provider} onChange={(e) => pickProvider(e.target.value)}>
            {providers.map((p) => <option key={p.id} value={p.id}>{p.title}</option>)}
          </select>
        </label>
        <label style={{ display: "flex", flexDirection: "column", gap: 4 }}>
          <span className="lab">Model</span>
          <input className="input" list={`models-${role}`} value={form.model} onChange={(e) => setForm({ ...form, model: e.target.value })} placeholder={form.provider === "anthropic" ? "claude-sonnet-5-5" : "llama3.2"} />
          <datalist id={`models-${role}`}>{catalog.map((m) => <option key={m} value={m} />)}</datalist>
        </label>
        {wantsUrl && (
          <label style={{ display: "flex", flexDirection: "column", gap: 4 }}>
            <span className="lab">{form.provider === "ollama" ? "Ollama address" : "Server address"}</span>
            <input className="input" value={form.base_url} onChange={(e) => setForm({ ...form, base_url: e.target.value })} placeholder={providers.find((p) => p.id === form.provider)?.url ?? ""} />
          </label>
        )}
        <label style={{ display: "flex", flexDirection: "column", gap: 4 }}>
          <span className="lab">$ per million tokens in</span>
          <input className="input" type="number" step="0.01" min="0" value={form.price_in ?? ""} onChange={(e) => setForm({ ...form, price_in: e.target.value === "" ? null : Number(e.target.value) })} placeholder="optional" />
        </label>
        <label style={{ display: "flex", flexDirection: "column", gap: 4 }}>
          <span className="lab">$ per million tokens out</span>
          <input className="input" type="number" step="0.01" min="0" value={form.price_out ?? ""} onChange={(e) => setForm({ ...form, price_out: e.target.value === "" ? null : Number(e.target.value) })} placeholder="optional" />
        </label>
      </div>
      <div style={{ display: "flex", gap: 8, flexWrap: "wrap", alignItems: "center" }}>
        <button className="btn dark" type="button" disabled={!dirty || busy !== null} onClick={save}>{busy === "save" ? "Saving…" : "Save"}</button>
        <button className="btn" type="button" disabled={form.provider === "none" || busy !== null} onClick={runTest}>{busy === "test" ? "Testing…" : "Test"}</button>
        {form.provider !== "none" && <button className="btn" type="button" disabled={busy !== null} onClick={() => find()}>{busy === "find" ? "Looking…" : "Find models"}</button>}
        {catalogError && <span style={{ fontSize: 13, color: "var(--critical)" }}>{catalogError}</span>}
      </div>
      {misfit && (
        <div style={{ display: "flex", gap: 8, alignItems: "center", flexWrap: "wrap", fontSize: 13, color: "var(--ink)" }}>
          <AlertTriangle size={16} color="var(--warning)" aria-hidden />
          <span><strong>Wrong model.</strong> "{form.model}" is not a {PROVIDER_WORD[form.provider] ?? form.provider} model, so it cannot answer.</span>
          {suggested && <button className="btn" type="button" onClick={() => setForm({ ...form, model: suggested })}>Use {suggested}</button>}
        </div>
      )}
      {catalog.length > 0 && (
        <div style={{ display: "flex", gap: 6, alignItems: "center", flexWrap: "wrap" }}>
          <span className="lab" style={{ marginRight: 4 }}>Tap a model, then Save</span>
          {catalog.map((m) => (
            <button key={m} type="button" className={m === form.model ? "btn dark" : "btn"} aria-pressed={m === form.model} onClick={() => setForm({ ...form, model: m })}
              style={{ display: "inline-flex", alignItems: "center", gap: 4 }}>
              {m === form.model && <Check size={14} aria-hidden />}{m}{m === suggested && <span style={{ fontSize: 11, opacity: 0.75 }}> · suggested</span>}
            </button>
          ))}
        </div>
      )}
      {test && (
        <div style={{ display: "flex", gap: 6, alignItems: "flex-start", fontSize: 13, color: test.ok ? "var(--safe)" : "var(--critical)" }}>
          {test.ok ? <CheckCircle2 size={16} aria-hidden style={{ flexShrink: 0, marginTop: 1 }} /> : <AlertTriangle size={16} aria-hidden style={{ flexShrink: 0, marginTop: 1 }} />}
          <span><strong>{test.ok ? "Works." : "Not working."}</strong> {test.ok ? `${test.model} replied "${test.reply}" in ${test.ms} ms.` : test.error}</span>
        </div>
      )}
      {form.provider === "ollama" && <Where id="ollama" />}
      {form.provider === "anthropic" && <Where id="anthropic_pricing" />}
      {form.provider !== "anthropic" && <div style={{ fontSize: 12, color: "var(--muted)" }}>Prices are optional. With both entered, every chat and review shows its cost in dollars; without them, tokens only.</div>}
    </section>
  );
}

function Secrets({ state, canStore, onSaved }: { state: Record<string, boolean>; canStore: boolean; onSaved: () => void }) {
  const [values, setValues] = useState<Record<string, string>>({ anthropic: "", openai: "" });
  const [note, setNote] = useState<string | null>(null);
  async function store(name: string) {
    try {
      await api("/api/v1/models/secrets", { method: "PUT", body: JSON.stringify({ name, value: values[name] }) });
      setValues({ ...values, [name]: "" });
      setNote(values[name] ? `The ${name} key is stored, encrypted.` : `The ${name} key is removed.`);
      onSaved();
    } catch (e) {
      setNote((e as Error).message);
    }
  }
  const rows = [
    { name: "anthropic", title: "Anthropic API key", where: "anthropic_key" as const },
    { name: "openai", title: "OpenAI-compatible key", where: "openai_key" as const },
  ];
  return (
    <section className="card" style={{ padding: 14, display: "flex", flexDirection: "column", gap: 10 }}>
      <div>
        <strong style={{ fontSize: 15 }}>Keys</strong>
        <div style={{ fontSize: 13, color: "var(--ink-2)", marginTop: 2 }}>Paste a key here, never into a chat. It is encrypted with the data key, used only from the server, and never shown again.</div>
        {!canStore && <div style={{ fontSize: 13, color: "var(--critical)", marginTop: 4 }}>FA_DATA_KEY is empty, so keys cannot be stored. Set it in the app's environment first.</div>}
      </div>
      {rows.map((r) => (
        <form
          key={r.name}
          onSubmit={(e) => {
            e.preventDefault();
            store(r.name);
          }}
          style={{ display: "flex", gap: 8, flexWrap: "wrap", alignItems: "end" }}
        >
          <label style={{ display: "flex", flexDirection: "column", gap: 4, flex: "1 1 260px" }}>
            <span className="lab">{r.title} · {state[r.name] ? "set" : "not set"}</span>
            <input className="input" type="password" autoComplete="off" value={values[r.name]} onChange={(e) => setValues({ ...values, [r.name]: e.target.value })} placeholder={state[r.name] ? "Paste a new key to replace it, or save empty to remove it" : "Paste the key"} disabled={!canStore} />
            <Where id={r.where} />
          </label>
          <button className="btn dark" type="submit" disabled={!canStore}>Save</button>
        </form>
      ))}
      {note && <div style={{ fontSize: 13, color: "var(--ink-2)" }}>{note}</div>}
    </section>
  );
}

function Rules() {
  const { data, reload } = useApi<{ rules: string; goals: string; buffer: number }>("/api/v1/rules");
  const [rules, setRules] = useState("");
  const [goals, setGoals] = useState("");
  const [saved, setSaved] = useState(false);
  useEffect(() => {
    if (data) {
      setRules(data.rules);
      setGoals(data.goals);
    }
  }, [data]);
  async function save() {
    await api("/api/v1/rules", { method: "PUT", body: JSON.stringify({ rules, goals }) });
    setSaved(true);
    reload();
    setTimeout(() => setSaved(false), 2000);
  }
  return (
    <section className="card" style={{ padding: 14, display: "flex", flexDirection: "column", gap: 10 }}>
      <div>
        <strong style={{ fontSize: 15 }}>Your rules and goals</strong>
        <div style={{ fontSize: 13, color: "var(--ink-2)", marginTop: 2 }}>Both models read these. The reviewer proposes rules you can add with one tap; you can write your own here. Plain sentences, one per line.</div>
      </div>
      <label style={{ display: "flex", flexDirection: "column", gap: 4 }}>
        <span className="lab">Standing rules</span>
        <textarea className="input" rows={5} value={rules} onChange={(e) => setRules(e.target.value)} placeholder={"- Never let checking go under $100.\n- Pay the advances first, always."} style={{ resize: "vertical" }} />
      </label>
      <label style={{ display: "flex", flexDirection: "column", gap: 4 }}>
        <span className="lab">Goals</span>
        <textarea className="input" rows={3} value={goals} onChange={(e) => setGoals(e.target.value)} placeholder="Clear the advance apps by spring. Then a $1,000 buffer." style={{ resize: "vertical" }} />
      </label>
      <div style={{ display: "flex", gap: 8, alignItems: "center" }}>
        <button className="btn dark" type="button" onClick={save}>Save</button>
        {saved && <span style={{ fontSize: 13, color: "var(--safe)", fontWeight: 700 }}>Saved. A review is due.</span>}
        {data && <span style={{ fontSize: 12, color: "var(--muted)" }}>The buffer the engine keeps is ${data.buffer.toFixed(0)}; the reviewer can propose another, and you apply it with one tap.</span>}
      </div>
    </section>
  );
}

export function Models() {
  const { data, reload, error } = useApi<ModelsOut>("/api/v1/models");
  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 14, maxWidth: 900 }}>
      <h1 style={{ margin: 0, fontSize: 20, fontWeight: 800 }}>Models</h1>
      <p style={{ margin: 0, color: "var(--ink-2)" }}>Two roles. Neither can change a number: the engine computes, the models explain and push back. Every figure a model states is checked against what the engine returned.</p>
      {error && <div style={{ color: "var(--critical)" }}>{error.message}</div>}
      {data && (
        <>
          <RoleCard role="advisor" conf={data.roles.advisor} providers={data.providers} defaults={data.defaults} onSaved={reload} />
          <RoleCard role="reviewer" conf={data.roles.reviewer} providers={data.providers} defaults={data.defaults} onSaved={reload} />
          <Secrets state={data.secrets} canStore={data.can_store_secrets} onSaved={reload} />
          <Rules />
        </>
      )}
    </div>
  );
}
