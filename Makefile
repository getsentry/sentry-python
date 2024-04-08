SHELL = /bin/bash

VENV_PATH = .venv

help:
	@echo "Thanks for your interest in the Sentry Python SDK!"
	@echo
	@echo "make lint: Run linters"
	@echo "make test: Run basic tests (not testing most integrations)"
	@echo "make test-all: Run ALL tests (slow, closest to CI)"
	@echo "make format: Run code formatters (destructive)"
	@echo "make aws-lambda-layer: Build AWS Lambda layer directory for serverless integration"
	@echo
	@echo "Also make sure to read ./CONTRIBUTING.md"
	@false

.venv:
	pip install uv
	uv venv $(VENV_PATH)
	. $(VENV_PATH)/bin/activate
	uv pip install tox tox-uv

dist: .venv
	rm -rf dist dist-serverless build
	uv pip install wheel setuptools
	python setup.py sdist bdist_wheel

.PHONY: dist

format: .venv
	uv pip install black
	black .

.PHONY: format

test: .venv
	tox -e py3.12
.PHONY: test

test-all: .venv
	@TOXPATH=$(VENV_PATH)/bin/tox sh ./scripts/runtox.sh
.PHONY: test-all

check: lint test
.PHONY: check

lint: .venv
	@set -e && tox -e linters || ( \
		echo "================================"; \
		echo "Bad formatting? Run: make format"; \
		echo "================================"; \
		false)
.PHONY: lint

apidocs: .venv
	uv pip install --editable .
	uv pip install -U -r ./docs-requirements.txt
	rm -rf docs/_build
	sphinx-build -vv -W -b html docs/ docs/_build
.PHONY: apidocs

apidocs-hotfix: apidocs
	uv pip install ghp-import
	ghp-import -pf docs/_build
.PHONY: apidocs-hotfix

aws-lambda-layer: dist
	uv pip install -r aws-lambda-layer-requirements.txt
	python -m scripts.build_aws_lambda_layer
.PHONY: aws-lambda-layer
