.PHONY: up down rebuild logs clean doctor

# Diagnose the environment (Docker, daemon, Compose v2, host ports) without
# changing anything. Run this first when a setup fails.
doctor:
	./setup.sh --check

up:
	docker compose up --build -d

down:
	docker compose down

rebuild:
	docker compose up --build -d --force-recreate

logs:
	docker compose logs -f

clean:
	docker compose down -v
