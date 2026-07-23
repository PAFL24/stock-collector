import os
import time
import datetime
import requests
from supabase import create_client, Client
import OpenDartReader as OpenDartReader

SUPABASE_URL = os.environ.get("SUPABASE_URL")
SUPABASE_KEY = os.environ.get("SUPABASE_KEY")
DART_API_KEY = os.environ.get("DART_API_KEY")

supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)
dart = OpenDartReader(DART_API_KEY)

def safe_int(val):
    if val is None or val == '-':
        return None
    try:
        return int(str(val).replace(',', '').strip())
    except:
        return None

def safe_float(val):
    if val is None or val == '-':
        return None
    try:
        return float(str(val).replace(',', '').strip())
    except:
        return None

def fetch_stock_data_from_naver(code):
    # 6자리 자릿수 맞춤 (예: 5930 -> 005930)
    formatted_code = str(code).zfill(6)
    
    url = f"https://m.stock.naver.com/api/stock/{formatted_code}/basic"
    headers = {
        "User-Agent": "Mozilla/5.0 (iPhone; CPU iPhone OS 16_0 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/16.0 Mobile/15E148 Safari/604.1",
        "Referer": f"https://m.stock.naver.com/stock/{formatted_code}/total"
    }
    
    try:
        res = requests.get(url, headers=headers, timeout=10)
        if res.status_code != 200:
            print(f"  └ HTTP 요청 실패 (코드: {res.status_code})")
            return {}
        
        data = res.json()
        
        close_price = safe_int(data.get("nowValue"))
        
        # 시가총액 (백만원 단위 -> 원 단위로 변환)
        raw_market_cap = safe_int(data.get("marketValue"))
        market_cap = raw_market_cap * 1000000 if raw_market_cap else None
        
        per = safe_float(data.get("per"))
        pbr = safe_float(data.get("pbr"))
        dividend_yield = safe_float(data.get("dividendYield"))
        foreign_ratio = safe_float(data.get("foreignRatio"))
                
        return {
            "close_price": close_price,
            "market_cap": market_cap,
            "per": per,
            "pbr": pbr,
            "dividend_yield": dividend_yield,
            "foreign_ratio": foreign_ratio
        }
    except Exception as e:
        print(f"  └ 네이버 API 호출 오류: {e}")
        return {}

def run():
    today_dt = datetime.datetime.now()
    date_formatted = today_dt.strftime("%Y-%m-%d")
    current_year = today_dt.year - 1
    
    print(f"수집 기준일: {date_formatted}")

    res = supabase.table("watchlist").select("*").execute()
    stocks = res.data if res else []
    print(f"조회된 종목 수: {len(stocks)}개")

    if not stocks:
        print("⚠️ 워치리스트에 종목이 없습니다.")
        return

    for item in stocks:
        raw_code = item.get('stock_code')
        # 무조건 6자리 문자열로 변환 (005930)
        code = str(raw_code).zfill(6)
        name = item.get('stock_name', code)
        
        print(f"\n---> [{name} ({code})] 데이터 수집 시작...")
        
        # 1. 네이버 증권 데이터 수집
        stock_info = fetch_stock_data_from_naver(code)
        print(f"  └ 수집 결과: 주가={stock_info.get('close_price')}원, PER={stock_info.get('per')}, PBR={stock_info.get('pbr')}, 외국인={stock_info.get('foreign_ratio')}%")

        # 2. DART 재무 수집
        net_income, total_equity, roe = None, None, None
        try:
            fin = dart.finstate(code, current_year, reprt_code='11011')
            if fin is not None and not fin.empty:
                net_row = fin[fin['account_nm'].str.contains('당기순이익', na=False)]
                eq_row = fin[fin['account_nm'].str.contains('자본총계', na=False)]
                
                if not net_row.empty:
                    net_income = safe_int(net_row['thstrm_amount'].iloc[0])
                if not eq_row.empty:
                    total_equity = safe_int(eq_row['thstrm_amount'].iloc[0])
                if net_income and total_equity and total_equity != 0:
                    roe = round((net_income / total_equity) * 100, 2)
        except Exception as dart_err:
            print(f"  └ DART 수집 경고: {dart_err}")

        payload = {
            "stock_code": code,
            "date": date_formatted,
            "close_price": stock_info.get("close_price"),
            "market_cap": stock_info.get("market_cap"),
            "per": stock_info.get("per"),
            "pbr": stock_info.get("pbr"),
            "dividend_yield": stock_info.get("dividend_yield"),
            "foreign_ratio": stock_info.get("foreign_ratio"),
            "net_income": net_income,
            "total_equity": total_equity,
            "roe": roe,
            "shareholder_return_rate": None
        }

        try:
            supabase.table("daily_stock_metrics").upsert(payload, on_conflict="stock_code,date").execute()
            print(f"✅ [{name}] Supabase 저장 성공!")
        except Exception as db_err:
            print(f"❌ [{name}] Supabase 저장 실패: {db_err}")

        time.sleep(1)

if __name__ == "__main__":
    run()
