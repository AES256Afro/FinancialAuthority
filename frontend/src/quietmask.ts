// "Hide amounts" blurs figures on the page (tokens.css, .num). A blur cannot reach what the browser draws or reads
// itself: a tooltip from a title, a chart's <title>, a screen reader's aria-label. While amounts are hidden, every
// dollar figure in those is put in words here, in one place, so no screen has to remember it; the words come back
// when amounts are shown again.
const FIGURE = /[−+-]?\$\s?\d[\d,]*(?:\.\d+)?(?:\s?[kKmM]\b)?/g;
const HIDDEN = "hidden amount";
const ATTRS = ["title", "aria-label", "aria-valuetext"];
const SELECTOR = ATTRS.map((a) => `[${a}]`).join(",");

export function maskFigures(text: string): string {
  return text.replace(FIGURE, HIDDEN);
}

const heldAttrs = new Map<Element, Map<string, string>>();
const heldTitles = new Map<Node, string>();
let watcher: MutationObserver | null = null;

function maskElement(el: Element): void {
  for (const attr of ATTRS) {
    const value = el.getAttribute(attr);
    if (!value) continue;
    const masked = maskFigures(value);
    if (masked === value) continue;
    let held = heldAttrs.get(el);
    if (!held) heldAttrs.set(el, (held = new Map()));
    held.set(attr, value);
    el.setAttribute(attr, masked);
  }
}

function maskSvgTitle(title: Element): void {
  for (const node of title.childNodes) {
    const value = node.nodeValue;
    if (!value) continue;
    const masked = maskFigures(value);
    if (masked === value) continue;
    heldTitles.set(node, value);
    node.nodeValue = masked;
  }
}

function maskTree(root: Element): void {
  maskElement(root);
  root.querySelectorAll(SELECTOR).forEach(maskElement);
  if (root.tagName.toLowerCase() === "title" && root.closest("svg")) maskSvgTitle(root);
  root.querySelectorAll("svg title").forEach(maskSvgTitle);
}

/** Turn the masking on or off for the whole document. Safe to call again with the same answer. */
export function setQuietMask(on: boolean): void {
  if (on && !watcher) {
    if (typeof MutationObserver === "undefined") return;
    maskTree(document.documentElement);
    watcher = new MutationObserver((changes) => {
      for (const c of changes) {
        if (c.type === "attributes") maskElement(c.target as Element);
        else if (c.type === "characterData") {
          const parent = c.target.parentElement;
          if (parent?.tagName.toLowerCase() === "title" && parent.closest("svg")) maskSvgTitle(parent);
        } else c.addedNodes.forEach((n) => { if (n.nodeType === 1) maskTree(n as Element); });
      }
    });
    watcher.observe(document.documentElement, { subtree: true, childList: true, characterData: true, attributes: true, attributeFilter: ATTRS });
  } else if (!on && watcher) {
    watcher.disconnect();
    watcher = null;
    heldAttrs.forEach((held, el) => held.forEach((value, attr) => el.setAttribute(attr, value)));
    heldTitles.forEach((value, node) => { node.nodeValue = value; });
    heldAttrs.clear();
    heldTitles.clear();
  }
}
