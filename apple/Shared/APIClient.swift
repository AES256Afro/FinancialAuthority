// One client for the app, the widgets and the watch: the server's REST with the bearer token the app was
// given, refreshed when it runs out. Read only: the app holds the read scope, so it can look and never change.
import Foundation

enum APIError: LocalizedError {
    case signedOut
    case server(Int, String)
    case decoding(String)

    var errorDescription: String? {
        switch self {
        case .signedOut: return "Sign in to your server first."
        case let .server(code, text): return "The server answered \(code): \(text)"
        case let .decoding(what): return "Could not read the answer: \(what)"
        }
    }
}

actor APIClient {
    static let shared = APIClient()
    private let session: URLSession

    init(session: URLSession = .shared) {
        self.session = session
    }

    func get<T: Decodable>(_ path: String, as type: T.Type) async throws -> T {
        guard var creds = Keychain.load() else { throw APIError.signedOut }
        var (data, status) = try await fetch(path, creds: creds)
        if status == 401, let refreshed = try await refresh(creds) {
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

    /// Exchange the refresh token for a new pair; signed out if the server says no.
    private func refresh(_ creds: Credentials) async throws -> Credentials? {
        guard let refresh = creds.refreshToken else { return nil }
        var request = URLRequest(url: creds.tokenEndpoint)
        request.httpMethod = "POST"
        request.setValue("application/x-www-form-urlencoded", forHTTPHeaderField: "Content-Type")
        request.httpBody = FormBody.encode(["grant_type": "refresh_token", "refresh_token": refresh, "client_id": creds.clientID])
        let (data, response) = try await session.data(for: request)
        guard (response as? HTTPURLResponse)?.statusCode == 200, let tokens = try? JSONDecoder().decode(TokenResponse.self, from: data) else {
            Keychain.clear()
            return nil
        }
        var next = creds
        next.accessToken = tokens.access_token
        next.refreshToken = tokens.refresh_token ?? creds.refreshToken
        Keychain.save(next)
        return next
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
