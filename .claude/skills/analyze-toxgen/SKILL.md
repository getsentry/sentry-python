---
name: analyze-toxgen-failures
description: Analyze toxgen failures
---

# Analyze Toxgen

## Instructions

The purpose of this skill is to analyze and resolve test failures introduced by
updating our test matrix.


### Step 1: Fetch CI status

Find the newest PR from the toxgen/update branch in the getsentry/sentry-python
repo on GitHub.

Check the results of the CI runs on the PR. If not all workflows have
finished, wait for them to finish, checking periodically every ~10 seconds.
Look for failures of worfklows starting with "Test" (for example,
"Test Agents" or "Test Web 1", etc.). If there are no failures, inform the
user and don't continue with the next steps.

The different jobs run the test suite for multiple integrations on one Python
version. So for instance "DBs (3.12, ubuntu-22.04)" runs the tests for database
integrations on Python 3.12. Multiple versions of a single integration might
be run in a single job, so for example the Redis step in the
"DBs (3.12, ubuntu-22.04)" job might run the test suite against Redis versions
5.3.1 as well as 6.4.0. The test matrix that determines what package versions
are run on which Python versions is stored in tox.ini.

Make a list of all tox targets that are failing. A tox target has the pattern
"py{python-version}-{integration}-{package-version}". A failing tox target
will look like this: "py3.14t-openai_agents-v0.9.1: FAIL", while a passing
one will look like this: "py3.14t-openai_agents-v0.9.1: OK".

Compile a text summary that contains the following:
   - A list of all failing integrations.
   - For each integration:
     * The specific tox targets that are failing
     * The test failure message or error output from the failing tests
     * The command used in CI to run the test suite to reproduce the failure --
       it should use tox, check the job output for the specific command
   - Show the summary to the user.


### Step 2: Analyze failures

Do this for each integration that is failing.

#### Determine if the failure is consistent

The first step is to determine whether the failure is a flake, for example
because of a temporary connection problem, or if it persists, because it's
related to a change introduced in a new version.
- Check if the package version that's failing was newly added to tox.ini via
  the PR. If not, it's likely to be a flake.
- Check whether the same package version is failing on other Python versions,
  too. Check whether there are more failing jobs on different Python versions
  where the same version of the same package is failing. If the specific package
  version is failing across multiple Python versions, it's unlikely to be a
  flake.

If it looks like a flake, offer to rerun the failing test suite. If the
user accepts, wait for the result, polling periodically. If the run is
successful, there's nothing else to do; move on to the next integration if
there is another one that's failing.

#### Analyze non-flake failures

Run the test suite for the failing tox target locally via  `tox -e {tox_target}`.

Analyze the error message from the local run, then start localizing
the source of the breakage:

1. Retrieve the repo code into a temporary directory.
2. Using git, retrieve the diff between the last working version of the package
   (the original max version in tox.ini before the PR) and the newly introduced,
   failing version.
3. Analyze the diff, looking for parts that could be related to the failing
   tests. Remember the specific code parts that are relevant so that you can
   show them to the user.

Present the user with the results of your investigation. Make sure to link or
point to the specific code snippets for double checking.

#### Propose a fix

Ask the user if you should propose a fix.

First check out the toxgen/update branch locally. Then branch off of it for the
fix. When fixing, make sure the integration code supports both the current and
the new way of integrating with the library, as we need to support both.
