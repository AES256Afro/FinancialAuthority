// What each thing the app asks for is, and where you get it: the official page, and the clicks once there.
// One place, so every screen says the same thing. Links go to the vendor's own pages only, checked October 2026.
// {name} is filled in by the screen (an address, for example).

export interface WhereEntry {
  what: string;
  get: { label: string; href: string }[];
  steps?: string[];
  note?: string;
}

export const WHERE = {
  anthropic_key: {
    what: "A key from Anthropic that lets this app use Claude. It is billed to your Anthropic account per use; it is not your claude.ai subscription.",
    get: [
      { label: "Claude Console › API keys", href: "https://platform.claude.com/settings/keys" },
      { label: "Billing", href: "https://platform.claude.com/settings/billing" },
    ],
    steps: [
      "Sign in to the Claude Console (separate from a claude.ai subscription) and add credit under Billing.",
      "Open Settings › API keys and choose Create key; name it FinancialAuthority.",
      "Copy the key, which starts with sk-ant-, and paste it in the box here. Anthropic shows it once.",
    ],
    note: "Paste keys only here, never into a chat. The app stores it encrypted and never shows it again.",
  },
  anthropic_pricing: {
    what: "Optional: the price per million tokens, so each chat and review shows its cost in dollars.",
    get: [{ label: "Claude API pricing", href: "https://claude.com/pricing#api" }],
  },
  openai_key: {
    what: "A key for an OpenAI-compatible service. Leave it empty for a server on your own network, such as Ollama or LM Studio.",
    get: [
      { label: "OpenAI › API keys", href: "https://platform.openai.com/api-keys" },
      { label: "OpenRouter › Keys", href: "https://openrouter.ai/settings/keys" },
      { label: "Groq › API keys", href: "https://console.groq.com/keys" },
    ],
  },
  ollama: {
    what: "Ollama runs models on the server, free and private. Install it on the server (on BoxPilot, from its app catalog), then download a model once.",
    get: [
      { label: "Download Ollama", href: "https://ollama.com/download" },
      { label: "The model library", href: "https://ollama.com/library" },
    ],
    steps: [
      "Install Ollama on the server the app runs on (BoxPilot: App catalog › Ollama).",
      "On the server, download a model: ollama pull llama3.2 (small and quick) or ollama pull qwen3:8b (better at following tools).",
      "Here, choose Ollama, press Find models and tap the one you downloaded, then Save and Test.",
    ],
  },
  plaid: {
    what: "Plaid connects most US banks and cards. You make a free developer account once; the client id and secret go here, and your bank's own login opens in Plaid's window, so the app never sees your bank password.",
    get: [
      { label: "Plaid sign-up", href: "https://dashboard.plaid.com/signup" },
      { label: "Free Trial plan", href: "https://dashboard.plaid.com/trial-plan" },
      { label: "Keys", href: "https://dashboard.plaid.com/developers/keys" },
      { label: "Allowed redirect URIs", href: "https://dashboard.plaid.com/developers/api" },
    ],
    steps: [
      "Sign up at Plaid and verify your email (personal use is fine).",
      "Apply for the free Trial plan: ten real bank logins for the life of your account; removing one does not free its slot. Sandbox stays free for Plaid's test banks.",
      "Open Keys: copy the client_id, and the secret for the environment you will use (Production for real banks).",
      "Paste them here, choose the environment and Save.",
      "For banks that sign in with OAuth (Capital One, Chase and others): API › Allowed redirect URIs › Configure › Add New URI, and add {redirect}",
    ],
  },
  teller: {
    what: "Teller connects many US banks directly. Its free development environment allows a hundred bank logins; it gives an application id and a certificate with its private key, and all three go here.",
    get: [
      { label: "Teller", href: "https://teller.io" },
      { label: "Application settings", href: "https://teller.io/settings/application" },
      { label: "Certificates", href: "https://teller.io/settings/certificates" },
    ],
    steps: [
      "Get a Teller developer account from teller.io; the sign-up hands you teller.zip with the certificate and private key.",
      "Copy the application id (it starts with app_) from Application settings.",
      "Open certificate.pem and private_key.pem from teller.zip in a text editor and paste all of each, including the BEGIN and END lines. Lost them? Certificates: revoke and create a new one.",
    ],
  },
  gmail_app_password: {
    what: "Gmail is read with an app password: 16 letters Google makes for one app. It is not your Google password, and it needs 2-Step Verification turned on first.",
    get: [
      { label: "Google › App passwords", href: "https://myaccount.google.com/apppasswords" },
      { label: "Turn on 2-Step Verification", href: "https://myaccount.google.com/signinoptions/twosv" },
    ],
    steps: [
      "Turn on 2-Step Verification for your Google account if it is not on.",
      "Open App passwords, name it FinancialAuthority and create it.",
      "Username here: your full Gmail address. Password: the 16 letters Google shows, without spaces. IMAP is always on in Gmail; there is nothing to switch on.",
    ],
    note: "Google offers no app passwords for work or school accounts, for Advanced Protection, or when security keys are the only second step. Changing your Google password cancels them; make a new one.",
  },
  rocketmoney_export: {
    what: "Rocket Money has no way in for other apps; its export file carries your history (the export needs Rocket Money Premium). With a mailbox connected, the app opens the export email itself.",
    get: [
      { label: "Rocket Money on the web", href: "https://app.rocketmoney.com" },
      { label: "Rocket Money: exporting", href: "https://help.rocketmoney.com/en/articles/10296106-exporting-transactions" },
    ],
    steps: [
      "Sign in to Rocket Money on the web, on a computer, and open Transactions.",
      "Choose Export, pick all time, and confirm. Rocket Money emails you a Download File link, which works on a computer.",
      "Download the file and import it here, or leave it: a connected mailbox brings it in by itself.",
    ],
  },
  bank_file: {
    what: "Your bank's own download of transactions. Look on the bank's website, in the account's activity or statements, for Download or Export.",
    get: [],
    steps: [
      "Choose CSV, or OFX, QFX or QBO (the Quicken and QuickBooks formats, which carry the account number and are matched more surely).",
      "Pick the dates; overlapping downloads are fine, rows already here are not added twice.",
      "Choose the account they belong to below. An import can be undone on Settings › Backups.",
    ],
  },
  paystub: {
    what: "A pay statement PDF from your payroll site. Most let you download each one.",
    get: [
      { label: "ADP", href: "https://my.adp.com" },
      { label: "Paychex Flex", href: "https://myapps.paychex.com" },
      { label: "Gusto", href: "https://app.gusto.com" },
    ],
    steps: [
      "ADP: Myself › Pay › Pay Statements. Paychex Flex: the Check Stubs tile › View All › View PDF. Gusto: Pay › a paystub › Download PDF. Workday: Pay › Payslips.",
      "Upload it here, forward it to a connected mailbox, or drop it into the import folder's paystubs subfolder.",
      "It appears on Needs you to confirm; nothing counts until you do.",
    ],
  },
  ntfy: {
    what: "ntfy sends notifications to the ntfy app on your phone. The free public server works; the topic is a name you make up, long and hard to guess, because anyone who knows it can read it.",
    get: [
      { label: "ntfy for Android", href: "https://play.google.com/store/apps/details?id=io.heckel.ntfy" },
      { label: "ntfy for iPhone", href: "https://apps.apple.com/app/ntfy/id1625396347" },
      { label: "ntfy.sh", href: "https://ntfy.sh" },
    ],
    steps: [
      "Install the ntfy app on your phone.",
      "Make up a topic, for example money-7f3k9q2v, and put it here with the server https://ntfy.sh (or your own ntfy server).",
      "In the ntfy app, tap + and subscribe to the same topic (with your own server, tick Use another server and enter its address). Then Send a test here.",
    ],
    note: "A self-hosted ntfy server reaches iPhones quickly only with upstream-base-url: https://ntfy.sh in its settings.",
  },
  gotify: {
    what: "Gotify is a notification server you run yourself, with an app for Android. The app here needs its address and an application token.",
    get: [{ label: "Gotify install guide", href: "https://gotify.net/docs/install" }],
    steps: [
      "In Gotify's web page, open Apps and create an application named FinancialAuthority.",
      "Copy its token into the token box here, and Gotify's address into the server box.",
    ],
  },
  phone_install: {
    what: "On iPhone and iPad, notifications from a web app need it on the Home Screen (iOS 16.4 or later).",
    get: [{ label: "Apple: open as a web app", href: "https://support.apple.com/guide/iphone/open-as-web-app-iphea86e5236/ios" }],
    steps: [
      "Open the app's address in Safari.",
      "Tap the … menu, then Share, scroll to Add to Home Screen, turn on Open as Web App, then Add.",
      "Open it from the Home Screen and turn notifications on from Settings › Notifications in the app.",
    ],
  },
  claude_connect: {
    what: "Claude can read your plan through the app's own sign-in. The address to give it is {url}.",
    get: [
      { label: "claude.ai › Customize › Connectors", href: "https://claude.ai/customize/connectors" },
      { label: "Claude: custom connectors", href: "https://claude.com/docs/connectors/custom/add-unlisted" },
      { label: "Claude Code: MCP", href: "https://code.claude.com/docs/en/mcp" },
    ],
    steps: [
      "claude.ai or the Claude desktop app: Customize › Connectors › + Add › Add custom connector; paste {url} and Add; this app's sign-in page opens once. A free Claude account allows one custom connector.",
      "Claude Code, in a terminal: claude mcp add --transport http financialauthority {url}, then /mcp inside Claude Code to sign in.",
      "Claude on the web and the phone reach the app only at an https address the internet can reach (Setting up › Reach it from anywhere).",
    ],
  },
  calendar: {
    what: "Your calendar app subscribes to the address once and refreshes it by itself.",
    get: [
      { label: "Google Calendar › From URL", href: "https://calendar.google.com/calendar/r/settings/addbyurl" },
      { label: "Apple: subscribe to calendars", href: "https://support.apple.com/en-us/102301" },
      { label: "Outlook: subscribe from web", href: "https://support.microsoft.com/en-us/outlook/import-or-subscribe-to-a-calendar-in-outlook-com-or-outlook-on-the-web" },
    ],
    steps: [
      "Google Calendar, on a computer: Other calendars › + › From URL, paste the address, Add calendar.",
      "iPhone: the Calendar app › Calendars › Add Calendar › Add Subscription Calendar, paste it. Mac: Calendar › File › New Calendar Subscription.",
      "Outlook.com: Add calendar › Subscribe from web, paste it. Outlook refreshes about every three hours.",
    ],
  },
  direct_deposit: {
    what: "Payroll needs the new account's routing and account numbers. Credit unions and banks put them, often with a ready-made direct deposit form, in online banking under the account's details.",
    get: [],
    steps: [
      "In the new bank's online banking, open the checking account's details, or search its help for direct deposit form.",
      "Give the form, or the two numbers, to payroll (often in the payroll site under Pay › Direct deposit).",
      "Ask whether payroll can split the paycheck if the plan here suggests one.",
    ],
  },
  tailscale: {
    what: "Tailscale makes a private network of your own devices, with https addresses and no ports opened to the internet.",
    get: [{ label: "Tailscale download", href: "https://tailscale.com/download" }],
  },
} satisfies Record<string, WhereEntry>;

export type WhereId = keyof typeof WHERE;
