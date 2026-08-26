PYTHON ?= python3
PERSONAL_TEST_DIRS := $(shell find skills/personal -type d -name tests | sort)

.PHONY: check install-dev test validate

check: validate test

install-dev:
	$(PYTHON) -m pip install -r requirements-dev.txt

validate:
	$(PYTHON) scripts/validate_repo.py

test:
	@set -e; \
	for test_dir in $(PERSONAL_TEST_DIRS); do \
		echo "==> $$test_dir"; \
		PYTHONDONTWRITEBYTECODE=1 $(PYTHON) -m unittest discover -s "$$test_dir"; \
	done
