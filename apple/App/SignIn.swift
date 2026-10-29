// Sign in with the server's own OAuth: the app registers itself once, opens the server's consent page in a
// private browser sheet, the owner signs in there and allows read access, and the server hands back a code the
// app exchanges for tokens. The app never sees or keeps the password.
import AuthenticationServices
import CryptoKit
import SwiftUI

struct ServerMetadata: Decodable {
    let authorization_endpoint: URL
    let token_endpoint: URL
    let registration_endpoint: URL?
}

enum SignIn {
    static let callbackScheme = "financialauthority"
    static let callback = "financialauthority://oauth/callback"

    static func pkce() -> (verifier: String, challenge: String) {
        var bytes = [UInt8](repeating: 0, count: 48)
        _ = SecRandomCopyBytes(kSecRandomDefault, bytes.count, &bytes)
        let verifier = Data(bytes).base64URL
        let challenge = Data(SHA256.hash(data: Data(verifier.utf8))).base64URL
        return (verifier, challenge)
    }

    static func metadata(for base: URL) async throws -> ServerMetadata {
        let (data, _) = try await URLSession.shared.data(from: base.appending(path: "/.well-known/oauth-authorization-server"))
        return try JSONDecoder().decode(ServerMetadata.self, from: data)
    }

    /// The server address as typed, if the app may use it: https, or plain http only to this computer's loopback (a
    /// dev server seen from the Simulator). Kept without a trailing slash so paths join cleanly.
    static func server(_ typed: String) -> URL? {
        var text = typed.trimmingCharacters(in: .whitespacesAndNewlines)
        while text.hasSuffix("/") { text.removeLast() }
        guard let url = URL(string: text), let host = url.host()?.lowercased() else { return nil }
        if url.scheme == "https" { return url }
        return url.scheme == "http" && ["localhost", "127.0.0.1", "::1"].contains(host) ? url : nil
    }

    /// The whole sign-in for one client: register it with the read scope, open the server's consent page in a
    /// private browser sheet, check the state, and trade the code for tokens. The phone and the watch each run it
    /// once with their own name, so each holds its own client and renews its own refresh token.
    @MainActor
    static func authorize(base: URL, clientName: String, using webAuth: WebAuthenticationSession) async throws -> Credentials {
        let meta = try await metadata(for: base)
        guard let registration = meta.registration_endpoint else { throw APIError.server(0, "the server does not register apps") }
        let clientID = try await register(at: registration, name: clientName)
        let (verifier, challenge) = pkce()
        let state = UUID().uuidString
        guard var components = URLComponents(url: meta.authorization_endpoint, resolvingAgainstBaseURL: false) else { throw APIError.decoding("the sign-in address") }
        components.queryItems = [
            .init(name: "response_type", value: "code"), .init(name: "client_id", value: clientID), .init(name: "redirect_uri", value: callback),
            .init(name: "code_challenge", value: challenge), .init(name: "code_challenge_method", value: "S256"), .init(name: "state", value: state), .init(name: "scope", value: "read"),
        ]
        guard let start = components.url else { throw APIError.decoding("the sign-in address") }
        let back = try await webAuth.authenticate(using: start, callbackURLScheme: callbackScheme, preferredBrowserSession: .ephemeral)
        let items = URLComponents(url: back, resolvingAgainstBaseURL: false)?.queryItems ?? []
        guard items.first(where: { $0.name == "state" })?.value == state, let code = items.first(where: { $0.name == "code" })?.value else {
            throw SignInRefused(allowed: items.first(where: { $0.name == "error" }) == nil)
        }
        let tokens = try await exchange(code: code, verifier: verifier, clientID: clientID, at: meta.token_endpoint)
        return Credentials(baseURL: base, clientID: clientID, tokenEndpoint: meta.token_endpoint, accessToken: tokens.access_token, refreshToken: tokens.refresh_token)
    }

    static func register(at endpoint: URL, name: String) async throws -> String {
        var request = URLRequest(url: endpoint)
        request.httpMethod = "POST"
        request.setValue("application/json", forHTTPHeaderField: "Content-Type")
        let body: [String: Any] = ["client_name": name, "redirect_uris": [callback], "token_endpoint_auth_method": "none",
                                   "grant_types": ["authorization_code", "refresh_token"], "response_types": ["code"], "scope": "read"]
        request.httpBody = try JSONSerialization.data(withJSONObject: body)
        let (data, response) = try await URLSession.shared.data(for: request)
        guard (response as? HTTPURLResponse)?.statusCode == 201, let info = try JSONSerialization.jsonObject(with: data) as? [String: Any], let id = info["client_id"] as? String else {
            throw APIError.server((response as? HTTPURLResponse)?.statusCode ?? 0, "the server did not register the app")
        }
        return id
    }

    static func exchange(code: String, verifier: String, clientID: String, at endpoint: URL) async throws -> TokenResponse {
        var request = URLRequest(url: endpoint)
        request.httpMethod = "POST"
        request.setValue("application/x-www-form-urlencoded", forHTTPHeaderField: "Content-Type")
        request.httpBody = FormBody.encode(["grant_type": "authorization_code", "code": code, "code_verifier": verifier, "client_id": clientID, "redirect_uri": callback])
        let (data, response) = try await URLSession.shared.data(for: request)
        guard (response as? HTTPURLResponse)?.statusCode == 200 else { throw APIError.server((response as? HTTPURLResponse)?.statusCode ?? 0, "the code was not accepted") }
        return try JSONDecoder().decode(TokenResponse.self, from: data)
    }
}

struct SignInRefused: LocalizedError {
    let allowed: Bool
    var errorDescription: String? { allowed ? "The server did not send a code." : "Access was not allowed." }
}

extension Data {
    var base64URL: String {
        base64EncodedString().replacingOccurrences(of: "+", with: "-").replacingOccurrences(of: "/", with: "_").replacingOccurrences(of: "=", with: "")
    }
}

struct SignInView: View {
    @Environment(\.webAuthenticationSession) private var webAuth
    @AppStorage("serverAddress", store: UserDefaults(suiteName: AppGroup.id)) private var address = ""
    @State private var busy = false
    @State private var note: String?
    var onSignedIn: () -> Void

    var body: some View {
        Form {
            Section {
                VStack(alignment: .leading, spacing: 6) {
                    Text("FinancialAuthority").font(.title2.bold())
                    Text("Your own server, read only from here. The phone keeps the server's token in the Keychain; never your password.")
                        .font(.footnote).foregroundStyle(.secondary)
                }
            }
            Section("Your server") {
                TextField("https://your-server:8430", text: $address)
                    .keyboardType(.URL).textInputAutocapitalization(.never).autocorrectionDisabled()
                Button {
                    Task { await signIn() }
                } label: {
                    if busy { ProgressView() } else { Text("Sign in") }
                }
                .disabled(busy || SignIn.server(address) == nil)
            }
            if let note { Section { Text(note).font(.footnote).foregroundStyle(.secondary) } }
        }
    }

    private func signIn() async {
        guard let base = SignIn.server(address) else { return }
        busy = true
        defer { busy = false }
        do {
            Keychain.save(try await SignIn.authorize(base: base, clientName: "FinancialAuthority for iPhone and iPad", using: webAuth))
            onSignedIn()
        } catch let error as ASWebAuthenticationSessionError where error.code == .canceledLogin {
            note = nil
        } catch {
            note = error.localizedDescription
        }
    }
}

enum AppGroup {
    static var id: String { (Bundle.main.object(forInfoDictionaryKey: "FAAppGroup") as? String) ?? "group.financialauthority" }
}
