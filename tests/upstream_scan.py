"""Static (AST) inspection of the installed meta-ads-mcp package.

Nothing is imported or executed: importing meta_ads_mcp would run its
setup_logging() and write to the real log file.
"""
import ast
import importlib.util
from pathlib import Path

HTTP_MODULES = ("requests", "aiohttp", "urllib.request", "urllib3", "http.client", "pycurl")


def package_dir():
    spec = importlib.util.find_spec("meta_ads_mcp")
    if spec is None or not spec.submodule_search_locations:
        raise RuntimeError("meta-ads-mcp is not installed in this environment")
    return Path(next(iter(spec.submodule_search_locations)))


def _modules(root):
    for path in sorted(root.rglob("*.py")):
        yield path.relative_to(root).as_posix(), ast.parse(path.read_text(encoding="utf-8"))


def _is_tool_factory(node):
    """Matches `mcp_server.tool(...)`."""
    return (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
            and node.func.attr == "tool" and isinstance(node.func.value, ast.Name)
            and node.func.value.id == "mcp_server")


def registered_tool_names(root=None):
    """All functions registered via `@mcp_server.tool()` or `mcp_server.tool()(func)`."""
    names = set()
    for _, tree in _modules(root or package_dir()):
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and any(map(_is_tool_factory, node.decorator_list)):
                names.add(node.name)
            elif isinstance(node, ast.Call) and _is_tool_factory(node.func):
                names.update(arg.id for arg in node.args if isinstance(arg, ast.Name))
    return names


def http_imports(root=None):
    """(module file, imported HTTP library) pairs for libraries other than httpx."""
    found = set()
    for relative, tree in _modules(root or package_dir()):
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom) and node.module:
                imported = [node.module]
            else:
                continue
            for name in imported:
                library = next((lib for lib in HTTP_MODULES if name == lib or name.startswith(lib + ".")), None)
                if library:
                    found.add((relative, library))
    return found


def attribute_calls(module_file, object_name, root=None):
    """Method names called on `object_name` in one module, e.g. requests.get -> 'get'."""
    tree = ast.parse(((root or package_dir()) / module_file).read_text(encoding="utf-8"))
    return {
        node.func.attr
        for node in ast.walk(tree)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
        and isinstance(node.func.value, ast.Name) and node.func.value.id == object_name
    }
