// The tokens live in the Keychain, shared with the widgets through an access group; nothing secret is written
// anywhere else. On the watch, the phone hands the tokens over through WatchConnectivity.
import Foundation
import Security

enum Keychain {
    private static let service = "financialauthority"
    private static let account = "credentials"

    /// The shared access group, from the entitlements (the team prefix comes from Info.plist).
    private static var accessGroup: String? {
        guard let prefix = Bundle.main.object(forInfoDictionaryKey: "AppIdentifierPrefix") as? String, !prefix.isEmpty,
              let group = Bundle.main.object(forInfoDictionaryKey: "FASharedKeychainGroup") as? String, !group.isEmpty else { return nil }
        return prefix + group
    }

    private static func base() -> [String: Any] {
        var q: [String: Any] = [kSecClass as String: kSecClassGenericPassword, kSecAttrService as String: service, kSecAttrAccount as String: account]
        if let group = accessGroup { q[kSecAttrAccessGroup as String] = group }
        return q
    }

    static func save(_ credentials: Credentials) {
        guard let data = try? JSONEncoder().encode(credentials) else { return }
        SecItemDelete(base() as CFDictionary)
        var add = base()
        add[kSecValueData as String] = data
        add[kSecAttrAccessible as String] = kSecAttrAccessibleAfterFirstUnlockThisDeviceOnly  // never carried to another device by a backup
        SecItemAdd(add as CFDictionary, nil)
    }

    static func load() -> Credentials? {
        var q = base()
        q[kSecReturnData as String] = true
        q[kSecMatchLimit as String] = kSecMatchLimitOne
        var out: AnyObject?
        guard SecItemCopyMatching(q as CFDictionary, &out) == errSecSuccess, let data = out as? Data else { return nil }
        return try? JSONDecoder().decode(Credentials.self, from: data)
    }

    static func clear() {
        SecItemDelete(base() as CFDictionary)
    }
}
