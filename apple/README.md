# FinancialAuthority for iPhone, iPad and Apple Watch

A native app that reads your own FinancialAuthority server: Today (safe to spend, payday, the one thing, the
streaks), the paychecks as strips, rings or a table, Cover with the payday sheet, Home Screen and Lock Screen
widgets, and an Apple Watch app with complications. It signs in through the server's own OAuth with the **read**
scope only, so it can look and never change, confirm or approve anything; the token sits in the Keychain and the
password never touches the phone.

The installable web app (Safari › Share › Add to Home Screen) already gives you the same pages, notifications
and the watch mirror for free. This native app adds Home Screen and Lock Screen widgets and watch complications that refresh on their own.

## What it needs

- A Mac with Xcode 16 or later (Swift 6; built and tested with Xcode 27.1), and [XcodeGen](https://github.com/yonaskolb/XcodeGen) (`brew install xcodegen`).
- Your Apple ID in Xcode › Settings › Accounts. A free account can run the app on your own devices, re-signed
  every seven days; TestFlight and the App Store need the Apple Developer Program, $99 a year. That fee is the only
  paid thing anywhere in this project, and it is optional.
- The server reachable from the phone over https: the tailnet https address works on any device in the tailnet.

## Build and run

1. In `project.yml`, set `BUNDLE_PREFIX` to your own reverse domain (for example `com.yourname.financialauthority`)
   and `DEVELOPMENT_TEAM` to your team ID.
2. `cd apple && xcodegen`. The project, the Info.plists and the entitlements are made from `project.yml` and are
   not committed (`apple/.gitignore`).
3. Open `FinancialAuthority.xcodeproj`, add an app icon to an `AppIcon` asset set if you want one (the PNGs in
   `frontend/public` work), pick your iPhone, and Run.
4. In the app: enter the server's https address, tap Sign in, sign in on the server's own page, and tap Allow.
5. On the watch: install the watch app from the iPhone's Watch app and open it. On the iPhone, tap Settings ›
   Sign in the watch and sign in on the server's page once more. The watch gets a client and a refresh token of
   its own (the phone hands it over once and keeps no copy), so the phone and the watch each renew their own
   token and signing one out leaves the other signed in. Add the Authority complication to a watch face; add the
   widget to the Home Screen or the Lock Screen.

## Build on the command line

If `xcode-select -p` names the Command Line Tools rather than Xcode, either run
`sudo xcode-select -s /Applications/Xcode.app/Contents/Developer` once or put
`DEVELOPER_DIR=/Applications/Xcode.app/Contents/Developer` before each command below.

```sh
cd apple && xcodegen
# The iPhone app with its widget extension, the watch app and its complications (dependencies of the app):
xcodebuild -project FinancialAuthority.xcodeproj -scheme FinancialAuthority -destination 'generic/platform=iOS Simulator' -derivedDataPath build/DD build
xcodebuild -project FinancialAuthority.xcodeproj -scheme FAWatch -destination 'generic/platform=watchOS Simulator' -derivedDataPath build/DD build
# The same, plus the UI tests and the widget tests:
xcodebuild -project FinancialAuthority.xcodeproj -scheme FinancialAuthority -destination 'generic/platform=iOS Simulator' -derivedDataPath build/DD build-for-testing
```

## Try it on the Simulator against a local server

The app accepts plain http only to this computer (`localhost`), so a Simulator can sign in to a dev server with
the synthetic household. Nothing real is involved.

1. Start the server: `backend/.venv/bin/python backend/scripts/dev_signin.py` (http://localhost:8001; its test
   password is in that script).
2. Boot an iPhone Simulator that is paired with a watch Simulator (`xcrun simctl list pairs`), and in the app sign
   in to `http://localhost:8001`.
3. For the watch: `xcrun simctl install <watch-udid> build/DD/Build/Products/Debug-watchsimulator/FAWatch.app`,
   then `xcrun simctl launch <watch-udid> $BUNDLE_PREFIX.watchkitapp`, then Settings › Sign in the watch on the
   phone.

## Tests

Both suites run against the local server above, on a booted Simulator (`<udid>` from `xcrun simctl list devices`).
The UI tests drive the server's own sign-in page with its test password, passed through the environment.

```sh
TEST_RUNNER_FA_DEV_SERVER=http://localhost:8001 TEST_RUNNER_FA_DEV_PASSWORD='<the password in dev_signin.py>' \
  xcodebuild -project FinancialAuthority.xcodeproj -scheme FinancialAuthority -destination 'id=<udid>' \
  -derivedDataPath build/DD -resultBundlePath build/results/run.xcresult test-without-building \
  -only-testing:FAUITests/AppFlowTests/test1SignInAndEveryScreen -only-testing:FAUITests/AppFlowTests/test2RelaunchKeepsTheToken \
  -only-testing:FAUITests/AppFlowTests/test3SignOutAndIn -only-testing:FAWidgetTests
```

| Test | What it proves |
|---|---|
| `test1SignInAndEveryScreen` | Sign-in through the server's page (a refused stored token goes back to sign-in), Today showing the glance, Paychecks as strips, rings and table, Cover with the sheet, Settings |
| `test2RelaunchKeepsTheToken` | Two relaunches open on Today with the figures: the token survives in the Keychain |
| `test3SignOutAndIn` | Sign out revokes this device's tokens on the server; signing in again works |
| `test4ScreensOnly` | Every screen once, for a run at another size, text size (`xcrun simctl ui <udid> content_size accessibility-extra-extra-extra-large`) or appearance (`xcrun simctl ui <udid> appearance dark`) |
| `test5SignInTheWatch` | With the watch app open on the paired watch Simulator: the watch's own sign-in reaches the watch and is confirmed |
| `FAWidgetTests` | The widget's timeline provider returns the server's glance figure for figure, and every family draws in light and dark (attached images) |

Each screen is attached to the result as a screenshot and as its visible text
(`xcrun xcresulttool export attachments --path build/results/run.xcresult --output-path build/results/run`), so its
figures can be checked against `GET /api/v1/glance`, `/api/v1/cover` and `/api/v1/paychecks`.

## How it is built

| Folder | What is in it |
|---|---|
| `Shared/` | The models, the REST client with token refresh, the Keychain, money and date formatting, the part and state colours |
| `App/` | The iPhone and iPad app: sign-in (OAuth with PKCE in a private browser sheet), Today, Paychecks (Swift Charts), Cover, Settings, and the hand-off to the watch |
| `Widgets/` | The glance widget: small and medium on the Home Screen, inline, circular and rectangular on the Lock Screen, and the same code as watch complications |
| `Watch/` | The watch app, which keeps its own sign-in (sent once by the phone) in its own Keychain and renews it itself |
| `UITests/`, `WidgetTests/` | The app driven through sign-in and every screen; the widget's timeline provider against the server |

Every figure comes from the server's engine, as on the web; nothing is computed on the phone. Revoke the app at
any time on the web app's Settings › Clients.

Status: built for the Simulator with Xcode 27.1 (iOS 27, watchOS 27; deployment targets iOS 17 and watchOS 10) and
run against the dev server: every screen matched the REST, at the largest text size, in dark mode and on the
smallest iPhone Simulator, and the watch signed in on its own on a paired watch Simulator. Not yet run on a real
iPhone or watch.
