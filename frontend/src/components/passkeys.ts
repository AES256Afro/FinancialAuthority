// Passkeys in the browser. The server sends its options as JSON with base64url strings; the browser wants bytes,
// and the answer goes back as JSON again. Newer browsers do both conversions themselves; for the rest they are
// done here by hand. Every failure becomes a sentence that says what happened.
import { api } from "../api";

interface DescriptorJSON { id: string; type: string; transports?: string[] }

export interface CreationOptionsJSON {
  rp: { name: string; id?: string };
  user: { id: string; name: string; displayName: string };
  challenge: string;
  pubKeyCredParams: { type: string; alg: number }[];
  timeout?: number;
  excludeCredentials?: DescriptorJSON[];
  authenticatorSelection?: Record<string, unknown>;
  attestation?: string;
  hints?: string[];
}

export interface RequestOptionsJSON {
  challenge: string;
  timeout?: number;
  rpId?: string;
  allowCredentials?: DescriptorJSON[];
  userVerification?: string;
}

/** True when this browser can make and use passkeys here (browsers offer them only at https or on localhost). */
export function passkeysSupported(): boolean {
  return typeof window !== "undefined" && typeof window.PublicKeyCredential === "function" && typeof navigator !== "undefined" && Boolean(navigator.credentials);
}

export function fromBase64url(text: string): ArrayBuffer {
  const b64 = text.replace(/-/g, "+").replace(/_/g, "/");
  const raw = atob(b64 + "=".repeat((4 - (b64.length % 4)) % 4));
  const bytes = new Uint8Array(raw.length);
  for (let i = 0; i < raw.length; i++) bytes[i] = raw.charCodeAt(i);
  return bytes.buffer;
}

export function toBase64url(data: ArrayBuffer | ArrayBufferView): string {
  const bytes = data instanceof ArrayBuffer ? new Uint8Array(data) : new Uint8Array(data.buffer, data.byteOffset, data.byteLength);
  let raw = "";
  for (let i = 0; i < bytes.length; i++) raw += String.fromCharCode(bytes[i]);
  return btoa(raw).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");
}

function descriptor(d: DescriptorJSON): PublicKeyCredentialDescriptor {
  return { ...d, id: fromBase64url(d.id), type: "public-key" } as PublicKeyCredentialDescriptor;
}

interface NativeParsers {
  parseCreationOptionsFromJSON?: (o: unknown) => PublicKeyCredentialCreationOptions;
  parseRequestOptionsFromJSON?: (o: unknown) => PublicKeyCredentialRequestOptions;
}

// The browser's own parsers, when it has them; older browsers do not.
function native(): NativeParsers {
  return (typeof window !== "undefined" && window.PublicKeyCredential ? window.PublicKeyCredential : {}) as NativeParsers;
}

export function creationOptions(json: CreationOptionsJSON): PublicKeyCredentialCreationOptions {
  const parse = native().parseCreationOptionsFromJSON;
  if (typeof parse === "function") {
    try {
      return parse.call(window.PublicKeyCredential, json);
    } catch {
      // fall through to the conversion by hand
    }
  }
  return {
    ...json,
    challenge: fromBase64url(json.challenge),
    user: { ...json.user, id: fromBase64url(json.user.id) },
    excludeCredentials: json.excludeCredentials?.map(descriptor),
  } as PublicKeyCredentialCreationOptions;
}

export function requestOptions(json: RequestOptionsJSON): PublicKeyCredentialRequestOptions {
  const parse = native().parseRequestOptionsFromJSON;
  if (typeof parse === "function") {
    try {
      return parse.call(window.PublicKeyCredential, json);
    } catch {
      // fall through to the conversion by hand
    }
  }
  return { ...json, challenge: fromBase64url(json.challenge), allowCredentials: json.allowCredentials?.map(descriptor) } as PublicKeyCredentialRequestOptions;
}

/** The credential as the JSON the server reads: the browser's own toJSON() when it works, else built by hand. */
export function credentialJSON(credential: PublicKeyCredential): Record<string, unknown> {
  const own = credential as PublicKeyCredential & { toJSON?: () => unknown };
  if (typeof own.toJSON === "function") {
    try {
      const out = own.toJSON() as Record<string, unknown> | null;
      if (out && typeof out === "object" && typeof out.id === "string") return out;
    } catch {
      // some password managers' passkeys cannot write themselves as JSON; build it below
    }
  }
  const r = credential.response;
  const response: Record<string, unknown> = { clientDataJSON: toBase64url(r.clientDataJSON) };
  if ("attestationObject" in r) {
    const made = r as AuthenticatorAttestationResponse;
    response.attestationObject = toBase64url(made.attestationObject);
    if (typeof made.getTransports === "function") response.transports = made.getTransports();
  }
  if ("authenticatorData" in r) {
    const used = r as AuthenticatorAssertionResponse;
    response.authenticatorData = toBase64url(used.authenticatorData);
    response.signature = toBase64url(used.signature);
    response.userHandle = used.userHandle ? toBase64url(used.userHandle) : null;
  }
  return {
    id: credential.id,
    rawId: toBase64url(credential.rawId),
    type: credential.type,
    response,
    authenticatorAttachment: credential.authenticatorAttachment ?? null,
    clientExtensionResults: typeof credential.getClientExtensionResults === "function" ? credential.getClientExtensionResults() : {},
  };
}

/** Make a passkey on this phone or computer and keep it on the server under the name given. */
export async function makePasskey(name: string): Promise<{ ok: boolean; id: number; name: string }> {
  const start = await api<{ key: string; options: CreationOptionsJSON }>("/api/auth/passkeys/register/options", { method: "POST" });
  const made = await navigator.credentials.create({ publicKey: creationOptions(start.options) });
  if (!made) throw new Error("No passkey was made. Try again, and follow what your phone or computer asks.");
  return api("/api/auth/passkeys/register", { method: "POST", body: JSON.stringify({ key: start.key, credential: credentialJSON(made as PublicKeyCredential), name }) });
}

/** Sign in with a passkey: the server's challenge, the device's answer, and the server's yes. */
export async function signInWithPasskey(): Promise<void> {
  const start = await api<{ key: string; options: RequestOptionsJSON }>("/api/auth/passkeys/login/options", { method: "POST" });
  const used = await navigator.credentials.get({ publicKey: requestOptions(start.options) });
  if (!used) throw new Error("No passkey was used. Try again, or sign in with your password.");
  await api("/api/auth/passkeys/login", { method: "POST", body: JSON.stringify({ key: start.key, credential: credentialJSON(used as PublicKeyCredential) }) });
}

/** What went wrong, in words. The browser names its refusals; the server's own sentences pass through. */
export function passkeyWords(error: unknown, doing: "make" | "use"): string {
  const name = (error as { name?: string } | null)?.name;
  switch (name) {
    case "NotAllowedError":
      return doing === "make"
        ? "No passkey was made: the prompt was closed, or it waited too long. Try again, and follow what your phone or computer asks."
        : "No passkey was used: the prompt was closed, it waited too long, or this device has no passkey for this address. Try again, or sign in with your password.";
    case "AbortError":
      return "It was cancelled. Try again when you are ready.";
    case "InvalidStateError":
      return doing === "make"
        ? "This phone or computer already has a passkey for this app at this address, so there is nothing to add."
        : "This passkey cannot be used here. Sign in with your password.";
    case "SecurityError":
      return "This browser would not use a passkey at this address. Passkeys work at your https address.";
    case "NotSupportedError":
      return "This phone or computer cannot make the kind of passkey the app asks for.";
    case "ConstraintError":
      return "This phone or computer cannot check that it is you: set up a fingerprint, face or PIN on it first.";
  }
  const message = (error as Error | null)?.message;
  return message || (doing === "make" ? "The passkey was not made. Try again." : "The passkey did not work. Try again, or sign in with your password.");
}
