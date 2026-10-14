// Builds the static demo: the same app, routed after the hash, answered from demo-data.json.
// Output lands in frontend/dist-demo with relative asset paths, ready to publish as files beside one page.
import { copyFileSync, existsSync, mkdirSync, readFileSync, writeFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import { build } from "vite";

process.env.VITE_STATIC_DEMO = "1";
const root = join(dirname(fileURLToPath(import.meta.url)), "..");
const data = join(root, "demo", "demo-data.json");
if (!existsSync(data)) {
  console.error("demo/demo-data.json is missing: run `uv run python scripts/capture_demo.py` in backend/ first");
  process.exit(1);
}
await build({ root });
const out = join(root, "dist-demo");
mkdirSync(out, { recursive: true });
copyFileSync(data, join(out, "demo-data.json"));

// The page an artifact publishes is a fragment: the host adds the document skeleton, so the built index.html
// is reduced to its title, its stylesheet and script links, and the root element.
const html = readFileSync(join(out, "index.html"), "utf8");
const links = [...html.matchAll(/<link[^>]+>/g)].map((m) => m[0]).filter((l) => /stylesheet|preconnect/.test(l) && !/manifest|icon/.test(l));
const scripts = [...html.matchAll(/<script[^>]*src="[^"]+"[^>]*><\/script>/g)].map((m) => m[0]);
const page = [
  "<title>FinancialAuthority Demo</title>",
  '<meta name="description" content="A clickable demo of FinancialAuthority over a synthetic household. Nothing here is real." />',
  ...links,
  '<style>html, body { margin: 0; min-height: 100%; } body { background: #f6f4ef; } @media (prefers-color-scheme: dark) { :root:not([data-theme="light"]) body { background: #121417; } } :root[data-theme="dark"] body { background: #121417; }</style>',
  '<div id="root"></div>',
  ...scripts,
].join("\n");
writeFileSync(join(out, "page.html"), page + "\n");
console.log(`demo built: ${out}/page.html with ${scripts.length} script(s) and ${links.length} link(s)`);
