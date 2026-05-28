"""
kosis_fetcher.py
─────────────────────────────────────────────────────────────────────────────
KOSIS Open API 수집 모듈 — 메타데이터 및 코드 시트 기반 완전 재작성

━━━ 수집 대상 (kosis_metadata.xlsx [데이터명] 시트 기준) ━━━━━━━━━━━━━━━━━━━

[source = kosis_emp]  경제활동인구조사 / orgId=101

  No  통계표ID            통계표명                          수록기간          조정
  ─────────────────────────────────────────────────────────────────────────
  1   DT_1DA7012S        성/연령별 경제활동인구              1999.06~현재     원계열
  2   DT_1DA9001S        계절조정 경제활동인구 총괄           1999.06~현재     계절조정
  3   DT_1DA7E06S_NEW    산업별 취업자                      2013.01~현재     원계열
  4   DT_1DA7E26S_NEW    성/산업별 취업자                   2013.01~현재     원계열
  5   DT_1DA9003S        산업별 계절조정 취업자              2013.01~현재     계절조정
  6   DT_1DA7010S        종사상지위별 취업자                 1963~현재        원계열
  7   DT_1DA7028S        성/종사상지위별 취업자              1963~현재        원계열
  8   DT_1DA9006S        종사상지위별 계절조정 취업자         1989.01~현재     계절조정

[source = kosis_wage]  사업체노동력조사 / orgId=118

  9   DT_118N_MON051     산업/규모별 임금 및 근로시간        2020.01~현재     원계열

[source = kosis_benefit]  고용행정통계 / orgId=118

  10  DT_11844N_7374_Z   구직급여 신청 동향                 2015.01~현재     원계열

━━━ API 파라미터 규칙 ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

  - 복수 코드: "코드1+코드2+코드3+"  (끝에 + 포함)
  - 전체 분류: "ALL"  (코드 시트에 없는 분류는 ALL 사용)
  - 성별 코드:
      일반 테이블 (DT_1DA7012S 등): 전체=0, 남=1, 여=2
      계절조정 테이블 (DT_1DA9001S): 전체=00, 남=10, 여=20
  - 연령대 코드: 00(전체)+75(15-29)+10(15-19)+20(20-29)+30(30-39)+
                 40(40-49)+50(50-59)+60(60이상)+63(15-64)+70(15-24)+
  - 경활인구 변수: T10(15이상인구)+T20(경활인구)+T30(취업자)+T40(실업자)+
                  T50(비경활)+T60(경활참가율)+T80(실업률)+T90(고용률)+
  - 산업코드 (경활): 01+05+10+35+37+41+45+49+55+58+64+68+70+74+84+85+86+90+94+97+99+
  - 임금/시간 변수: 전체임금총액=13103110311MD_12, 상용임금총액=13103110311MD_13,
                   상용정액급여=13103110311MD_14, 상용초과급여=13103110311MD_15,
                   전체근로시간=13103110311MD_7, 상용총근로시간=13103110311MD_8
  - 산업코드 (사업체): 190326INDUSTRY_10S0(전체)+...+190326INDUSTRY_10SS+
  - 규모코드: size01(전규모)+...+size14+
  - 고용행정통계 변수: T001(신청자)+T002(지급자)+T003(지급액)+T004(지급건수)+

API 문서: https://kosis.kr/openapi/
"""

import time
import logging
from dataclasses import dataclass
from typing import Optional

import requests

from config import KOSIS_API_KEY, get_env_periods

logger = logging.getLogger(__name__)

KOSIS_BASE_URL = "https://kosis.kr/openapi/Param/statisticsParameterData.do"


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 코드 상수 (코드 시트 기반)
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

# ── 경제활동인구조사 성별 코드 ──
SEX_CODES_GENERAL    = "0+1+2+"        # 일반 테이블 (전체/남/여)
SEX_CODES_SEASONAL   = "00+10+20+"     # 계절조정 테이블 (전체/남/여, 코드 체계 다름)

# ── 경제활동인구조사 연령대 코드 ──
AGE_CODES = "00+75+10+20+30+40+50+60+63+70+"
# 00=전체, 75=15-29세, 10=15-19세, 20=20-29세, 30=30-39세,
# 40=40-49세, 50=50-59세, 60=60세이상, 63=15-64세, 70=15-24세

# ── 경제활동인구조사 변수명 코드 ──
EMP_ITEM_CODES = "T10+T20+T30+T40+T50+T60+T80+T90+"
# T10=15이상인구, T20=경제활동인구, T30=취업자, T40=실업자,
# T50=비경제활동인구, T60=경제활동참가율, T80=실업률, T90=고용률

EMP_ITEM_EMPLOYED_ONLY = "T30+"         # 취업자 수만 수집할 때

# ── 경제활동인구조사 산업코드 ──
INDUSTRY_CODES_EMP = (
    "01+05+10+35+37+41+45+49+55+58+64+68+70+74+84+85+86+90+94+97+99+"
)
# 01=농업임업어업, 05=광업, 10=제조업, 35=전기가스,
# 37=수도폐기물, 41=건설업, 45=도매소매, 49=운수창고,
# 55=숙박음식점, 58=정보통신, 64=금융보험, 68=부동산,
# 70=전문과학기술, 74=사업시설관리, 84=공공행정,
# 85=교육서비스, 86=보건사회복지, 90=예술스포츠,
# 94=협회단체, 97=가구내고용, 99=국제기관

# ── 사업체노동력조사 변수 코드 (임금/근로시간) ──
WAGE_ITEM_CODES = (
    "13103110311MD_12+"   # 전체임금총액
    "13103110311MD_13+"   # 상용임금총액
    "13103110311MD_14+"   # 상용정액급여
    "13103110311MD_15+"   # 상용초과급여
    #"13103110311MD_7+"    # 전체근로시간
    #"13103110311MD_8+"    # 상용총근로시간
)

INDUSTRY_CODES_SE = (
    "190326INDUSTRY_10S0+"    # 전체
    "190326INDUSTRY_10SB+"    # B.광업
    "190326INDUSTRY_10SC+"    # C.제조업
    "190326INDUSTRY_10SD+"    # D.전기가스
    "190326INDUSTRY_10SE+"    # E.수도폐기물
    "190326INDUSTRY_10SF+"    # F.건설업
    "190326INDUSTRY_10SG+"    # G.도소매
    "190326INDUSTRY_10SH+"    # H.운수창고
    "190326INDUSTRY_10SI+"    # I.숙박음식점
    "190326INDUSTRY_10SJ+"    # J.정보통신
    "190326INDUSTRY_10SK+"    # K.금융보험
    "190326INDUSTRY_10SL+"    # L.부동산
    "190326INDUSTRY_10SM+"    # M.전문과학기술
    "190326INDUSTRY_10SN+"    # N.사업시설관리
    "190326INDUSTRY_10SP+"    # P.교육
    "190326INDUSTRY_10SQ+"    # Q.보건복지
    "190326INDUSTRY_10SR+"    # R.예술스포츠
    "190326INDUSTRY_10SS+"    # S.협회단체
)

SIZE_CODES_SE = (
    "size01+"   # 전규모(1인이상)
    #"size02+"   # 5인이상
    #"size03+"   # 10인이상
    #"size04+"   # 30인이상
    #"size05+"   # 1~299인
    #"size06+"   # 5~299인
    "size07+"   # 1~4인
    "size08+"   # 5~9인
    "size09+"   # 10~29인
    "size10+"   # 30~99인
    "size11+"   # 100~299인
    "size12+"   # 300인이상
    #"size13+"   # 1~9인
    #"size14+"   # 1~29인
)

# ── 고용행정통계 변수 코드 ──
BENEFIT_ITEM_CODES = "T001+T002+T003+T004+"
# T001=구직급여 신청자, T002=구직급여 지급자,
# T003=구직급여 지급액,  T004=구직급여 지급건수


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 데이터셋 정의
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
@dataclass
class KosisDataset:
    variable_name:   str          # BQ variable_name (prefix: {adjustment_type}_)
    org_id:          str          # KOSIS 기관코드
    tbl_id:          str          # KOSIS 통계표 ID
    adjustment_type: str          # "raw" | "seasonal"
    description:     str          # 한국어 설명
    tbl_nm:          str          # KOSIS 통계표명 (메타데이터 기준)
    available_from:  str          # 수록 시작 기간 YYYY-MM
    itm_id:          str          # 항목코드 문자열 (예: "T10+T20+T30+")
    obj_l1:          str = "ALL"  # 분류1
    obj_l2:          str = ""     # 분류2
    obj_l3:          str = ""     # 분류3
    source:          str = "kosis_emp"


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# KOSIS_EMP_DATASETS  (경제활동인구조사 / orgId=101)
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
KOSIS_EMP_DATASETS: list[KosisDataset] = [

    # ① 성/연령별 경제활동인구 (원계열) — DT_1DA7012S
    # URL 예시:
    #   ?itmId=T10+T20+T30+T40+T50+T60+T80+T90+
    #   &objL1=0+1+2+  (성별: 전체=0, 남=1, 여=2)
    #   &objL2=00+75+10+20+30+40+50+60+63+70+  (연령대)
    #   &orgId=101&tblId=DT_1DA7012S
    KosisDataset(
        variable_name   = "employed_by_sex_age",
        org_id          = "101",
        tbl_id          = "DT_1DA7012S",
        adjustment_type = "raw",
        description     = "성/연령별 경제활동인구 (원계열)",
        tbl_nm          = "성/연령별 경제활동인구",
        available_from  = "1999-06",
        itm_id          = EMP_ITEM_CODES,          # T10~T90
        obj_l1          = SEX_CODES_GENERAL,        # 0+1+2+
        obj_l2          = AGE_CODES,                # 00+75+10+20+...
        source          = "kosis_emp",
    ),

    # ② 계절조정 경제활동인구 총괄 (계절조정) — DT_1DA9001S
    # ※ 이 테이블의 성별 코드는 00(전체)/10(남)/20(여) — 일반 테이블과 다름
    # URL 예시:
    #   ?itmId=T10+T20+T30+T40+T50+T60+T80+T90+
    #   &objL1=00+10+20+  (계절조정 전용 성별 코드)
    #   &objL2=  (연령대 분류 없음)
    #   &orgId=101&tblId=DT_1DA9001S
    KosisDataset(
        variable_name   = "employed",
        org_id          = "101",
        tbl_id          = "DT_1DA9001S",
        adjustment_type = "seasonal",
        description     = "계절조정 경제활동인구 총괄 (계절조정)",
        tbl_nm          = "계절조정 경제활동인구 총괄",
        available_from  = "1999-06",
        itm_id          = EMP_ITEM_CODES,           # T10~T90
        obj_l1          = SEX_CODES_SEASONAL,        # 00+10+20+ (계절조정 전용)
        obj_l2          = "",                        # 연령 분류 없음
        source          = "kosis_emp",
    ),

    # ③ 산업별 취업자 (원계열) — DT_1DA7E06S_NEW
    # URL 예시:
    #   ?itmId=T30+
    #   &objL1=01+05+10+...+99+  (산업 대분류 코드)
    #   &objL2=  (없음)
    #   &orgId=101&tblId=DT_1DA7E06S_NEW
    KosisDataset(
        variable_name   = "employed_by_industry",
        org_id          = "101",
        tbl_id          = "DT_1DA7E06S_NEW",
        adjustment_type = "raw",
        description     = "산업별 취업자 (원계열)",
        tbl_nm          = "산업별 취업자",
        available_from  = "2013-01",
        itm_id          = EMP_ITEM_EMPLOYED_ONLY,   # T30만
        obj_l1          = INDUSTRY_CODES_EMP,        # 01+05+...+99+
        obj_l2          = "",
        source          = "kosis_emp",
    ),

    # ④ 성/산업별 취업자 (원계열) — DT_1DA7E26S_NEW
    # URL 예시:
    #   ?itmId=T30+
    #   &objL1=0+1+2+  (성별)
    #   &objL2=01+05+10+...+99+  (산업)
    #   &orgId=101&tblId=DT_1DA7E26S_NEW
    KosisDataset(
        variable_name   = "employed_by_sex_industry",
        org_id          = "101",
        tbl_id          = "DT_1DA7E26S_NEW",
        adjustment_type = "raw",
        description     = "성/산업별 취업자 (원계열)",
        tbl_nm          = "성/산업별 취업자",
        available_from  = "2013-01",
        itm_id          = EMP_ITEM_EMPLOYED_ONLY,   # T30만
        obj_l1          = SEX_CODES_GENERAL,         # 0+1+2+
        obj_l2          = INDUSTRY_CODES_EMP,        # 01+05+...+99+
        source          = "kosis_emp",
    ),

    # ⑤ 산업별 계절조정 취업자 (계절조정) — DT_1DA9003S
    # URL 예시:
    #   ?itmId=T30+
    #   &objL1=01+05+10+...+99+  (산업)
    #   &objL2=
    #   &orgId=101&tblId=DT_1DA9003S
    KosisDataset(
        variable_name   = "employed_by_industry",
        org_id          = "101",
        tbl_id          = "DT_1DA9003S",
        adjustment_type = "seasonal",
        description     = "산업별 계절조정 취업자 (계절조정)",
        tbl_nm          = "산업별 계절조정 취업자",
        available_from  = "2013-01",
        itm_id          = EMP_ITEM_EMPLOYED_ONLY,   # T30만
        obj_l1          = INDUSTRY_CODES_EMP,        # 01+05+...+99+
        obj_l2          = "",
        source          = "kosis_emp",
    ),

    # ⑥ 종사상지위별 취업자 (원계열) — DT_1DA7010S
    # 종사상지위 분류코드는 메타데이터 미제공 → ALL로 전체 수집
    KosisDataset(
        variable_name   = "employed_by_work_status",
        org_id          = "101",
        tbl_id          = "DT_1DA7010S",
        adjustment_type = "raw",
        description     = "종사상지위별 취업자 (원계열)",
        tbl_nm          = "종사상지위별 취업자",
        available_from  = "1963-01",
        itm_id          = EMP_ITEM_EMPLOYED_ONLY,   # T30만
        obj_l1          = "ALL",                    # 종사상지위 코드 미제공
        obj_l2          = "",
        source          = "kosis_emp",
    ),

    # ⑦ 성/종사상지위별 취업자 (원계열) — DT_1DA7028S
    KosisDataset(
        variable_name   = "employed_by_sex_work_status",
        org_id          = "101",
        tbl_id          = "DT_1DA7028S",
        adjustment_type = "raw",
        description     = "성/종사상지위별 취업자 (원계열)",
        tbl_nm          = "성/종사상지위별 취업자",
        available_from  = "1963-01",
        itm_id          = EMP_ITEM_EMPLOYED_ONLY,   # T30만
        obj_l1          = SEX_CODES_GENERAL,         # 0+1+2+
        obj_l2          = "ALL",                    # 종사상지위 코드 미제공
        source          = "kosis_emp",
    ),

    # ⑧ 종사상지위별 계절조정 취업자 (계절조정) — DT_1DA9006S
    KosisDataset(
        variable_name   = "employed_by_work_status",
        org_id          = "101",
        tbl_id          = "DT_1DA9006S",
        adjustment_type = "seasonal",
        description     = "종사상지위별 계절조정 취업자 (계절조정)",
        tbl_nm          = "종사상지위별 계절조정 취업자",
        available_from  = "1989-01",
        itm_id          = EMP_ITEM_EMPLOYED_ONLY,   # T30만
        obj_l1          = "ALL",                    # 종사상지위 코드 미제공
        obj_l2          = "",
        source          = "kosis_emp",
    ),
]


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# KOSIS_WAGE_DATASETS  (사업체노동력조사 / orgId=118)
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
KOSIS_WAGE_DATASETS: list[KosisDataset] = [

    # ⑨ 산업/규모별 임금 및 근로시간 (원계열) — DT_118N_MON051
    # URL 예시:
    #   ?itmId=13103110311MD_12+13103110311MD_13+...+
    #   &objL1=190326INDUSTRY_10S0+...+190326INDUSTRY_10SS+  (산업)
    #   &objL2=size01+size02+...+size14+  (규모)
    #   &orgId=118&tblId=DT_118N_MON051
    KosisDataset(
        variable_name   = "wage_and_hours_by_industry_size",
        org_id          = "118",
        tbl_id          = "DT_118N_MON051",
        adjustment_type = "raw",
        description     = "산업/규모별 임금 및 근로시간 (원계열)",
        tbl_nm          = "산업/규모별 임금 및 근로시간",
        available_from  = "2020-01",
        itm_id          = WAGE_ITEM_CODES,          # 임금·근로시간 6종
        obj_l1          = INDUSTRY_CODES_SE,         # 사업체 산업코드
        obj_l2          = SIZE_CODES_SE,             # 규모코드
        source          = "kosis_wage",
    ),
]


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# KOSIS_BENEFIT_DATASETS  (고용행정통계 / orgId=118)
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
KOSIS_BENEFIT_DATASETS: list[KosisDataset] = [

    # ⑩ 구직급여 신청 동향 (원계열) — DT_11844N_7374_Z
    # URL 예시:
    #   ?itmId=T001+T002+T003+T004+
    #   &objL1=ALL
    #   &orgId=118&tblId=DT_11844N_7374_Z
    KosisDataset(
        variable_name   = "job_seeker_trend",
        org_id          = "118",
        tbl_id          = "DT_11844N_7374_Z",
        adjustment_type = "raw",
        description     = "구직급여 신청 동향 (신청자·지급자·지급액·건수)",
        tbl_nm          = "구직급여 신청 동향",
        available_from  = "2015-01",
        itm_id          = BENEFIT_ITEM_CODES,       # T001~T004
        obj_l1          = "ALL",
        obj_l2          = "",
        source          = "kosis_benefit",
    ),
]


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# API 호출
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
def _effective_start(available_from: str, period_start: str) -> str:
    """수록 시작과 요청 시작 중 더 늦은 쪽을 반환 (수록 기간 외 요청 방어)."""
    return max(available_from, period_start)


def _call_kosis_api(
    ds:           KosisDataset,
    period_start: str,
    period_end:   str,
    retries:      int   = 3,
    backoff:      float = 2.0,
) -> list[dict]:
    """
    KOSIS 통계 데이터 조회 API 단건 호출.

    파라미터 우선순위
    ─────────────────────────────────────────────────────────────────
    - itm_id  : 항목코드 문자열 ("T10+T20+..." 형식)
    - obj_l1  : 분류1 ("0+1+2+" 또는 "ALL")
    - obj_l2  : 분류2 (빈 문자열이면 파라미터에서 제외하지 않고 빈값 전달)
    - prdSe   : M (월별 고정)
    - startPrdDe / endPrdDe : YYYYMM 형식

    Returns
    -------
    list[dict] : 원시 응답 레코드 목록. 오류·스킵이면 [].
    """
    eff_start = _effective_start(ds.available_from, period_start)
    if eff_start > period_end:
        logger.info(
            f"  [skip] {ds.tbl_id} — 수록시작({ds.available_from}) > "
            f"수집종료({period_end})"
        )
        return []

    params: dict = {
        "method":     "getList",
        "apiKey":     KOSIS_API_KEY,
        "orgId":      ds.org_id,
        "tblId":      ds.tbl_id,
        "itmId":      ds.itm_id,
        "objL1":      ds.obj_l1,
        "objL2":      ds.obj_l2,
        "objL3":      "",
        "objL4":      "",
        "objL5":      "",
        "objL6":      "",
        "objL7":      "",
        "objL8":      "",
        "format":     "json",
        "jsonVD":     "Y",
        "prdSe":      "M",
        "startPrdDe": eff_start.replace("-", ""),
        "endPrdDe":   period_end.replace("-", ""),
    }

    for attempt in range(1, retries + 1):
        try:
            resp = requests.get(KOSIS_BASE_URL, params=params, timeout=30)
            resp.raise_for_status()
            data = resp.json()

            if isinstance(data, dict) and "err" in data:
                raise ValueError(
                    f"KOSIS 오류 (err={data.get('err')}): {data.get('errMsg', data)}"
                )
            return data if isinstance(data, list) else []

        except (requests.RequestException, ValueError) as exc:
            logger.warning(f"  [retry {attempt}/{retries}] {ds.tbl_id}: {exc}")
            if attempt < retries:
                time.sleep(backoff ** attempt)
            else:
                logger.error(f"  [fail] {ds.tbl_id} 최종 실패")
                raise

    return []


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 응답 정규화
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
def _normalize(
    raw_records: list[dict],
    ds:          KosisDataset,
    run_id:      str,
) -> list[dict]:
    """
    KOSIS 응답 레코드 → BigQuery 공통 스키마.

    KOSIS 응답 주요 필드
    ───────────────────────────────
    PRD_DE   기간 (YYYYMM)
    DT       값
    C1       분류1 코드 (예: 성별 '0')
    C1_NM    분류1 명   (예: '전체')
    C2       분류2 코드 (예: 연령대 '30')
    C2_NM    분류2 명   (예: '30 - 39세')
    C3       분류3 코드
    C3_NM    분류3 명
    ITM_ID   항목코드   (예: 'T30')
    ITM_NM   항목명     (예: '취업자')

    BQ 스키마
    ───────────────────────────────
    source / period / variable_name / category_key /
    value / adjustment_type / ingested_at / run_id /
    category_name / tbl_id
    """
    from datetime import datetime, timezone

    ingested_at = datetime.now(timezone.utc).isoformat()
    out: list[dict] = []

    for rec in raw_records:
        prd = rec.get("PRD_DE", "")
        if len(prd) != 6 or not prd.isdigit():
            continue
        period = f"{prd[:4]}-{prd[4:]}"

        # category_key: 분류1|분류2|분류3|항목코드
        cat_parts = [
            rec.get("C1",     ""),
            rec.get("C2",     ""),
            rec.get("C3",     ""),
            rec.get("ITM_ID", ""),
        ]
        category_key = "|".join(p for p in cat_parts if p) or "total"

        # category_name: 한국어 레이블 (참고용)
        name_parts = [
            rec.get("C1_NM",  ""),
            rec.get("C2_NM",  ""),
            rec.get("C3_NM",  ""),
            rec.get("ITM_NM", ""),
        ]
        category_name = " | ".join(p for p in name_parts if p)

        raw_val = rec.get("DT", "")
        try:
            value = (
                float(str(raw_val).replace(",", ""))
                if raw_val not in ("", "-", None)
                else None
            )
        except ValueError:
            value = None

        out.append({
            "source":          ds.source,
            "period":          period,
            "variable_name":   f"{ds.adjustment_type}_{ds.variable_name}",
            "category_key":    category_key,
            "value":           value,
            "adjustment_type": ds.adjustment_type,
            "ingested_at":     ingested_at,
            "run_id":          run_id,
            "category_name":   category_name,
            "tbl_id":          ds.tbl_id,
        })

    return out


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 단일 데이터셋 수집
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
def fetch_kosis_dataset(
    ds:           KosisDataset,
    period_start: str,
    period_end:   str,
    run_id:       str,
) -> list[dict]:
    logger.info(
        f"[KOSIS] {ds.adjustment_type:8s} | {ds.tbl_id:22s} | {ds.description}"
    )
    try:
        raw     = _call_kosis_api(ds, period_start, period_end)
        records = _normalize(raw, ds, run_id)
        logger.info(f"         → {len(records):,}건")
        return records
    except Exception as exc:
        logger.error(f"         → 실패: {exc}")
        return []
    finally:
        time.sleep(0.5)  # KOSIS rate limit


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 전체 수집 진입점
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
def fetch_all_kosis(run_id: str) -> dict[str, list[dict]]:
    """
    KOSIS 전체 수집 실행 (3개 source 통합).

    Returns
    -------
    {
        "kosis_emp":     list[dict],   # 경제활동인구조사 8개 테이블
        "kosis_wage":    list[dict],   # 사업체노동력조사 1개 테이블
        "kosis_benefit": list[dict],   # 고용행정통계 1개 테이블
    }
    """
    period_start, period_end = get_env_periods()
    output: dict[str, list[dict]] = {
        "kosis_emp":     [],
        "kosis_wage":    [],
        "kosis_benefit": [],
    }

    groups = [
        ("경제활동인구조사", KOSIS_EMP_DATASETS,     "kosis_emp"),
        ("사업체노동력조사", KOSIS_WAGE_DATASETS,    "kosis_wage"),
        ("고용행정통계",    KOSIS_BENEFIT_DATASETS,  "kosis_benefit"),
    ]

    for survey_nm, datasets, source_key in groups:
        logger.info("=" * 65)
        logger.info(
            f"[KOSIS] {survey_nm} 수집 시작 "
            f"({len(datasets)}개 테이블) | {period_start} ~ {period_end}"
        )
        for ds in datasets:
            output[source_key].extend(
                fetch_kosis_dataset(ds, period_start, period_end, run_id)
            )

    logger.info("=" * 65)
    logger.info(
        f"[KOSIS] 수집 완료 — "
        + " / ".join(f"{k}: {len(v):,}건" for k, v in output.items())
    )
    return output


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 단독 실행 (디버그 / API 키 확인용)
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
if __name__ == "__main__":
    import json, os
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
        datefmt="%H:%M:%S",
    )
    os.environ.setdefault("PERIOD_START", "2025-01")
    os.environ.setdefault("PERIOD_END",   "2025-03")
    os.environ.setdefault("KOSIS_API_KEY", "YOUR_API_KEY_HERE")

    result = fetch_all_kosis(run_id="test-run-00000000")

    print("\n=== 결과 미리보기 (각 소스 첫 2건) ===")
    for source, records in result.items():
        print(f"\n[{source}] 총 {len(records):,}건")
        for r in records[:2]:
            print(json.dumps(r, ensure_ascii=False, indent=2))
