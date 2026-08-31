override SHELL := /bin/sh
override .SHELLFLAGS := -eu -c
override PERSONAL_TEST_DIRS := $(shell /usr/bin/find skills/personal -type d -name tests | /usr/bin/sort)
override UNITTEST_RUNNER := skills/personal/code-review/scripts/run_unittest.py
override RELEASE_INPUT_DIRS := skills/personal/code-review/scripts $(PERSONAL_TEST_DIRS)
override OPTIONAL_RELEASE_INPUT_DIRS := skills/personal/code-review/tests
override CHECK_LAUNCHED_ORIGIN := $(origin CODE_REVIEW_CHECK_LAUNCHED)
override CHECK_PYTHON_ORIGIN := $(origin CODE_REVIEW_CHECK_PYTHON)
override CHECK_REQUESTED := $(if $(MAKECMDGOALS),$(filter check,$(MAKECMDGOALS)),check)
override MAKE_SHORT_FLAGS := $(firstword $(MAKEFLAGS))

ifneq ($(CHECK_REQUESTED),)
ifneq ($(filter-out --% -%,$(MAKE_SHORT_FLAGS)),)
ifneq ($(findstring i,$(MAKE_SHORT_FLAGS))$(findstring n,$(MAKE_SHORT_FLAGS))$(findstring t,$(MAKE_SHORT_FLAGS)),)
$(error make check rejects execution-altering MAKEFLAGS (-i, -n, and -t))
endif
endif
endif

.PHONY: check check-environment check-interpreter install-dev release-gate test validate

check: check-environment check-interpreter validate test release-gate

check-interpreter release-gate test validate: check-environment

check-environment:
	@test -f Makefile && test ! -L Makefile || { echo "Makefile must be a nofollow regular release input" >&2; exit 1; }
	@for release_dir in $(RELEASE_INPUT_DIRS); do \
		test -d "$$release_dir" && test ! -L "$$release_dir" || { echo "$$release_dir must be a nofollow release-input directory" >&2; exit 1; }; \
	done
	@for release_dir in $(OPTIONAL_RELEASE_INPUT_DIRS); do \
		if test -e "$$release_dir" || test -L "$$release_dir"; then \
			test -d "$$release_dir" && test ! -L "$$release_dir" || { echo "$$release_dir must be a nofollow release-input directory" >&2; exit 1; }; \
		fi; \
	done
	@if /usr/bin/find $(RELEASE_INPUT_DIRS) \( -type l -o \( ! -type f ! -type d \) \) -print -quit | /usr/bin/grep -q .; then \
		echo "release inputs must be nofollow regular files or directories" >&2; exit 1; \
	fi
	@test "$(CHECK_LAUNCHED_ORIGIN)" = "environment" && test "$(CODE_REVIEW_CHECK_LAUNCHED)" = "1" || { echo "checks require the documented release-check launcher" >&2; exit 1; }
	@test "$(CHECK_PYTHON_ORIGIN)" = "environment" || { echo "checks require launcher-bound Python" >&2; exit 1; }
	@test -n "$$CODE_REVIEW_CHECK_PYTHON" || { echo "launcher-bound Python is unavailable" >&2; exit 1; }

check-interpreter:
	@unset PYTHONHOME PYTHONPATH; \
	PATH=/usr/bin:/bin:/usr/sbin:/sbin; export PATH; \
	test "$$("$$CODE_REVIEW_CHECK_PYTHON" -I -c 'import os, stat, sys; target = os.path.realpath(sys.executable); info = os.stat(target); valid = stat.S_ISREG(info.st_mode) and os.access(target, os.X_OK) and not (stat.S_IMODE(info.st_mode) & 0o022) and (not hasattr(os, "geteuid") or info.st_uid in {0, os.geteuid()}); print("code-review-check-python-v2" if valid else "")')" = "code-review-check-python-v2"

install-dev:
	python3 -m pip install -r requirements-dev.txt

validate:
	@unset PYTHONHOME PYTHONPATH; \
	PATH=/usr/bin:/bin:/usr/sbin:/sbin; export PATH; \
	if output="$$("$$CODE_REVIEW_CHECK_PYTHON" scripts/validate_repo.py)"; then :; else status=$$?; printf '%s\n' "$$output"; exit $$status; fi; \
	printf '%s\n' "$$output"; \
	case "$$output" in *Validated*plugin\ skills*personal\ skills*Markdown\ files.*) ;; *) echo "validation phase marker missing" >&2; exit 1;; esac

test:
	@set -e; \
	unset PYTHONHOME PYTHONPATH; \
	PATH=/usr/bin:/bin:/usr/sbin:/sbin; export PATH; \
	for test_dir in $(PERSONAL_TEST_DIRS); do \
		echo "==> $$test_dir"; \
		if output="$$(PYTHONDONTWRITEBYTECODE=1 "$$CODE_REVIEW_CHECK_PYTHON" -I "$(UNITTEST_RUNNER)" discover "$$test_dir" 2>&1)"; then :; else status=$$?; printf '%s\n' "$$output"; exit $$status; fi; \
		printf '%s\n' "$$output"; \
		case "$$output" in *CODE_REVIEW_UNITTEST_RUNNER_OK*) ;; *) echo "test phase marker missing" >&2; exit 1;; esac; \
	done

release-gate:
	@unset PYTHONHOME PYTHONPATH; \
	PATH=/usr/bin:/bin:/usr/sbin:/sbin; export PATH; \
	if grade_output="$$(PYTHONDONTWRITEBYTECODE=1 "$$CODE_REVIEW_CHECK_PYTHON" skills/personal/code-review/scripts/review_workflow.py grade)"; then :; else exit $$?; fi; \
	case "$$grade_output" in *\"probe_observations\"*\"release_ready\"*true*) ;; *) echo "grade phase marker missing" >&2; exit 1;; esac; \
	if gate_output="$$(printf '%s\n' "$$grade_output" | PYTHONDONTWRITEBYTECODE=1 "$$CODE_REVIEW_CHECK_PYTHON" skills/personal/code-review/scripts/review_workflow.py gate)"; then :; else exit $$?; fi; \
	printf '%s\n' "$$gate_output"; \
	case "$$gate_output" in *\"status\"*\"release_ready\"*) ;; *) echo "gate phase marker missing" >&2; exit 1;; esac
