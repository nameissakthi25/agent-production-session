# Everything you need, in the order you need it. uv does the work.
.PHONY: help install hub phoenix run test lint notebook clean

help:
	@echo "make install    lock-exact envs for the chatbot and the notebook"
	@echo "make phoenix    trace viewer on :6006 (Docker)"
	@echo "make run        the chatbot on :8001"
	@echo "make notebook   JupyterLab, with the serving kernel registered"
	@echo "make test       26 tests, no model, no GPU"
	@echo "make lint       ruff"
	@echo "make hub        optional Guardrails Hub validators (needs egress)"

# Two projects, two locks, on purpose: chainlit needs mcp<2.0.0 and the Phoenix
# server needs mcp>=2.0.0, so one lockfile covering both cannot exist.
install:
	cd chatbot && uv sync --frozen --group dev
	uv sync --frozen
	@echo
	@echo "copy chatbot/.env.example to chatbot/.env and edit it."

# The notebook environment wants a GPU; `uv sync` here pulls vLLM.
notebook:
	uv run python -m ipykernel install --user --name serving --display-name "serving"
	uv run jupyter lab

phoenix:
	cd chatbot && docker compose up -d phoenix

run:
	cd chatbot && uv run chainlit run app.py --port 8001 -w

test:
	cd chatbot && uv run pytest tests/ -q

lint:
	cd chatbot && uv run ruff check . && uv run ruff format --check .

# Optional. Needs network access to hub.api.guardrailsai.com, which is exactly
# what the default backend avoids depending on.
hub:
	cd chatbot && uv run guardrails hub install hub://guardrails/detect_pii
	@echo "then set GUARD_VALIDATOR=hub in chatbot/.env"

clean:
	rm -rf .venv chatbot/.venv chatbot/.ruff_cache chatbot/.pytest_cache
