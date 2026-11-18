import importlib
import inspect
import json
import sys

NEVER_IMPORTED = frozenset({"antigravity", "this", "idlelib", "tkinter", "turtle", "turtledemo", "test", "__hello__",
                            "__phello__"})


def may_import(name: str) -> bool:
    parts = name.split(".")
    return (all(part.isidentifier() and not part.startswith("_") for part in parts)
            and parts[0] in sys.stdlib_module_names and parts[0] not in NEVER_IMPORTED)


def loaded(name: str):
    if not may_import(name):
        return None
    try:
        return importlib.import_module(name)
    except BaseException:
        return None


def names_in_module(module) -> list[str]:
    names = set(dir(module))
    for name in dir(module):
        member = getattr(module, name, None)
        if inspect.isclass(member):
            names.update(dir(member))
    return sorted(names)


def entry(module) -> dict:
    return {"own": sorted(dir(module)), "all": names_in_module(module), "package": hasattr(module, "__path__")}


def catalogue(names: list[str]) -> dict:
    found = {}
    for name in names:
        module = loaded(name)
        if module is not None:
            found[name] = entry(module)
    return found


def main():
    names = [str(name) for name in json.load(sys.stdin)]
    json.dump(catalogue(names), sys.stdout)


if __name__ == "__main__":
    main()
