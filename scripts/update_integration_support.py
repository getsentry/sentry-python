"""
Find out what the actual minimum supported version of each framework/library is.
"""

import os
import sys

populate_tox_dir = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "populate_tox"
)
sys.path.append(populate_tox_dir)

from populate_tox import main


def update():
    print("Running populate_tox.py...")
    packages = main()

    print("Figuring out the lowest supported version of integrations...")
    min_versions = []

    for _, integrations in packages.items():
        for integration in integrations:
            min_versions.append(
                (integration["integration_name"], str(integration["releases"][0]))
            )

    min_versions = sorted(
        set(
            [
                (integration, tuple([int(v) for v in min_version.split(".")]))
                for integration, min_version in min_versions
            ]
        )
    )

    print()
    print("Effective minimal versions:")
    print(
        "(The format is the same as _MIN_VERSIONS in sentry_sdk/integrations/__init__.py for easy replacing.)"
    )
    print(
        "(When updating these, make sure to also update the docs page for the integration.)"
    )
    print()
    for integration, min_version in min_versions:
        print(f'"{integration}": {min_version},')


if __name__ == "__main__":
    update()
