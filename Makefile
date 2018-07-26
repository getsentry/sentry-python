dist:
	rm -rf dist build
	python setup.py sdist bdist_wheel

.PHONY: dist
