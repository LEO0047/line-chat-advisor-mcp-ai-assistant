import Foundation
import Security

func fail(_ message: String, status: OSStatus? = nil) -> Never {
    var payload: [String: Any] = ["ok": false, "error": message]
    if let status {
        payload["status"] = status
    }
    let data = try! JSONSerialization.data(withJSONObject: payload)
    FileHandle.standardOutput.write(data)
    FileHandle.standardOutput.write(Data([0x0a]))
    exit(1)
}

guard CommandLine.arguments.count == 3 else {
    fail("usage")
}

let service = CommandLine.arguments[1]
let account = CommandLine.arguments[2]
let secretData = FileHandle.standardInput.readDataToEndOfFile()
guard secretData.count == 32,
      let secret = String(data: secretData, encoding: .utf8),
      secret.range(of: "^[0-9a-fA-F]{32}$", options: .regularExpression) != nil else {
    fail("invalid_secret")
}

let lookup: [String: Any] = [
    kSecClass as String: kSecClassGenericPassword,
    kSecAttrService as String: service,
    kSecAttrAccount as String: account,
]
let update: [String: Any] = [kSecValueData as String: secretData]
let updateStatus = SecItemUpdate(lookup as CFDictionary, update as CFDictionary)

if updateStatus == errSecItemNotFound {
    var add = lookup
    add[kSecValueData as String] = secretData
    add[kSecAttrAccessible as String] = kSecAttrAccessibleAfterFirstUnlock
    let addStatus = SecItemAdd(add as CFDictionary, nil)
    guard addStatus == errSecSuccess else {
        fail("keychain_add_failed", status: addStatus)
    }
} else if updateStatus != errSecSuccess {
    fail("keychain_update_failed", status: updateStatus)
}

let output = try! JSONSerialization.data(withJSONObject: ["ok": true])
FileHandle.standardOutput.write(output)
FileHandle.standardOutput.write(Data([0x0a]))
