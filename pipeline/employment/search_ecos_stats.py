"""
check_ecos_rates.py — 월별 시장금리 통계표 확인
"""
import requests, os

key = os.environ.get("ECOS_API_KEY", "")
if not key:
    raise EnvironmentError("ECOS_API_KEY 환경변수가 설정되지 않았습니다.")

# 월별 금리 통계표 후보들
codes = {
    "817Y002": "시장금리(일별) - 현재 사용중",
    "817Y001": "시장금리 후보1",
    "817Y003": "시장금리 후보2",
    "817Y004": "시장금리 후보3",
}

for code, desc in codes.items():
    # 실제 데이터 조회로 주기 확인
    url = (f"https://ecos.bok.or.kr/api/StatisticSearch/{key}/json/kr"
           f"/1/3/{code}/M/202501/202502/010200000")
    try:
        r = requests.get(url, timeout=10).json()
        rows = r.get("StatisticSearch", {}).get("row", [])
        if rows:
            print(f"\n{code} [{desc}] → 월별 데이터 있음")
            for row in rows:
                print(f"  TIME={row.get('TIME','')}  VAL={row.get('DATA_VALUE','')}")
        else:
            result = r.get("RESULT", {})
            print(f"{code}: {result.get('CODE','')} {result.get('MESSAGE','')}")
    except Exception as e:
        print(f"  {code} 오류: {e}")