# Everything you need, in the order you need it.
.PHONY: help venv install hub phoenix run test lint clean

help:
	@echo "make install   create .venv and install the app"
	@echo "make hub       add the Guardrails AI Hub validators (optional, downloads)"
	@echo "make phoenix   run the trace viewer on :6006"
	@echo "make run       run the chatbot on :8001"
	@echo "make test      fast tests, no model needed"
	@echo "make lint      ruff"

CHATBOT := chatbot
PY := $(CHATBOT)/.venv/bin/python
PIP := $(CHATBOT)/.venv/bin/pip

install:
	python3 -m venv $(CHATBOT)/.venv
	$(PIP) install -q -U pip
	$(PIP) install -q -r $(CHATBOT)/requirements-dev.txt
	@echo "installed. copy chatbot/.env.example to chatbot/.env and edit it."

hub:
	cd $(CHATBOT) && .venv/bin/guardrails hub install hub://guardrails/detect_pii
	cd $(CHATBOT) && .venv/bin/guardrails hub install hub://guardrails/nsfw_text
	@echo "now set GUARD_VALIDATOR=hub in chatbot/.env"

phoenix:
	cd $(CHATBOT) && .venv/bin/phoenix serve

run:
	cd $(CHATBOT) && .venv/bin/chainlit run app.py --port 8001 -w

test:
	cd $(CHATBOT) && .venv/bin/python -m pytest tests/ -q

lint:
	cd $(CHATBOT) && .venv/bin/ruff check . && .venv/bin/ruff format --check .

clean:
	rm -rf $(CHATBOT)/.venv $(CHATBOT)/.ruff_cache $(CHATBOT)/.pytest_cache
