# Dougal

Dougal is a lightweight, self-hosted Telegram assistant for Home Assistant. It
uses an OpenAI-compatible chat-completions API for natural language and tool
calling, while Home Assistant access stays on your own network. Version 0.1
supports checking Home Assistant API availability, discovering entities by
domain, reading one entity's state, and calling a Home Assistant service.

## Requirements

- Docker with Compose, or Python 3.12+
- A Telegram bot token from [@BotFather](https://t.me/BotFather)
- A Home Assistant long-lived access token
- An OpenAI API key or a compatible provider endpoint/model

## Configure

Copy `.env.example` to `.env` and set all required values:

```dotenv
TELEGRAM_BOT_TOKEN=...
ALLOWED_TELEGRAM_USER_IDS=[123456789]
OPENAI_API_KEY=...
OPENAI_BASE_URL=
OPENAI_MODEL=gpt-5.4-nano
HOME_ASSISTANT_URL=http://homeassistant.local:8123
HOME_ASSISTANT_TOKEN=...
```

Find your numeric Telegram user ID using a trusted ID lookup bot or Telegram
client. The allowlist must not be empty; messages from every other user are
ignored. Keep `.env` private and never commit it. The model name and optional
base URL can be changed to match any provider that supports the OpenAI
Chat Completions tools interface.

The service can read entity states and invoke services permitted by the
configured Home Assistant token. Use a dedicated HA account/token with only
the access you intend Dougal to have.

Ask "Is Home Assistant running?" to check the configured API directly, without
providing an entity ID. Dougal distinguishes successful API access from denied
authentication, an unreachable API, and HTTP errors. This check does not verify
that every connected device or integration is healthy.

Ask "Are any lights on?" to discover the lights visible to Dougal and check
their current states without supplying entity IDs. Discovery returns only the
requested domain's names, IDs, and states to the AI. Routine lighting summaries
omit commentary about unavailable or unknown devices; those states remain
available for direct entity queries and troubleshooting and are never treated
as off. Tool failures distinguish access denied, missing entities or endpoints,
HTTP errors, and connection failures.

### Room-specific queries

Ask "Are the kitchen lights on?" to restrict discovery to the Kitchen Area
configured in Home Assistant. The `list_entities` tool accepts an optional
`area` name, ID, or alias, matched exactly without regard to case. Room matching
does not guess from entity IDs or friendly names.

An entity's explicit Area takes precedence over its device's Area. Entities
without an explicit Area inherit their device's Area; unassigned entities are
excluded from room-specific results. Indicator filtering still applies.
Unknown or ambiguous Areas and failed registry lookups produce an error rather
than falling back to whole-house results. Room lookup uses the same read-only
WebSocket registry connection mechanism as indicator filtering.

Discovery remains domain-specific: `light` queries do not automatically include
`switch` entities, even if a switch controls a lamp. Each query reads current
states; the returned list has no application-imposed item limit. Conversation
memory is unchanged, so follow-up questions still need to specify their subject.

### Indicator lighting

To exclude switch backlights and status LEDs from general light discovery,
apply the `Indicator_Lighting` label to those **entities** in Home Assistant,
then set this optional environment variable in `.env` or your Portainer Stack:

```dotenv
DOUGAL_INDICATOR_LABEL=Indicator_Lighting
```

Recreate the container after changing the setting. The value is the exact,
case-sensitive Home Assistant **label name**, not its internal label ID.
Dougal resolves the name through HA's label registry and checks labels attached
directly to entities. Device and area labels are not inherited. There is no
prefix matching or matching against entity IDs or friendly names.

With the setting enabled, `list_entities` excludes labelled entities from the
`light` domain by default, so general questions such as "Are any lights on?"
do not include them. Explicit questions about indicators or backlights can use
`include_indicators: true` to include all light entities. This is a discovery
filter, not an access restriction: direct state reads and service calls are
unchanged. Other entity domains are unaffected.

Leaving the variable blank or unset disables filtering and preserves discovery
of all lights. Label changes are read on each filtered discovery request.
Dougal uses read-only label/entity registry commands over Home Assistant's
authenticated WebSocket API; the reverse proxy, if any, must allow
`/api/websocket`. This does not require the admin-only REST template endpoint.
If the configured label is missing, registry access fails, or the lookup times
out, Dougal reports a filtering failure rather than silently including indicators.

If the AI provider reports exhausted API credits, Dougal replies with a
top-up reminder. Temporary rate limits instead receive a request to try
again later.

## Run with Docker Compose

```sh
docker compose up --build -d
```

Compose builds a Python 3.12 image for the host architecture, including
Raspberry Pi 4 ARM64 and x86_64. Polling means no inbound Telegram port
forwarding is needed. The optional port 8000 exposes a read-only landing page,
health, and metrics HTTP endpoints; restrict it to your trusted network.

### Deploy from Git with Portainer

In Portainer, create a Stack using **Git repository**, enter this repository's
URL and branch, and set the Compose path to `docker-compose.yml`. In the
Stack's environment variables, provide:

- `TELEGRAM_BOT_TOKEN`
- `ALLOWED_TELEGRAM_USER_IDS` as a JSON array, e.g. `[123456789]`
- `OPENAI_API_KEY`
- `HOME_ASSISTANT_URL`
- `HOME_ASSISTANT_TOKEN`

`OPENAI_BASE_URL` is optional (leave it unset for OpenAI), and
`OPENAI_MODEL` defaults to `gpt-5.4-nano`. Deploy the Stack, then check
`http://<raspberry-pi-address>:8000/health` from your trusted network. Keep
credentials in Portainer, not in the Git repository.
`DOUGAL_INDICATOR_LABEL` is also optional; set it to `Indicator_Lighting` to
enable the entity-label filtering described above.

- `http://localhost:8000/` shows a Dougal overview, service uptime, and links to
  health and metrics, plus the latest 100 completed Telegram message/reply
  exchanges across all allowlisted users, newest first. Refresh for updates.
  History is held only in memory per service instance and cleared on restart;
  it does not change the AI's conversation context or import earlier Telegram
  messages. Each exchange shows the sender's Telegram display name (first
  name and last name, when present) at the time of the exchange and a
  "via Telegram" source badge; no extra
  configuration is needed. Display names do not affect the numeric user-ID
  allowlist. Numeric Telegram IDs are not stored in history or displayed.
  The page does not expose configuration or check external services.
  **There is no login: anyone who can reach the page can read the message
  contents and display names.** Keep it on your trusted network and do not
  expose it publicly.
- `http://localhost:8000/health` reports service health.
- `http://localhost:8000/metrics` exposes basic Prometheus text-format request
  and uptime metrics.
- `http://localhost:8000/api/status` returns the last accepted Telegram message
  receipt time as JSON.

### Status API

The read-only `GET /api/status` endpoint returns HTTP 200 with a JSON body:

```json
{"last_message_received": "2026-11-17T16:40:00+00:00"}
```

The value is an ISO 8601 UTC timestamp recorded when Dougal receives an
allowlisted user's private text message, before waiting for the AI or sending
a reply. Ignored messages and commands do not update it. It is `null` until
the first accepted message after each restart. No message contents, names,
user IDs, or credentials are returned.

Responses include `Cache-Control: no-store`. The endpoint has no authentication;
keep it on your trusted network.

## Run locally

```sh
python -m venv .venv
# Activate the virtual environment for your shell, then:
pip install -r requirements.txt
uvicorn src.main:app --host 0.0.0.0 --port 8000
```

The app loads `.env` from the process working directory. Telegram polling
starts with the web service and stops with it.

## Tests

```sh
pytest
```
