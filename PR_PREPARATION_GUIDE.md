# PR Preparation Guide for GitHub Issue

## Current Status
- Branch: `cursor/prepare-pull-request-for-github-issue-0aef`
- Repository: `getsentry/sentry-python`
- Status: No changes made yet (branch is identical to master)

## Issue Content
**NOTE**: The GitHub issue content was not provided. Please paste the issue content here:

```
[PASTE GITHUB ISSUE CONTENT HERE]
```

## Steps to Prepare the PR

### 1. Understanding the Issue
Once the issue content is available, analyze:
- Is it a bug fix or a feature request?
- Which components/integrations are affected?
- Are there any specific requirements or acceptance criteria?

### 2. Setting Up the Development Environment
```bash
# Create and activate virtual environment
python -m venv .venv
source .venv/bin/activate  # On Windows: .venv\Scripts\activate

# Install sentry-python in editable mode
pip install -e .

# Install development dependencies
pip install -r requirements-devenv.txt
pip install -r requirements-testing.txt
pip install -r requirements-linting.txt

# Install pre-commit hooks for code style
pip install pre-commit
pre-commit install
```

### 3. Common Tasks Based on Issue Type

#### For Bug Fixes:
1. **Reproduce the issue**: Create a minimal test case
2. **Write a test**: Add a test in the appropriate `tests/` directory that fails with the current code
3. **Fix the bug**: Implement the fix in the relevant module
4. **Verify**: Ensure the test passes and no other tests are broken

#### For Feature Requests:
1. **Design the API**: Consider how users will interact with the new feature
2. **Write tests**: Add comprehensive tests for the new functionality
3. **Implement**: Add the feature following the existing code patterns
4. **Documentation**: Update relevant documentation and docstrings

#### For New Integrations:
1. **Write the integration** in `sentry_sdk/integrations/`
   - Instrument by default with zero configuration
   - Handle monkeypatching carefully
   - Don't break user contracts or have side effects
2. **Add tests** in `tests/integrations/<integration_name>/`
3. **Update `setup.py`** with `extras_require` for the integration
4. **Write documentation** (separate PR to sentry-docs repo)

### 4. Code Structure
- Main SDK code: `sentry_sdk/`
- Integrations: `sentry_sdk/integrations/`
- Tests: `tests/`
- Documentation: `docs/`

### 5. Running Tests
```bash
# Run all tests
pytest tests/

# Run specific test file
pytest tests/test_specific.py

# Run tests for a specific integration
pytest -rs tests/integrations/flask/

# Run tests with tox for specific environments
tox -e py3.11-django-v4.2

# Run linters
tox -e linters
```

### 6. Code Quality Checks
```bash
# Run linting (required before PR)
tox -e linters

# Format code
black sentry_sdk tests

# The pre-commit hooks will also run automatically on commit
```

### 7. Commit Message Format
Follow the [Sentry commit message format](https://develop.sentry.dev/commit-messages/#commit-message-format):
```
<type>(<scope>): <subject>

[optional body]

[optional footer]
```

Example:
```
fix(django): Handle empty request body in error handler

Previously, the Django integration would crash when processing
requests with empty bodies. This fix adds proper validation.

Fixes #123
```

### 8. Creating the PR
1. Commit your changes with a descriptive message following the format above
2. Push to your branch
3. Create a PR with:
   - Clear description of changes
   - Reference to the issue (e.g., "Fixes #123")
   - Tests that validate your changes
   - Confirmation that you've run `tox -e linters`

### 9. PR Checklist
- [ ] Tests added/updated for the changes
- [ ] Code passes `tox -e linters`
- [ ] Commit messages follow the proper format
- [ ] PR description clearly explains the changes
- [ ] Issue number referenced in PR description
- [ ] No breaking changes (or clearly documented if necessary)

## Next Steps
1. Please provide the GitHub issue content
2. Based on the issue, I can help implement the necessary changes
3. We'll follow the repository's contribution guidelines

## Additional Resources
- [Contributing Guide](CONTRIBUTING.md)
- [Commit Message Format](https://develop.sentry.dev/commit-messages/#commit-message-format)
- [Sentry Community Discord](https://discord.com/invite/Ww9hbqr)