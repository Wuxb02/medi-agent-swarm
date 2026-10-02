"""固定 embedding 与向量 schema 的启动校验。"""

MODEL_ID = "BAAI/bge-small-zh-v1.5"
MODEL_REVISION = "7999e1d3359715c523056ef9478215996d62a620"
MODEL_DIMENSION = 512
SCHEMA_DESCRIPTION = f"medizj:v1:{MODEL_ID}:{MODEL_REVISION}"


def validate_collection(description: dict, fields: dict, dimension: int) -> None:
    if dimension != MODEL_DIMENSION:
        raise RuntimeError("embedding 必须使用固定的 512 维模型")
    if description.get("description") != SCHEMA_DESCRIPTION:
        raise RuntimeError("collection 的模型版本或 schema 版本不匹配")
    actual = {field["name"]: field for field in description["fields"]}
    for name, expected_type in fields.items():
        if name not in actual or int(actual[name]["type"]) != int(expected_type):
            raise RuntimeError(f"collection 字段不匹配: {name}")
    vector = actual.get("dense_vector", actual.get("vector"))
    if vector is None or int(vector["params"]["dim"]) != dimension:
        raise RuntimeError("collection 向量维度不匹配")
    if description.get("auto_id") or not actual["id"].get("is_primary"):
        raise RuntimeError("collection 必须使用业务主键")
