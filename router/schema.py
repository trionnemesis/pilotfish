"""Small offline validator for the JSON Schema subset used by pilotfish."""

from __future__ import annotations

import json
import math
import re
from datetime import datetime
from pathlib import Path
from typing import Any, Mapping
from urllib.parse import unquote, urldefrag

from .models import canonical_json


ROOT = Path(__file__).resolve().parents[1]
SCHEMA_ROOT = ROOT / "schemas"


class SchemaValidationError(ValueError):
    """A deterministic schema validation failure."""

    def __init__(self, path: str, message: str) -> None:
        self.path = path
        self.message = message
        super().__init__(f"{path}: {message}")


def _schema_path(schema_name: str) -> Path:
    if not isinstance(schema_name, str) or not schema_name:
        raise ValueError("schema_name must be a non-empty string")
    if Path(schema_name).name != schema_name:
        raise ValueError("schema_name must not contain a path")
    filename = (
        schema_name
        if schema_name.endswith(".schema.json")
        else f"{schema_name}.schema.json"
    )
    return SCHEMA_ROOT / filename


def _load_schema(path: Path) -> Mapping[str, Any]:
    document = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(document, dict):
        raise ValueError(f"schema must be an object: {path.name}")
    return document


def validate_schema(schema_name: str, document: Any) -> None:
    """Validate ``document`` against a named schema without dependencies."""

    path = _schema_path(schema_name)
    schema = _load_schema(path)
    _Validator().validate(document, schema, "$", schema, path)


def _json_equal(left: Any, right: Any) -> bool:
    try:
        return canonical_json(left) == canonical_json(right)
    except (TypeError, ValueError):
        return type(left) is type(right) and left == right


def _matches_type(instance: Any, expected: str) -> bool:
    if expected == "null":
        return instance is None
    if expected == "boolean":
        return isinstance(instance, bool)
    if expected == "object":
        return isinstance(instance, Mapping)
    if expected == "array":
        return isinstance(instance, list)
    if expected == "string":
        return isinstance(instance, str)
    if expected == "integer":
        return (
            isinstance(instance, int)
            and not isinstance(instance, bool)
        ) or (
            isinstance(instance, float)
            and math.isfinite(instance)
            and instance.is_integer()
        )
    if expected == "number":
        return isinstance(instance, (int, float)) and not isinstance(instance, bool)
    raise ValueError(f"unsupported schema type: {expected}")


def _escape_path_part(value: Any) -> str:
    text = str(value).replace("~", "~0").replace("/", "~1")
    return text


class _Validator:
    def validate(
        self,
        instance: Any,
        schema: Any,
        path: str,
        root_schema: Mapping[str, Any],
        schema_path: Path,
    ) -> None:
        if schema is True:
            return
        if schema is False:
            raise SchemaValidationError(path, "value is forbidden by schema")
        if not isinstance(schema, Mapping):
            raise ValueError("schema node must be an object or boolean")

        if "$ref" in schema:
            target, target_root, target_path = self._resolve_ref(
                schema["$ref"], root_schema, schema_path
            )
            self.validate(instance, target, path, target_root, target_path)

        if "allOf" in schema:
            for candidate in schema["allOf"]:
                self.validate(instance, candidate, path, root_schema, schema_path)

        if "anyOf" in schema:
            if not any(
                self._is_valid(instance, candidate, path, root_schema, schema_path)
                for candidate in schema["anyOf"]
            ):
                raise SchemaValidationError(path, "does not match any allowed schema")

        if "oneOf" in schema:
            matches = sum(
                self._is_valid(instance, candidate, path, root_schema, schema_path)
                for candidate in schema["oneOf"]
            )
            if matches != 1:
                raise SchemaValidationError(
                    path, f"must match exactly one schema (matched {matches})"
                )

        if "not" in schema and self._is_valid(
            instance, schema["not"], path, root_schema, schema_path
        ):
            raise SchemaValidationError(path, "matches a forbidden schema")

        if "if" in schema:
            branch = "then" if self._is_valid(
                instance, schema["if"], path, root_schema, schema_path
            ) else "else"
            if branch in schema:
                self.validate(
                    instance, schema[branch], path, root_schema, schema_path
                )

        expected_type = schema.get("type")
        if expected_type is not None:
            types = [expected_type] if isinstance(expected_type, str) else expected_type
            if not isinstance(types, list) or not all(
                isinstance(item, str) for item in types
            ):
                raise ValueError("schema type must be a string or string array")
            if not any(_matches_type(instance, item) for item in types):
                raise SchemaValidationError(path, f"expected type {types}")

        if "const" in schema and not _json_equal(instance, schema["const"]):
            raise SchemaValidationError(path, "does not equal the required constant")
        if "enum" in schema and not any(
            _json_equal(instance, candidate) for candidate in schema["enum"]
        ):
            raise SchemaValidationError(path, "value is not in the closed enum")

        if isinstance(instance, Mapping):
            self._validate_object(instance, schema, path, root_schema, schema_path)
        elif isinstance(instance, list):
            self._validate_array(instance, schema, path, root_schema, schema_path)
        elif isinstance(instance, str):
            self._validate_string(instance, schema, path)
        elif isinstance(instance, (int, float)) and not isinstance(instance, bool):
            self._validate_number(instance, schema, path)

    def _validate_object(
        self,
        instance: Mapping[str, Any],
        schema: Mapping[str, Any],
        path: str,
        root_schema: Mapping[str, Any],
        schema_path: Path,
    ) -> None:
        required = schema.get("required", [])
        for key in required:
            if key not in instance:
                raise SchemaValidationError(path, f"missing required property {key!r}")

        properties = schema.get("properties", {})
        patterns = schema.get("patternProperties", {})
        matched = set()
        for key, value in instance.items():
            child_path = f"{path}/{_escape_path_part(key)}"
            if key in properties:
                matched.add(key)
                self.validate(
                    value, properties[key], child_path, root_schema, schema_path
                )
            for pattern, child_schema in patterns.items():
                if re.search(pattern, key):
                    matched.add(key)
                    self.validate(
                        value, child_schema, child_path, root_schema, schema_path
                    )

        additional = schema.get("additionalProperties", True)
        for key, value in instance.items():
            if key in matched:
                continue
            child_path = f"{path}/{_escape_path_part(key)}"
            if additional is False:
                raise SchemaValidationError(child_path, "additional property is forbidden")
            if isinstance(additional, Mapping) or isinstance(additional, bool):
                self.validate(
                    value, additional, child_path, root_schema, schema_path
                )

        if "propertyNames" in schema:
            for key in instance:
                self.validate(
                    key,
                    schema["propertyNames"],
                    f"{path}/<property-name>",
                    root_schema,
                    schema_path,
                )

        for key, dependencies in schema.get("dependentRequired", {}).items():
            if key in instance:
                for dependency in dependencies:
                    if dependency not in instance:
                        raise SchemaValidationError(
                            path,
                            f"property {key!r} requires {dependency!r}",
                        )

        if len(instance) < schema.get("minProperties", 0):
            raise SchemaValidationError(path, "has too few properties")
        if "maxProperties" in schema and len(instance) > schema["maxProperties"]:
            raise SchemaValidationError(path, "has too many properties")

    def _validate_array(
        self,
        instance: list[Any],
        schema: Mapping[str, Any],
        path: str,
        root_schema: Mapping[str, Any],
        schema_path: Path,
    ) -> None:
        if len(instance) < schema.get("minItems", 0):
            raise SchemaValidationError(path, "has too few items")
        if "maxItems" in schema and len(instance) > schema["maxItems"]:
            raise SchemaValidationError(path, "has too many items")
        if schema.get("uniqueItems"):
            encoded = [canonical_json(item) for item in instance]
            if len(encoded) != len(set(encoded)):
                raise SchemaValidationError(path, "array items must be unique")

        prefix = schema.get("prefixItems", [])
        for index, child_schema in enumerate(prefix):
            if index < len(instance):
                self.validate(
                    instance[index],
                    child_schema,
                    f"{path}/{index}",
                    root_schema,
                    schema_path,
                )
        if "items" in schema:
            start = len(prefix) if prefix else 0
            for index in range(start, len(instance)):
                self.validate(
                    instance[index],
                    schema["items"],
                    f"{path}/{index}",
                    root_schema,
                    schema_path,
                )

        if "contains" in schema:
            matches = sum(
                self._is_valid(
                    item,
                    schema["contains"],
                    f"{path}/{index}",
                    root_schema,
                    schema_path,
                )
                for index, item in enumerate(instance)
            )
            minimum = schema.get("minContains", 1)
            maximum = schema.get("maxContains")
            if matches < minimum or (maximum is not None and matches > maximum):
                raise SchemaValidationError(path, "contains match count is out of range")

    @staticmethod
    def _validate_string(
        instance: str, schema: Mapping[str, Any], path: str
    ) -> None:
        if len(instance) < schema.get("minLength", 0):
            raise SchemaValidationError(path, "string is too short")
        if "maxLength" in schema and len(instance) > schema["maxLength"]:
            raise SchemaValidationError(path, "string is too long")
        if "pattern" in schema and re.search(schema["pattern"], instance) is None:
            raise SchemaValidationError(path, "string does not match required pattern")
        if schema.get("format") == "date-time":
            try:
                parsed = datetime.fromisoformat(instance.replace("Z", "+00:00"))
            except ValueError as exc:
                raise SchemaValidationError(path, "invalid RFC3339 date-time") from exc
            if parsed.tzinfo is None:
                raise SchemaValidationError(path, "date-time must include a timezone")

    @staticmethod
    def _validate_number(
        instance: float, schema: Mapping[str, Any], path: str
    ) -> None:
        if "minimum" in schema and instance < schema["minimum"]:
            raise SchemaValidationError(path, "number is below minimum")
        if "maximum" in schema and instance > schema["maximum"]:
            raise SchemaValidationError(path, "number is above maximum")
        if "exclusiveMinimum" in schema and instance <= schema["exclusiveMinimum"]:
            raise SchemaValidationError(path, "number is not above exclusiveMinimum")
        if "exclusiveMaximum" in schema and instance >= schema["exclusiveMaximum"]:
            raise SchemaValidationError(path, "number is not below exclusiveMaximum")
        if "multipleOf" in schema:
            quotient = instance / schema["multipleOf"]
            if not math.isclose(quotient, round(quotient), abs_tol=1e-12):
                raise SchemaValidationError(path, "number is not a multipleOf value")

    def _is_valid(
        self,
        instance: Any,
        schema: Any,
        path: str,
        root_schema: Mapping[str, Any],
        schema_path: Path,
    ) -> bool:
        try:
            self.validate(instance, schema, path, root_schema, schema_path)
        except SchemaValidationError:
            return False
        return True

    def _resolve_ref(
        self,
        reference: Any,
        root_schema: Mapping[str, Any],
        schema_path: Path,
    ) -> tuple[Any, Mapping[str, Any], Path]:
        if not isinstance(reference, str):
            raise ValueError("$ref must be a string")
        resource, fragment = urldefrag(reference)
        target_root = root_schema
        target_path = schema_path
        if resource:
            if "://" in resource:
                raise ValueError("remote schema references are not supported")
            target_path = (schema_path.parent / unquote(resource)).resolve()
            if SCHEMA_ROOT.resolve() not in target_path.parents:
                raise ValueError("schema reference escapes the schema directory")
            target_root = _load_schema(target_path)

        target: Any = target_root
        if fragment:
            if not fragment.startswith("/"):
                raise ValueError("only JSON Pointer schema fragments are supported")
            for raw_part in fragment[1:].split("/"):
                part = unquote(raw_part).replace("~1", "/").replace("~0", "~")
                if not isinstance(target, Mapping) or part not in target:
                    raise ValueError(f"unresolvable schema reference: {reference}")
                target = target[part]
        return target, target_root, target_path
