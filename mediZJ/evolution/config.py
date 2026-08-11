"""自进化模块配置。"""

import os
from dataclasses import dataclass


def _bounded_rate(name: str, default: str) -> float:
    value = float(os.getenv(name, default))
    if not 0 <= value <= 1:
        raise ValueError(f"{name} 必须位于 0 到 1 之间")
    return value


def _positive_float(name: str, default: str) -> float:
    value = float(os.getenv(name, default))
    if value <= 0:
        raise ValueError(f"{name} 必须大于 0")
    return value


def _positive_int(name: str, default: str) -> int:
    value = int(os.getenv(name, default))
    if value <= 0:
        raise ValueError(f"{name} 必须大于 0")
    return value


@dataclass(frozen=True)
class EvolutionSettings:
    """启动时一次性校验的自进化配置。"""

    enabled: bool
    sample_rate: float
    observation_rate: float
    poll_interval: float
    judge_timeout: float
    medical_expiry_days: int
    global_min_support: int
    trusted_sources: frozenset[str]
    trusted_domains: frozenset[str]

    @classmethod
    def from_env(cls) -> "EvolutionSettings":
        trusted_sources = {
            item.strip()
            for item in os.getenv(
                "EVOLUTION_TRUSTED_SOURCES",
                "临床指南数据库,ICD-10疾病编码数据库",
            ).split(",")
            if item.strip()
        }
        trusted_domains = {
            item.strip().lower()
            for item in os.getenv("EVOLUTION_TRUSTED_DOMAINS", "").split(",")
            if item.strip()
        }
        return cls(
            enabled=os.getenv("EVOLUTION_ENABLED", "true").lower()
            in {"1", "true", "yes"},
            sample_rate=_bounded_rate("EVOLUTION_SAMPLE_RATE", "0.2"),
            observation_rate=_bounded_rate(
                "EVOLUTION_OBSERVATION_RATE",
                "0.2",
            ),
            poll_interval=_positive_float("EVOLUTION_POLL_INTERVAL", "2"),
            judge_timeout=_positive_float("EVOLUTION_JUDGE_TIMEOUT", "120"),
            medical_expiry_days=_positive_int(
                "EVOLUTION_MEDICAL_EXPIRY_DAYS",
                "180",
            ),
            global_min_support=_positive_int(
                "EVOLUTION_GLOBAL_MIN_SUPPORT",
                "3",
            ),
            trusted_sources=frozenset(trusted_sources),
            trusted_domains=frozenset(trusted_domains),
        )
