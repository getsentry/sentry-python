SHELL = /bin/bash

VENV_PATH = .venv

help:
	@echo "Thanks for your interest in the Sentry Python SDK!"
	@echo
	@echo "make lint: Run linters"
	@echo "make test: Run basic tests (not testing most integrations)"
	@echo "make test-all: Run ALL tests (slow, closest to CI)"
	@echo "make format: Run code formatters (destructive)"
	@echo
	@echo "Also make sure to read ./CONTRIBUTING.md"
	@false

.venv:
	virtualenv -ppython3 $(VENV_PATH)
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

check: lint test
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
	@$(VENV_PATH)/bin/pip install -U -r ./docs-requirements.txt
	@$(VENV_PATH)/bin/sphinx-build -W -b html docs/ docs/_build
.PHONY: apidocs

apidocs-hotfix: apidocs
	@$(VENV_PATH)/bin/pip install ghp-import
	@$(VENV_PATH)/bin/ghp-import -pf docs/_build
.PHONY: apidocs-hotfix

install-zeus-cli:
	npm install -g @zeus-ci/cli
.PHONY: install-zeus-cli

travis-upload-docs: apidocs install-zeus-cli
	cd docs/_build && zip -r gh-pages ./
	zeus upload -t "application/zip+docs" docs/_build/gh-pages.zip \
		|| [[ ! "$(TRAVIS_BRANCH)" =~ ^release/ ]]
.PHONY: travis-upload-docs

travis-upload-dist: dist install-zeus-cli
	zeus upload -t "application/zip+wheel" dist/* \
		|| [[ ! "$(TRAVIS_BRANCH)" =~ ^release/ ]]
.PHONY: travis-upload-dist

awslambda-layer-build-pre: dist
	@$(VENV_PATH)/bin/pip install .
.PHONY: awslambda-layer-build-pre

awslambda-layer-build-py27: awslambda-layer-build-pre
	$(VENV_PATH)/bin/python scripts/build-awslambda-layer.py --python 2.7
.PHONY: awslambda-layer-build-py27

awslambda-layer-build-py36: awslambda-layer-build-pre
	$(VENV_PATH)/bin/python scripts/build-awslambda-layer.py --python 3.6
.PHONY: awslambda-layer-build-py36

awslambda-layer-build-py37: awslambda-layer-build-pre
	$(VENV_PATH)/bin/python scripts/build-awslambda-layer.py --python 3.7
.PHONY: awslambda-layer-build-py37

awslambda-layer-build-py38: awslambda-layer-build-pre
	$(VENV_PATH)/bin/python scripts/build-awslambda-layer.py --python 3.8
.PHONY: awslambda-layer-build-py38

awslambda-layer-build: awslambda-layer-build-py27 awslambda-layer-build-py36 awslambda-layer-build-py37 awslambda-layer-build-py38
.PHONY: awslambda-layer-build

awslambda-layer-publish-pre:
	@$(VENV_PATH)/bin/pip install boto3
.PHONY: awslambda-layer-pre

awslambda-layer-publish-py27: awslambda-layer-build-py27 awslambda-layer-publish-pre
	$(VENV_PATH)/bin/python scripts/publish-awslambda-layer.py --python 2.7
.PHONY: awslambda-layer-publish-py27

awslambda-layer-publish-py36: awslambda-layer-build-py36 awslambda-layer-publish-pre
	$(VENV_PATH)/bin/python scripts/publish-awslambda-layer.py --python 3.6
.PHONY: awslambda-layer-publish-py36

awslambda-layer-publish-py37: awslambda-layer-build-py37 awslambda-layer-publish-pre
	$(VENV_PATH)/bin/python scripts/publish-awslambda-layer.py --python 3.7
.PHONY: awslambda-layer-publish-py37

awslambda-layer-publish-py38: awslambda-layer-build-py38 awslambda-layer-publish-pre
	$(VENV_PATH)/bin/python scripts/publish-awslambda-layer.py --python 3.8
.PHONY: awslambda-layer-publish-py38

awslambda-layer-publish: awslambda-layer-publish-py27 awslambda-layer-publish-py36 awslambda-layer-publish-py37 awslambda-layer-publish-py38
.PHONY: awslambda-layer-publish
