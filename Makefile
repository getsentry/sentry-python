SHELL = /bin/bash

VENV_PATH = .venv

help:
	@echo "Thanks for your interest in the Sentry Python SDK!"
	@echo
	@echo "make apidocs: Build the API documentation"
	@echo "make aws-lambda-layer: Build AWS Lambda layer directory for serverless integration"
	@echo
	@echo "Also make sure to read ./CONTRIBUTING.md"
	@echo
	@false

.venv:
	python -m venv $(VENV_PATH)
	$(VENV_PATH)/bin/pip install tox

dist: .venv
	rm -rf dist dist-serverless build
	$(VENV_PATH)/bin/pip install wheel setuptools
	$(VENV_PATH)/bin/python setup.py sdist bdist_wheel
.PHONY: dist

apidocs: .venv
	@$(VENV_PATH)/bin/pip install --editable .
	@$(VENV_PATH)/bin/pip install -U -r ./requirements-docs.txt
	rm -rf docs/_build
	@$(VENV_PATH)/bin/sphinx-build -vv -W -b html docs/ docs/_build
.PHONY: apidocs

aws-lambda-layer: dist
	$(VENV_PATH)/bin/pip install -r requirements-aws-lambda-layer.txt
	$(VENV_PATH)/bin/python -m scripts.build_aws_lambda_layer
.PHONY: aws-lambda-layer
