import unittest

from fastapi.routing import APIRoute

from app.auth import require_auth, router as auth_router
from app.main import app


AUTH_PUBLIC_OPERATIONS = {
    ("POST", "/api/auth-app/login"),
    ("POST", "/api/auth-app/logout"),
    ("GET", "/api/auth-app/me"),
}
APP_PUBLIC_OPERATIONS = {
    ("GET", "/api/health"),
    ("POST", "/api/app/shutdown"),
}
PUBLIC_API_OPERATIONS = AUTH_PUBLIC_OPERATIONS | APP_PUBLIC_OPERATIONS


def _dependency_tree_contains(dependant, target) -> bool:
    for dependency in getattr(dependant, "dependencies", []):
        if getattr(dependency, "call", None) is target:
            return True
        if _dependency_tree_contains(dependency, target):
            return True
    return False


class ApiContractTests(unittest.TestCase):
    def _all_api_routes(self):
        return [
            route
            for route in app.routes
            if isinstance(route, APIRoute)
            and route.path.startswith("/api/")
        ]

    def _schema_api_routes(self):
        return [route for route in self._all_api_routes() if route.include_in_schema]

    def test_no_duplicate_method_path_operations(self):
        seen = {}
        duplicates = []
        for route in self._schema_api_routes():
            for method in sorted(route.methods or []):
                key = (method, route.path)
                if key in seen:
                    duplicates.append((key, seen[key], route.name))
                else:
                    seen[key] = route.name
        self.assertEqual(duplicates, [], f"duplicate API operations: {duplicates}")

    def test_auth_router_public_surface_is_exact(self):
        operations = {
            (method, route.path)
            for route in auth_router.routes
            for method in (getattr(route, "methods", None) or set())
        }
        self.assertEqual(
            operations,
            AUTH_PUBLIC_OPERATIONS,
            f"auth public surface changed: {sorted(operations)}",
        )

    def test_public_api_surface_is_explicit_and_small(self):
        accidental_public = []
        for route in self._all_api_routes():
            if route.path == "/api/{rest:path}":
                continue
            protected = _dependency_tree_contains(route.dependant, require_auth)
            for method in route.methods or set():
                key = (method, route.path)
                if not protected and key not in PUBLIC_API_OPERATIONS:
                    accidental_public.append(key)

        self.assertEqual(
            accidental_public,
            [],
            f"API operations became public without allowlisting: {sorted(accidental_public)}",
        )

    def test_all_non_public_api_operations_require_app_auth(self):
        unprotected = []
        for route in self._all_api_routes():
            if route.path == "/api/{rest:path}":
                continue
            for method in route.methods or set():
                key = (method, route.path)
                if key in PUBLIC_API_OPERATIONS:
                    continue
                if not _dependency_tree_contains(route.dependant, require_auth):
                    unprotected.append(key)
        self.assertEqual(
            unprotected,
            [],
            f"protected API operations missing require_auth: {sorted(unprotected)}",
        )

    def test_openapi_operation_ids_are_unique(self):
        schema = app.openapi()
        operation_ids = []
        for path_item in schema.get("paths", {}).values():
            for method, operation in path_item.items():
                if method.lower() not in {"get", "post", "put", "delete", "patch", "options", "head"}:
                    continue
                operation_id = operation.get("operationId")
                self.assertTrue(operation_id, f"missing OpenAPI operationId for {method}")
                operation_ids.append(operation_id)

        self.assertEqual(
            len(operation_ids),
            len(set(operation_ids)),
            "OpenAPI contains duplicate operationId values",
        )
        self.assertGreaterEqual(
            len(operation_ids),
            40,
            "API surface unexpectedly shrank; review router registration",
        )

    def test_fallback_api_route_stays_out_of_openapi(self):
        schema_paths = app.openapi().get("paths", {})
        self.assertNotIn("/api/{rest}", schema_paths)
        fallback = [
            route
            for route in app.routes
            if isinstance(route, APIRoute) and route.path == "/api/{rest:path}"
        ]
        self.assertEqual(len(fallback), 1)
        self.assertFalse(fallback[0].include_in_schema)


if __name__ == "__main__":
    unittest.main()
