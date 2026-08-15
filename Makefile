# HonchoBench — one-command, repeatable containerized bench.
#
#   make setup   # copy .env.example -> .env (if missing), build image, print next step
#   make bench   # run the default bench (LiquidAI/LFM2_5-2_6B on the Plexus gateway)
#
# Custom models:  make bench MODELS=gpt-4o,gpt-4o-mini
# Smoke test:     make bench MODELS=LiquidAI/LFM2_5-2_6B LIMIT=1

COMPOSE      ?= docker compose
COMPOSE_FILE ?= docker-compose.yml
IMG          ?= honchobench:latest

.PHONY: help setup build bench bench-custom shell help-in-container down-clean

help:
	@echo 'HonchoBench targets:'
	@echo '  make setup   create .env (if missing) + build the image and print next command'
	@echo '  make bench   run the default bench; reports land in ./reports'
	@echo '  make bench MODELS=<csv>   run with a custom model list'
	@echo '  make shell   open a shell in the running app container'
	@echo '  make build   (re)build the image only'

setup:
	@if [ ! -f .env ]; then cp .env.example .env; echo 'Created .env from .env.example — edit it to set PLEXUS_API_KEY.'; \
	else echo '.env already exists — leaving untouched.'; fi
	@$(MAKE) build
	@echo
	@echo 'Next: edit .env to set PLEXUS_API_KEY, then run:'
	@echo '  make bench'
	@echo 'or for a custom model list:'
	@echo '  make bench MODELS=gpt-4o,gpt-4o-mini'
	@echo 'Reports are written to ./reports (.md + .csv per role).'

# Build with host networking: some Docker daemons (incl. this box) cannot reach
# pypi.org from the default bridge network. Harmless on normal daemons.
build:
	$(COMPOSE) -f $(COMPOSE_FILE) build --network host honchobench

# Default bench: no extra compose args -> ENTRYPOINT runs with the default
# model list (HONCHOBENCH_MODELS from .env, else LiquidAI/LFM2_5-2_6B).
bench:
	$(COMPOSE) -f $(COMPOSE_FILE) run --rm honchobench \
		$(if $(MODELS),--models $(MODELS),) \
		$(if $(LIMIT),--limit $(LIMIT),)

shell:
	$(COMPOSE) -f $(COMPOSE_FILE) run --rm --entrypoint /bin/sh honchobench

help-in-container:
	docker run --rm --entrypoint honchobench $(IMG) --help
