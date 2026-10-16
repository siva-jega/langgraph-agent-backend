"""Pure recursive serialization for artifacts / nested objects."""


def to_dict(obj):
    try:
        from pydantic import BaseModel

        if isinstance(obj, BaseModel):
            return obj.model_dump()
    except ImportError:
        pass
    if isinstance(obj, list):
        return [to_dict(item) for item in obj]
    if isinstance(obj, dict):
        return {k: to_dict(v) for k, v in obj.items()}
    obj_dict = getattr(obj, "__dict__", None)
    if isinstance(obj_dict, dict):
        return {k: to_dict(v) for k, v in obj_dict.items()}
    if isinstance(obj, (str, int, float, bool)) or obj is None:
        return obj
