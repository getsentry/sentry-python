# We have no handler() here and try to call a non-existing function.

func()  # type: ignore[name-defined]  # noqa: F821
