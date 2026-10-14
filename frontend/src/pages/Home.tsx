// The home: the view chosen in Settings › Appearance, or the one named in ?view= for a direct link.
import { useSearchParams } from "react-router-dom";
import { getPrefs } from "../prefs";
import type { Snapshot } from "../types";
import { viewById } from "../views";

export function Home({ snapshot }: { snapshot: Snapshot }) {
  const [params] = useSearchParams();
  const view = viewById(params.get("view") ?? getPrefs().view);
  const Component = view.component;
  return <Component snapshot={snapshot} />;
}
