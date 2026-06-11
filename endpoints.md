# Stagify API & server routes

This document describes HTTP endpoints registered in `server.js`. Static files are also served from `public/` (not every path is listed here). Default port: **`process.env.PORT` or `3000`**.

## Authentication helpers (used by several routes)

- **Bearer session:** `Authorization: Bearer <token>` (JWT/session token from `authToken` in login/register responses).
- **Token in body:** `authToken` in JSON or multipart field (e.g. staging).
- **Token in query:** `?authToken=...` (used by some browser flows and `/getpro`).

**`getAuthUserFromRequest`:** loads the user from a valid session token (header, body, or query).

**`requireProAccount`:** requires a signed-in user with `plan === 'pro'`; otherwise `401` (`AUTH_REQUIRED`) or `403` (`PRO_REQUIRED`).

---

## Public pages & SEO (no API key)

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/` | Serves `public/index.html`. |
| `GET` | `/robots.txt` | Serves `public/robots.txt`. |
| `GET` | `/sitemap.xml` | Serves `public/sitemap.xml`. |

Other `.html` and assets are served by **`express.static('public')`** (e.g. `/stagify-plus.html`, `/ai-designer.html`).

---

## Stagify+ pass link

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/getpro` | **Query:** `key` — must match the secret in `propass.txt` (or `STAGIFY_PRO_PASS_KEY`) using a constant-time hash compare. **If not configured:** `503` plain text. **If wrong key:** `404` plain text. **If no valid session:** HTML page that can retry with `authToken` from `localStorage`. **If valid session:** grants Stagify+ to that user and returns success HTML. Optional: `authToken` in query for the same client used elsewhere. |

---

## Billing (Stripe)

| Method | Path | Description |
|--------|------|-------------|
| `POST` | `/api/billing/stripe-webhook` | **Body:** raw JSON (must **not** go through `express.json()`; uses `express.raw`). **Header:** `stripe-signature` for verification. If Stripe is not configured: `503`. Forwards to internal `handleStripeEvent` (subscription lifecycle, etc.). Responds `{ received: true }` on success. |
| `POST` | `/api/billing/customer-portal` | **Auth:** signed-in user with a Stripe customer id. **Body:** JSON (can be empty). Returns `{ url }` to Stripe Billing Portal, or `503` if Stripe off, `401` if not signed in, `400` if no `stripeCustomerId` on the user. |

---

## Auth

| Method | Path | Description |
|--------|------|-------------|
| `POST` | `/api/auth/register` | **Body:** `{ email, password }`. Creates a free-tier account. Returns `{ success, token, user }` or `400` with `error`. |
| `POST` | `/api/auth/login` | **Body:** `{ email, password }`. Returns `{ success, token, user }` or `401`. |
| `GET` | `/api/auth/config` | Public. Returns `{ googleClientId: string | null }` for Google Sign-In. |
| `POST` | `/api/auth/google` | **Body:** `{ credential }` (Google ID token). Returns `{ success, token, user }` or `4xx/503` if not configured or invalid. |
| `GET` | `/api/auth/me` | **Auth:** valid session. Returns `{ user }` (public user shape, including `dailyGenerationsUsed` / `dailyGenerationLimit` for free tier). `401` if not signed in (`AUTH_REQUIRED`). |
| `POST` | `/api/auth/logout` | **Body (optional):** `authToken`. **Or** `Authorization: Bearer` token. Invalidates the session. Returns `{ success: true }`. |
| `POST` | `/api/auth/forgot-password` | **Body:** `{ email }`. If account exists and email is configured, sends reset link; various `{ ok, emailSent, message }` or `503/502` if email not configured. |
| `POST` | `/api/auth/reset-password` | **Body:** `{ token, password }`. Completes reset. Returns `{ ok: true }` or `400`. |

---

## Virtual staging (image)

| Method | Path | Description |
|--------|------|-------------|
| `POST` | `/api/process-image` | **Multipart** staging upload (`stagingProcessUpload`). **File:** at least `image` (see multer field names in server). **Typical body fields** (strings): `roomType`, `furnitureStyle`, `additionalPrompt`, `removeFurniture`, `userRole`, `userReferralSource`, `userEmail`, and for pro: `model`, `variationCount`, `furnitureImage` (repeat), `authToken`. **Rules:** (1) Signed-in user: enforces per-account daily free limit for non-pro; on success, may return `user` with updated usage. (2) **Not signed in:** only **mobile** user-agents can use a per-**IP** daily cap (no session); desktop browsers get `401` with `AUTH_REQUIRED`. **Errors:** `429` with `DAILY_LIMIT` + `dailyGenerationsUsed` / `dailyGenerationLimit`, `500` if AI not configured, etc. **Success:** `image` or `images` plus `success: true` and often `user` after consumption. |
| `POST` | `/api/stage-by-endpoint-key` | **Server integration staging** — same multipart shape as `/api/process-image`, but **no user session**. **Auth:** `LOGS_ACCESS_KEY` from `endpointkey.txt` or `process.env.endpoint_key`, passed as **`?key=`** on the request URL **or** header **`X-Stagify-Endpoint-Key`**. Same secret as log CSV exports and `/api/send-email` — **highly sensitive**; treat like a root credential. **Behavior:** Staging runs with **Stagify+-level options** (`model` `gpt-4o-mini` \| `gpt-5-mini`, `variationCount` 1–3, up to three `furnitureImage` files). **Does not** increment per-user or per-IP free-tier daily counters. **Success:** same JSON as process-image (`image` / `images`, `user` is `null`). **`403`** if key missing/wrong, **`500`** if key not configured on server. |

**`POST /api/stage-by-endpoint-key` field reference (multipart):**

| Field | Notes |
|--------|--------|
| `image` | **Required.** One JPEG/JPG/PNG/WebP, same as public staging. |
| `roomType`, `furnitureStyle`, `additionalPrompt`, `removeFurniture` | Same defaults and meaning as `/api/process-image`. |
| `userRole`, `userReferralSource`, `userEmail` | Optional analytics strings (default `unknown`). |
| `model` | `gpt-4o-mini` or `gpt-5-mini` (invalid values fall back to `gpt-4o-mini`). |
| `variationCount` | String or number `1`–`3`. |
| `furnitureImage` | Up to **3** files (same as Stagify+). |

Example URL: `POST https://your-host/api/stage-by-endpoint-key?key=YOUR_SECRET` with `multipart/form-data` body (do not put the secret in client-side browser code).

---

## Contact, email, and public counters

| Method | Path | Description |
|--------|------|-------------|
| `POST` | `/api/log-contact` | **Body:** JSON with `userRole`, `referralSource`, `email`, `userAgent` (and similar). Appends a row to `contact_logs.csv` and bumps an in-memory contact counter. Returns `{ success: true }`. |
| `POST` | `/api/send-email` | **Protected by server access key:** query `?key=` or `body.key` must match `endpointkey.txt` or `process.env.endpoint_key` (`LOGS_ACCESS_KEY`). **Body:** `to`, `subject`, `text` (Resend). Returns `403` if key wrong, `500` if no Resend, etc. |
| `GET` | `/api/health` | **Public.** `{ status, timestamp, aiConfigured: boolean }` (and similar). |
| `GET` | `/api/prompt-count` | Returns `{ promptCount }` (server-side counter, used for hero “Rooms staged” type stats). |
| `GET` | `/api/contact-count` | Returns `{ contactCount }` (in-memory + startup initialization). |

---

## PDF (AI Designer proxy)

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/api/pdf-health` | Proxies to external `PDF_PROCESSING_SERVER` `GET /health`, returns that JSON or `500` on failure. |
| `POST` | `/api/process-pdf` | **Auth:** **`requireProAccount`** (Stagify+). **Body:** `multipart/form-data` with file field `pdf`. **Query:** e.g. `skip`, `concurrency`, `dpi`, `continue`, `merge`, `filename`. Proxies to the external PDF pipeline and streams the result (often a PDF download). |

---

## AI Designer (chat, welcome, files)

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/api/welcome-message` | **Auth:** **`requireProAccount`**. **Query (optional):** `userId`. Returns `{ message, isReturning }` for the AI Designer welcome, using optional stored “memories”. |
| `POST` | `/api/chat` | **Auth:** **`requireProAccount`**. **Body:** JSON with `messages` (OpenAI-style array), optional `model`, `messageTag`. Long-running: staging/CAD/generation inside JSON tool contract. Respects user message limits (e.g. 20 user messages) and may return `contextLimitReached`. |
| `POST` | `/api/chat-upload` | **Auth:** **`requireProAccount`**. **Multipart:** up to 10 files in field `files`, plus form fields (e.g. `conversationHistory`, `messageTag`). AI Designer flow with file attachments. Large implementation in `server.js`. |

---

## Bug reports and mask edit

| Method | Path | Description |
|--------|------|-------------|
| `POST` | `/api/bug-report` | **Body:** `description` (required), and optional `steps`, `email`, `userId`, `userAgent`, `url`, `timestamp`, `conversationHistory`. Appends to `bug_reports.csv`. |
| `POST` | `/api/mask-edit` | **Auth:** **`requireProAccount`**. **Body (JSON):** `image` and `mask` as data URLs, `prompt`, optional `model`. Uses Gemini for region edit; returns `{ success, editedImage }` (data URL) or `500` errors. |

---

## Log download / admin (query key)

These routes use **`protectLogs`**: a shared secret `LOGS_ACCESS_KEY` from `endpointkey.txt` or `process.env.endpoint_key`. **Query:** `?key=<LOGS_ACCESS_KEY>`. If missing/invalid: **`403`**. The same secret authenticates **`POST /api/stage-by-endpoint-key`** (query `key` or header `X-Stagify-Endpoint-Key`) and **`POST /api/send-email`**.

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/authstore` | Download `auth-store.json` (users, session tokens, password hashes, Stripe ids, etc.). **Highly sensitive** — same `?key=` as other admin exports. `404` if the file has not been created yet. On disk: `data/auth-store.json` (or `/data/auth-store.json` on Render). |
| `GET` | `/promptlogs` | Download `prompt_logs.csv` (or `404` if missing). |
| `GET` | `/contactlogs` | Download `contact_logs.csv`. |
| `GET` | `/chatlogs` | Download `chat_logs.csv`. |
| `GET` | `/bugreports` | Download `bug_reports.csv`. |
| `GET` | `/masklogs` | Download `mask_logs.csv`. |
| `GET` | `/memories` | Download AI Designer `memories` JSON. |
| `GET` | `/resetmemories` | **Clears** the memories file (all users). Returns JSON success. |

`POST` `/api/send-email` uses the **same** `LOGS_ACCESS_KEY` (see above), not only for logs.

---

## Notes

- **CORS** is enabled globally.
- **JSON body limit** is very large (e.g. 50mb) for chat/history; oversized bodies get `400` / `413` with JSON error where configured.
- **Trust proxy** can be toggled with `TRUST_PROXY` (for real client IPs behind Render/nginx).

If you add a route, append it to this file so operators can find auth and query requirements quickly.
