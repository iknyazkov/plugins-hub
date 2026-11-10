install:
	@command -v uv >/dev/null 2>&1 || (echo "uv not found, installing..." && python -m pip install uv)
	uv sync

test:
	uv run pytest

lint:
	uv run ruff check .
	uv run mypy .

setup: install

run:
	make lint
	uv run channelsrv

profile:
	make lint
	uv run python -m cProfile -s cumulative -m channelsrv.main
