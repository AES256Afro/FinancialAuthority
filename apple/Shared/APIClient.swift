// One client for the app, the widgets and the watch: the server's REST with the bearer token the app was
// given, refreshed when it runs out. Read only: the app holds the read scope, so it can look and never change.
import Foundation

enum APIError: LocalizedError {
    case signedOut
    case server(Int, String)
    case decoding(String)

    var errorDescription: String? {
        switch self {
        case .signedOut:
            #if os(watchOS)
            return "Not signed in. Open Authority on this watch, then tap Sign in the watch in the iPhone app's Settings."
            #else
            return "Not signed in. Open Authority and sign in to your server."
            #endif
        case let .server(code, text): return "The server answered \(code): \(text)"
        case let .decoding(what): return "Could not read the answer: \(what)"
        }
    }
}

actor APIClient {
    static let shared = APIClient()
    private let session: URLSession
    /// The refresh under way. The screens read in parallel, so several requests meet an expired token at once; they
    /// all wait for this one exchange instead of each spending the same refresh token (the second of which the
    /// server refuses, and after a minute would treat as stolen and end every token the app holds).
    private var refreshing: Task<Credentials?, Error>?

    init(session: URLSession = .shared) {
        self.session = session
    }

    func get<T: Decodable & Sendable>(_ path: String, as type: T.Type) async throws -> T {
        guard var creds = Keychain.load() else { throw APIError.signedOut }
        var (data, status) = try await fetch(path, creds: creds)
        if status == 401, let refreshed = try await refreshOnce(creds) {
            creds = refreshed
            (data, status) = try await fetch(path, creds: creds)
        }
        guard (200 ..< 300).contains(status) else {
            if status == 401 { throw APIError.signedOut }
            throw APIError.server(status, String(data: data, encoding: .utf8)?.prefix(160).description ?? "")
        }
        do {
            return try JSONDecoder().decode(T.self, from: data)
        } catch {
            throw APIError.decoding(String(describing: T.self))
        }
    }

    func glance() async throws -> Glance { try await get("/api/v1/glance", as: Glance.self) }
    func today() async throws -> TodayThing { try await get("/api/v1/today", as: TodayThing.self) }
    func cover() async throws -> Cover { try await get("/api/v1/cover", as: Cover.self) }
    func paychecks() async throws -> PaycheckViews { try await get("/api/v1/paychecks", as: PaycheckViews.self) }

    private func fetch(_ path: String, creds: Credentials) async throws -> (Data, Int) {
        var request = URLRequest(url: creds.baseURL.appending(path: path))
        request.setValue("Bearer \(creds.accessToken)", forHTTPHeaderField: "Authorization")
        request.setValue("application/json", forHTTPHeaderField: "Accept")
        request.timeoutInterval = 20
        let (data, response) = try await session.data(for: request)
        return (data, (response as? HTTPURLResponse)?.statusCode ?? 0)
    }

    private func refreshOnce(_ creds: Credentials) async throws -> Credentials? {
        if let running = refreshing { return try await running.value }
        let task = Task { try await refresh(creds) }
        refreshing = task
        defer { refreshing = nil }
        return try await task.value
    }

    /// Exchange the refresh token for a new pair; signed out only if the server refuses it. The app and its widget
    /// share one Keychain item and run in separate processes, so when one of them rotated the pair a moment ago the
    /// other's refresh is refused as spent: it then takes the pair now stored instead of signing the phone out.
    private func refresh(_ creds: Credentials) async throws -> Credentials? {
        guard let refresh = creds.refreshToken else { return nil }
        if let stored = Keychain.load(), stored.refreshToken != refresh { return stored }
        var request = URLRequest(url: creds.tokenEndpoint)
        request.httpMethod = "POST"
        request.setValue("application/x-www-form-urlencoded", forHTTPHeaderField: "Content-Type")
        request.httpBody = FormBody.encode(["grant_type": "refresh_token", "refresh_token": refresh, "client_id": creds.clientID])
        let (data, response) = try await session.data(for: request)
        let status = (response as? HTTPURLResponse)?.statusCode ?? 0
        guard status == 200, let tokens = try? JSONDecoder().decode(TokenResponse.self, from: data) else {
            if let stored = Keychain.load(), stored.refreshToken != refresh { return stored }
            if status == 400 || status == 401 { Keychain.clear() }  // refused; a server error or an outage keeps the sign-in
            return nil
        }
        var next = creds
        next.accessToken = tokens.access_token
        next.refreshToken = tokens.refresh_token ?? creds.refreshToken
        Keychain.save(next)
        return next
    }
}

extension APIClient {
    /// Sign out: end this device's tokens on the server (each is revoked on its own), then forget them here. A
    /// server out of reach still signs the device out; the web app's Clients page can revoke what is left.
    static func signOut() async {
        struct Metadata: Decodable { let revocation_endpoint: URL? }
        if let creds = Keychain.load(),
           let (data, _) = try? await URLSession.shared.data(from: creds.baseURL.appending(path: "/.well-known/oauth-authorization-server")),
           let endpoint = (try? JSONDecoder().decode(Metadata.self, from: data))?.revocation_endpoint {
            for token in [creds.refreshToken, creds.accessToken].compactMap({ $0 }) {
                var request = URLRequest(url: endpoint)
                request.httpMethod = "POST"
                request.setValue("application/x-www-form-urlencoded", forHTTPHeaderField: "Content-Type")
                // The server's OAuth library wants the secret field even from a public client; empty is accepted.
                request.httpBody = FormBody.encode(["token": token, "client_id": creds.clientID, "client_secret": ""])
                request.timeoutInterval = 10
                _ = try? await URLSession.shared.data(for: request)
            }
        }
        Keychain.clear()
    }
}

struct TokenResponse: Decodable {
    let access_token: String
    let refresh_token: String?
    let scope: String?
}

enum FormBody {
    static func encode(_ fields: [String: String]) -> Data {
        var allowed = CharacterSet.alphanumerics
        allowed.insert(charactersIn: "-._~")
        return fields.map { "\($0.key)=\($0.value.addingPercentEncoding(withAllowedCharacters: allowed) ?? "")" }.joined(separator: "&").data(using: .utf8) ?? Data()
    }
}
