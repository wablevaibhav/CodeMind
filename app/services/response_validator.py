"""Turn raw model text into a validated response model, or a sanitized ValidationFailure."""

import json
import types
from typing import Any, TypeVar, Union, get_args, get_origin

from pydantic import BaseModel, ValidationError

from app.schemas.errors import FailureCategory, ValidationFailure, ValidationIssue
from app.utils.json_parser import JSONExtractionError, extract_json_text

T = TypeVar("T", bound=BaseModel)


class ResponseValidationError(Exception):
    def __init__(self, failure: ValidationFailure) -> None:
        super().__init__(failure.summary)
        self.failure = failure


def _model_types(annotation: Any) -> list[type[BaseModel]]:
    """BaseModel subclasses reachable through Optional/list wrappers of an annotation."""
    origin = get_origin(annotation)
    if origin in (Union, types.UnionType, list):
        return [m for arg in get_args(annotation) for m in _model_types(arg)]
    if isinstance(annotation, type) and issubclass(annotation, BaseModel):
        return [annotation]
    return []


def drop_unknown_fields(value: Any, model_cls: type[BaseModel]) -> Any:
    """Lenient schema policy: remove keys the model does not declare, recursively."""
    if not isinstance(value, dict):
        return value
    cleaned: dict[str, Any] = {}
    for key, item in value.items():
        field = model_cls.model_fields.get(key)
        if field is None:
            continue
        nested = _model_types(field.annotation)
        if nested and isinstance(item, dict):
            item = drop_unknown_fields(item, nested[0])
        elif nested and isinstance(item, list):
            item = [drop_unknown_fields(i, nested[0]) for i in item]
        cleaned[key] = item
    return cleaned


def _issues(exc: ValidationError, limit: int) -> list[ValidationIssue]:
    out: list[ValidationIssue] = []
    for err in exc.errors(include_input=False, include_url=False)[:limit]:
        path = ".".join(str(p) for p in err["loc"])
        out.append(
            ValidationIssue(path=path, message=str(err["msg"])[:200], error_type=err["type"])
        )
    return out


def validate_response(
    raw: str, model_cls: type[T], *, strict: bool = True, max_issues: int = 10
) -> T:
    """Parse and validate. Raises ResponseValidationError carrying a sanitized failure report."""
    if not (raw or "").strip():
        raise ResponseValidationError(
            ValidationFailure(
                category=FailureCategory.EMPTY_RESPONSE,
                summary="The response was empty. Return one JSON object.",
            )
        )
    try:
        text = extract_json_text(raw)
        data = json.loads(text)
    except (JSONExtractionError, json.JSONDecodeError) as exc:
        msg = exc.msg if isinstance(exc, json.JSONDecodeError) else str(exc)
        raise ResponseValidationError(
            ValidationFailure(
                category=FailureCategory.INVALID_JSON,
                summary=f"The response was not valid JSON ({msg[:120]}). Return one JSON object.",
            )
        ) from exc
    if not isinstance(data, dict):
        raise ResponseValidationError(
            ValidationFailure(
                category=FailureCategory.INVALID_JSON,
                summary="The JSON value must be an object, not a list or scalar.",
            )
        )
    if not strict:
        data = drop_unknown_fields(data, model_cls)
    try:
        return model_cls.model_validate(data)
    except ValidationError as exc:
        issues = _issues(exc, max_issues)
        raise ResponseValidationError(
            ValidationFailure(
                category=FailureCategory.SCHEMA_VIOLATION,
                summary=f"The JSON did not match the schema ({exc.error_count()} problem(s)).",
                issues=issues,
            )
        ) from exc
