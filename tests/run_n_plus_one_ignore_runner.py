import importlib.util
import sys
import os

# Ensure local package is importable
repo_root = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if repo_root not in sys.path:
    sys.path.insert(0, repo_root)

for test_file in ("test_n_plus_one_ignore.py", "test_n_plus_one_api.py"):
    spec = importlib.util.spec_from_file_location(
        test_file, os.path.join(os.path.dirname(__file__), test_file)
    )
    module = importlib.util.module_from_spec(spec)
    try:
        spec.loader.exec_module(module)
    except Exception as e:
        print(test_file + " - FAILED to import:", e)
        sys.exit(1)

    failed = False
    for name in dir(module):
        if name.startswith("test_"):
            try:
                getattr(module, name)()
                print(f"{test_file}:{name} - OK")
            except AssertionError:
                print(f"{test_file}:{name} - FAILED (AssertionError)")
                failed = True
            except Exception as e:
                print(f"{test_file}:{name} - FAILED (Exception): {e}")
                failed = True

    if failed:
        sys.exit(1)

print("All tests passed")
