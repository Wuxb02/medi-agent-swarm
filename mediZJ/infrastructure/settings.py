"""部署配置；必需基础设施不允许静默降级。"""

from functools import lru_cache

from pydantic import Field, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    mysql_url: str
    redis_url: str
    milvus_uri: str
    milvus_token: str = ""
    embedding_model_name: str = "BAAI/bge-small-zh-v1.5"
    knowledge_collection: str = "medical_knowledge_v1"
    session_collection: str = "session_summaries_v1"
    app_environment: str = "production"
    upload_dir: str = "/data/uploads"
    mysql_pool_size: int = Field(default=20, ge=1)
    redis_timeout: float = Field(default=5, gt=0)
    short_term_ttl: int = Field(default=3600, ge=1)
    run_concurrency: int = Field(default=16, ge=1)
    run_queue_limit: int = Field(default=32, ge=1)
    run_queue_timeout: int = Field(default=30, ge=1)
    user_run_limit: int = Field(default=2, ge=1)
    run_timeout: int = Field(default=300, ge=1)
    questionnaire_ttl: int = Field(default=86400, ge=1)
    lease_seconds: int = Field(default=60, ge=30)
    heartbeat_seconds: int = Field(default=15, ge=1)
    llm_max_concurrency: int = Field(default=16, ge=1)
    llm_queue_limit: int = Field(default=32, ge=1)
    llm_queue_timeout: int = Field(default=10, ge=1)
    background_llm_limit: int = Field(default=4, ge=1)
    event_subscriber_limit: int = Field(default=64, ge=1)
    slow_client_timeout: int = Field(default=30, ge=1)

    @field_validator("mysql_url")
    @classmethod
    def mysql_driver(cls, value: str) -> str:
        if not value.startswith("mysql+asyncmy://"):
            raise ValueError("MYSQL_URL 必须使用 mysql+asyncmy://")
        return value

    @field_validator("milvus_uri")
    @classmethod
    def milvus_server(cls, value: str) -> str:
        if not value.startswith(("http://", "https://")):
            raise ValueError("MILVUS_URI 必须指向 Milvus 服务端")
        return value

    @field_validator("embedding_model_name")
    @classmethod
    def fixed_embedding(cls, value: str) -> str:
        if value not in {"BAAI/bge-small-zh-v1.5", "/opt/models/embedding"}:
            raise ValueError("embedding 必须使用固定模型或镜像内预置资源")
        return value

    @model_validator(mode="after")
    def validate_leases(self):
        if self.heartbeat_seconds * 2 >= self.lease_seconds:
            raise ValueError("续租间隔必须小于租约时长的一半")
        if self.background_llm_limit > self.llm_max_concurrency:
            raise ValueError("后台 LLM 上限不能超过全集群上限")
        if self.llm_queue_timeout >= self.lease_seconds:
            raise ValueError("LLM 等待超时必须小于容量租约时长")
        return self


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
