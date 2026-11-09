.PHONY: test clean-venv

# CPU tests only: no Studio, no network calls beyond installing dev deps, no secrets.
test:
	./scripts/test.sh

# Prints the command instead of deleting anything.
clean-venv:
	@echo "To recreate the test venv: rm -rf $${VENV:-.venv} && make test"
