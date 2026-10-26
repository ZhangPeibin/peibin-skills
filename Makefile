.PHONY: check install-dev

check:
	python3 -I scripts/check.py

install-dev:
	python3 -m pip install -r requirements-dev.txt
