from rope.base.project import Project
from rope.refactor.rename import Rename


def main():
    project = Project("/Users/antonpirker/code/sentry-python")
    resource = project.get_resource("sentry_sdk/__init__.py")
    renamer = Rename(project, resource)
    changes = renamer.get_changes("sentry_sdk_alpha")
    project.do(changes)


if __name__ == "__main__":
    main()
