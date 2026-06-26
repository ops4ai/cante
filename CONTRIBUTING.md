# Contributing to Cante

## Getting started

```bash
git clone https://github.com/ops4ai/cante.git
cd cante
cp .env.example .env
make up
make seed
make test
```

## What to contribute

- **Channel adapters**: add Telegram, Instagram, web widget, etc. — implement the `ChannelAdapter` interface in `core/cante/channel.py`
- **LLM adapters**: add new provider types — see `core/cante/llm.py`
- **Skill presets**: share useful bot configurations
- **Docs**: fix errors, add examples, translate UI strings
- **Bug reports**: include reproduction steps and logs

## Code style

- Python 3.12, typed (mypy strict)
- Ruff for linting and formatting
- Tests with pytest + pytest-asyncio
- Follow the rules in `GOTCHAS.md` — they exist for a reason

## Pull requests

1. Open an issue before large changes
2. Write tests for new code
3. Run `make lint && make test` before pushing
4. Keep commits focused and well-described
