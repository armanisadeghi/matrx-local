import ast
import os
import json

from app.common.fancy_prints import print_link
from app.config import CODE_SAVES_DIR, BASE_DIR

def get_updated_routes_file():
    return os.path.join(BASE_DIR, "app/api/routes.py")

# Define the base API URL and WebSocket configuration
BASE_URL = "http://127.0.0.1:8000"
WEBSOCKET_CONFIG = {
    "url": "ws://127.0.0.1:8000/ws",
    "defaultMessage": "Hello, WebSocket!",
    "reconnectInterval": 5000,
}

def parse_routes(file_path):
    with open(file_path, "r") as f:
        tree = ast.parse(f.read())

    routes = []
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef):
            print(f"Inspecting function: {node.name}")  # Debugging line
            decorator = next(
                (d for d in node.decorator_list if isinstance(d, ast.Call) and hasattr(d.func, 'attr')), None
            )
            if not decorator:
                print(f"No suitable decorator found for function: {node.name}")  # Debugging line
                continue

            if not hasattr(decorator.func, 'attr'):
                print(f"Decorator func.attr missing for function: {node.name}")  # Debugging line
                continue

            print(f"Found decorator: {decorator.func.attr} for function: {node.name}")  # Debugging line

            route = {
                "id": node.name,
                "name": node.name.replace("_", " ").title(),
                "method": None,
                "url": None,
                "description": ast.get_docstring(node) or "",
            }

            # Extract method and path from decorators
            if decorator.func.attr in ["get", "post"]:
                route["method"] = decorator.func.attr.upper()
                route["url"] = decorator.args[0].s if decorator.args and isinstance(decorator.args[0], ast.Str) else ""

            # Extract parameters (arguments)
            route_args = []
            for arg in node.args.args:
                if arg.arg != "self":
                    route_args.append(arg.arg)
            if route_args:
                route["args"] = route_args

            # Detect if body parameters are used
            for stmt in node.body:
                if isinstance(stmt, ast.Assign):
                    if any(isinstance(t, ast.Name) and t.id == "dict" for t in stmt.targets):
                        route["hasBody"] = True
                        break

            routes.append(route)
    return routes

def generate_typescript_config(routes):
    ts_config = {
        "baseUrl": BASE_URL,
        "endpoints": [],
        "websocket": WEBSOCKET_CONFIG,
    }

    for route in routes:
        endpoint = {
            "id": route["id"],
            "name": route["name"],
            "method": route["method"],
            "url": route["url"],
            "description": route["description"],
        }
        if "args" in route:
            endpoint["args"] = route["args"]
        if route.get("hasBody"):
            endpoint["hasBody"] = True
            endpoint["defaultBody"] = {key: "" for key in route.get("args", [])}

        ts_config["endpoints"].append(endpoint)

    return ts_config

def main():
    routes_file = get_updated_routes_file()
    if not os.path.exists(routes_file):
        print(f"Error: Routes file not found at {routes_file}")
        return

    routes = parse_routes(routes_file)
    if not routes:
        print("Error: No routes were parsed from the file.")
        return

    ts_config = generate_typescript_config(routes)

    if not os.path.exists(CODE_SAVES_DIR):
        os.makedirs(CODE_SAVES_DIR)

    version = 1
    while True:
        output_file = os.path.join(CODE_SAVES_DIR, f"api_config_v{version}.ts")
        if not os.path.exists(output_file):
            break
        version += 1

    with open(output_file, "w") as f:
        f.write("const API_CONFIG = ")
        f.write(json.dumps(ts_config, indent=2))
        f.write(";\nexport default API_CONFIG;\n")

    print(f"API configuration has been generated and saved to {output_file}")
    print_link(output_file)

if __name__ == "__main__":
    main()
