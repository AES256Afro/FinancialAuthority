import type { ReactNode } from "react";
import { useApi } from "../api";
import { ownerName, roleOf, type MePerson } from "../types-people";

/** Who is looking, for a control the server keeps to the owner, or to whoever made the row. An older server or the demo
 * sends no role, which reads as the owner; the server still has the last word. */
export function useViewer(): { owner: boolean; personId: number | null; ownerWord: string } {
  const me = useApi<MePerson>("/api/auth/me").data;
  return { owner: roleOf(me) === "owner", personId: me?.person_id ?? null, ownerWord: ownerName(me?.owner) };
}

/** Controls only the owner can use: shown to a second person, switched off, with the reason, rather than as a form that
 * answers "refused" after they have filled it in. The fieldset takes no room, so nothing moves for the owner. */
export function OwnerLocked({ what, children }: { what: string; children: ReactNode }) {
  const { owner, ownerWord } = useViewer();
  return (
    <fieldset disabled={!owner} style={{ display: "contents" }}>
      {!owner && <p style={{ margin: 0, fontSize: 13, color: "var(--ink-2)" }}>Only {ownerWord} can change {what}. You can look.</p>}
      {children}
    </fieldset>
  );
}
