.PHONY: up down rebuild logs clean

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
