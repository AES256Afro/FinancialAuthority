# FinancialAuthority for iPhone, iPad and Apple Watch

A native app that reads your own FinancialAuthority server: Today (safe to spend, payday, the one thing, the
streaks), the paychecks as strips, rings or a table, Cover with the payday sheet, Home Screen and Lock Screen
widgets, and an Apple Watch app with complications. It signs in through the server's own OAuth with the **read**
scope only, so it can look and never change, confirm or approve anything; the token sits in the Keychain and the
password never touches the phone.

The installable web app (Safari › Share › Add to Home Screen) already gives you the same pages, notifications
and the watch mirror for free. This native app adds Home Screen and Lock Screen widgets and watch complications that refresh on their own.

## What it needs

- A Mac with Xcode 15 or later, and [XcodeGen](https://github.com/yonaskolb/XcodeGen) (`brew install xcodegen`).
- Your Apple ID in Xcode › Settings › Accounts. A free account can run the app on your own devices, re-signed
  every seven days; TestFlight and the App Store need the Apple Developer Program, $99 a year. That fee is the only
  paid thing anywhere in this project, and it is optional.
- The server reachable from the phone over https: the tailnet https address works on any device in the tailnet.

## Build and run

1. In `project.yml`, set `BUNDLE_PREFIX` to your own reverse domain (for example `com.yourname.financialauthority`)
   and `DEVELOPMENT_TEAM` to your team ID.
2. `cd apple && xcodegen`
3. Open `FinancialAuthority.xcodeproj`, add an app icon to an `AppIcon` asset set if you want one (the PNGs in
   `frontend/public` work), pick your iPhone, and Run.
4. In the app: enter the server's https address, tap Sign in, sign in on the server's own page, and tap Allow.
5. On the watch: install the watch app from the iPhone's Watch app; it receives the sign-in from the phone. Add the
   Authority complication to a watch face; add the widget to the Home Screen or the Lock Screen.

## How it is built

| Folder | What is in it |
|---|---|
| `Shared/` | The models, the REST client with token refresh, the Keychain, money and date formatting, the part and state colours |
| `App/` | The iPhone and iPad app: sign-in (OAuth with PKCE in a private browser sheet), Today, Paychecks (Swift Charts), Cover, Settings, and the hand-off to the watch |
| `Widgets/` | The glance widget: small and medium on the Home Screen, inline, circular and rectangular on the Lock Screen, and the same code as watch complications |
| `Watch/` | The watch app, which keeps the sign-in the phone sends in its own Keychain |

Every figure comes from the server's engine, as on the web; nothing is computed on the phone. Revoke the app at
any time on the web app's Settings › Clients.

Status: written against iOS 17 and watchOS 10 and the server's tested OAuth flow for this app
(`backend/tests/test_oauth.py`); it has not yet been compiled, because the work so far was done on Windows. The
first build on a Mac may need small fixes.
