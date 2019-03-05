SHELL = /bin/bash

VENV_PATH = .venv

.venv:
	virtualenv $(VENV_PATH)
	$(VENV_PATH)/bin/pip install tox

dist: .venv
	rm -rf dist build
	$(VENV_PATH)/bin/python setup.py sdist bdist_wheel

.PHONY: dist

format: .venv
	$(VENV_PATH)/bin/tox -e linters --notest
	.tox/linters/bin/black .
.PHONY: format

test: .venv
	@$(VENV_PATH)/bin/tox -e py2.7,py3.7
.PHONY: test

test-all: .venv
	@TOXPATH=$(VENV_PATH)/bin/tox sh ./scripts/runtox.sh
.PHONY: test-all

check: lint
.PHONY: check

lint: .venv
	@set -e && $(VENV_PATH)/bin/tox -e linters || ( \
		echo "================================"; \
		echo "Bad formatting? Run: make format"; \
		echo "================================"; \
		false)

.PHONY: lint

apidocs: .venv
	@$(VENV_PATH)/bin/pip install --editable .
	@$(VENV_PATH)/bin/pip install pdoc==0.3.2 pygments
	@$(VENV_PATH)/bin/pdoc --overwrite --html --html-dir build/apidocs sentry_sdk
.PHONY: apidocs

install-zeus-cli:
	npm install -g @zeus-ci/cli
.PHONY: install-zeus-cli

travis-upload-docs: apidocs install-zeus-cli
	cd build/apidocs && zip -r gh-pages ./sentry_sdk
	zeus upload -t "application/zip+docs" build/apidocs/gh-pages.zip \
		|| [[ ! "$(TRAVIS_BRANCH)" =~ ^release/ ]]
.PHONY: travis-upload-docs

travis-upload-dist: dist install-zeus-cli
	zeus upload -t "application/zip+wheel" dist/* \
		|| [[ ! "$(TRAVIS_BRANCH)" =~ ^release/ ]]
.PHONY: travis-upload-dist
