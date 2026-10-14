import { getPrefs } from "../prefs";
import type { Snapshot } from "../types";
import { viewById } from "../views";

export function Home({ snapshot }: { snapshot: Snapshot }) {
  const view = viewById(getPrefs().view);
  const Component = view.component;
  return <Component snapshot={snapshot} />;
}
