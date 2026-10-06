.PHONY: install run test lint docker clean
install:
	pip install -r requirements.txt
run:
	python -m app.main
test:
	python -m pytest -q
docker:
	docker build -t noor-islam-bot .
clean:
	rm -rf data/noor.db __pycache__ .pytest_cache
