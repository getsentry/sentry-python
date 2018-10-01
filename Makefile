SHELL = /bin/bash

dist:
	rm -rf dist build
	python setup.py sdist bdist_wheel

.PHONY: dist

.venv:
	@virtualenv .venv

test: .venv
	@pip install -r test-requirements.txt
	@pip install --editable .
	@pytest tests
.PHONY: test

format:
	@black sentry_sdk tests
.PHONY: format

tox-test:
	@sh ./scripts/runtox.sh
.PHONY: tox-test

lint:
	@tox -e linters
.PHONY: lint

apidocs:
	@pip install pdoc pygments
	@pdoc --overwrite --html --html-dir build/apidocs sentry_sdk
.PHONY: apidocs

install-zeus-cli:
	npm install -g @zeus-ci/cli
.PHONY: install-zeus-cli

travis-upload-docs:
	@pip install --editable .
	$(MAKE) apidocs
	cd build/apidocs && zip -r gh-pages ./sentry_sdk
	$(MAKE) install-zeus-cli
	zeus upload -t "application/zip+docs" build/apidocs/gh-pages.zip \
		|| [[ ! "$(TRAVIS_BRANCH)" =~ ^release/ ]]
.PHONY: travis-upload-docs

travis-upload-dist: dist
	$(MAKE) install-zeus-cli
	zeus upload -t "application/zip+wheel" dist/* \
		|| [[ ! "$(TRAVIS_BRANCH)" =~ ^release/ ]]
.PHONY: travis-upload-dist
