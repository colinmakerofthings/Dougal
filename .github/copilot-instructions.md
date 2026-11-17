# Dougal coding standards

- Keep the service asynchronous end to end; use `httpx.AsyncClient` and the official async OpenAI and Telegram APIs.
- Keep Home Assistant operations in `src/tools/home_assistant.py` and expose only explicit tools through the registry.
- Treat Telegram user IDs as untrusted input. Never process a message unless its numeric user ID is on the configured allowlist.
- Read secrets and runtime configuration from environment variables. Never log tokens, message contents, or full provider errors.
- Keep dependencies lightweight and compatible with Python 3.12 on ARM64 and x86_64.
- Add focused tests for behavior changes; mock external HTTP/API calls.
