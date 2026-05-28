"""
validator.py
─────────────────────────────────────────────────────────────────────────────
수집 레코드 검증 모듈 (Pydantic v2)

검증 규칙:
  - source        : 허용된 소스명 중 하나
  - period        : YYYY-MM 형식, 실존하는 연월
  - variable_name : 비어있지 않음, 공백 없음
  - category_key  : 비어있지 않음
  - value         : None 허용 (결측), 숫자인 경우 -inf/inf/NaN 불허
  - adjustment_type: "raw" | "seasonal" | "index"
  - ingested_at   : ISO 8601 형식
  - run_id        : UUID 형식
"""

import logging
import re
from typing import Optional

from pydantic import BaseModel, field_validator, model_validator, ValidationError

logger = logging.getLogger(__name__)

# 허용 소스 목록
VALID_SOURCES = {
    "kosis_emp",
    "kosis_wage",        # 사업체노동력조사 (임금·근로시간)
    "kosis_benefit",
    "naver_datalab",
    "naver_jobpost",
    "naver_news",
    "bls",
    "google_trends",
}

VALID_ADJUSTMENT_TYPES = {"raw", "seasonal", "index", "derived_pct", "derived_ma"}

_PERIOD_RE = re.compile(r"^\d{4}-(0[1-9]|1[0-2])$")
_VARNAME_RE = re.compile(r"^[a-z][a-z0-9_]*$")


# ── Pydantic 스키마 ───────────────────────────────────────────────────────────
class IngestionRecord(BaseModel):
    source:          str
    period:          str
    variable_name:   str
    category_key:    str
    value:           Optional[float]
    adjustment_type: str
    ingested_at:     str
    run_id:          str
    category_name:   Optional[str] = None

    @field_validator("source")
    @classmethod
    def check_source(cls, v: str) -> str:
        if v not in VALID_SOURCES:
            raise ValueError(f"허용되지 않은 source: '{v}'. 허용: {VALID_SOURCES}")
        return v

    @field_validator("period")
    @classmethod
    def check_period(cls, v: str) -> str:
        if not _PERIOD_RE.match(v):
            raise ValueError(f"period 형식 오류: '{v}' (YYYY-MM 필요)")
        return v

    @field_validator("variable_name")
    @classmethod
    def check_variable_name(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("variable_name이 비어 있습니다")
        if not _VARNAME_RE.match(v):
            raise ValueError(
                f"variable_name 형식 오류: '{v}' "
                "(소문자·숫자·언더스코어만 허용, 소문자로 시작)"
            )
        return v

    @field_validator("category_key")
    @classmethod
    def check_category_key(cls, v: str) -> str:
        if not v or not v.strip():
            raise ValueError("category_key가 비어 있습니다")
        return v.strip()

    @field_validator("value")
    @classmethod
    def check_value(cls, v: Optional[float]) -> Optional[float]:
        if v is None:
            return v
        import math
        if math.isnan(v) or math.isinf(v):
            raise ValueError(f"value가 NaN 또는 Inf입니다: {v}")
        return v

    @field_validator("adjustment_type")
    @classmethod
    def check_adjustment_type(cls, v: str) -> str:
        if v not in VALID_ADJUSTMENT_TYPES:
            raise ValueError(
                f"허용되지 않은 adjustment_type: '{v}'. 허용: {VALID_ADJUSTMENT_TYPES}"
            )
        return v

    @field_validator("run_id")
    @classmethod
    def check_run_id(cls, v: str) -> str:
        _uuid_re = re.compile(
            r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$",
            re.IGNORECASE,
        )
        if not _uuid_re.match(v):
            raise ValueError(f"run_id가 UUID 형식이 아닙니다: '{v}'")
        return v


# ── 검증 실행 ─────────────────────────────────────────────────────────────────
def validate_records(
    records: list[dict],
) -> tuple[list[dict], dict]:
    """
    레코드 목록을 Pydantic으로 검증하고 유효한 레코드만 반환.

    Parameters
    ----------
    records : 수집된 원시 레코드 목록

    Returns
    -------
    (valid_records, report) : tuple
        valid_records : 검증 통과한 레코드 목록 (dict)
        report : {
            "total_count":   int,
            "valid_count":   int,
            "invalid_count": int,
            "errors_by_source": { source: [error_msg, ...] }
        }
    """
    valid: list[dict]   = []
    errors_by_source: dict[str, list[str]] = {}

    for i, rec in enumerate(records):
        try:
            validated = IngestionRecord(**rec)
            valid.append(validated.model_dump())
        except ValidationError as e:
            src = rec.get("source", "unknown")
            period = rec.get("period", "?")
            var = rec.get("variable_name", "?")
            msg = f"[{i}] {src}/{var}/{period}: {e.error_count()}건 오류"

            if src not in errors_by_source:
                errors_by_source[src] = []
            errors_by_source[src].append(msg)

            # 상세 오류는 DEBUG 레벨로만 출력
            logger.debug(f"검증 실패 — {msg}\n{e}")

    total   = len(records)
    invalid = total - len(valid)

    # 소스별 오류 집계 요약 출력
    if errors_by_source:
        logger.warning(f"[Validator] 검증 실패 {invalid}건:")
        for src, errs in errors_by_source.items():
            logger.warning(f"  {src}: {len(errs)}건")

    report = {
        "total_count":      total,
        "valid_count":      len(valid),
        "invalid_count":    invalid,
        "errors_by_source": {
            src: len(errs) for src, errs in errors_by_source.items()
        },
    }

    return valid, report


# ── 단독 실행 (검증 테스트용) ─────────────────────────────────────────────────
if __name__ == "__main__":
    import json, sys

    logging.basicConfig(level=logging.INFO)
    sample = [
        {
            "source": "kosis_emp",
            "period": "2025-12",
            "variable_name": "raw_employed",
            "category_key": "total",
            "value": 28500.0,
            "adjustment_type": "raw",
            "ingested_at": "2026-01-01T00:00:00+00:00",
            "run_id": "550e8400-e29b-41d4-a716-446655440000",
        },
        {
            "source": "INVALID_SOURCE",          # 오류 케이스
            "period": "2025-13",                  # 오류 케이스
            "variable_name": "raw_employed",
            "category_key": "total",
            "value": float("nan"),                # 오류 케이스
            "adjustment_type": "raw",
            "ingested_at": "2026-01-01T00:00:00+00:00",
            "run_id": "550e8400-e29b-41d4-a716-446655440000",
        },
    ]

    valid, report = validate_records(sample)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    print(f"유효 레코드: {len(valid)}건")
