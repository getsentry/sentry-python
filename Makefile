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
