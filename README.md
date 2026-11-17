# Dougal

Dougal is a lightweight, self-hosted Telegram assistant for Home Assistant. It
uses an OpenAI-compatible chat-completions API for natural language and tool
calling, while Home Assistant access stays on your own network. Version 0.1
supports reading one entity's state and calling a Home Assistant service.

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

If the AI provider reports exhausted API credits, Dougal replies with a
top-up reminder. Temporary rate limits instead receive a request to try
again later.

## Run with Docker Compose

```sh
docker compose up --build -d
```

Compose builds a Python 3.12 image for the host architecture, including
Raspberry Pi 4 ARM64 and x86_64. Polling means no inbound Telegram port
forwarding is needed. The optional port 8000 exposes only the health and
metrics HTTP endpoints; restrict it to your trusted network.

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

- `http://localhost:8000/health` reports service health.
- `http://localhost:8000/metrics` exposes basic Prometheus text-format request
  and uptime metrics.

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
