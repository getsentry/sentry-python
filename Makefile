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

tox-test:
	@sh ./scripts/runtox.sh
.PHONY: tox-test
