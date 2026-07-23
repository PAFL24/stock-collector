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
    if not val or val == '-':
        return None
    try:
        return int(str(val).replace(',', '').strip())
    except:
        return None

def safe_float(val):
    if not val or val == '-':
        return None
    try:
        return float(str(val).replace(',', '').strip())
    except:
        return None

def fetch_naver_stock_data(code):
    """네이버 증권 API를 통해 주가 및 투자지표 수집"""
    url = f"https://m.stock.naver.com/api/stock/{code}/basic"
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    }
    
    res = requests.get(url, headers=headers)
    if res.status_code != 200:
        return {}
    
    data = res.json()
    
    # 주요 지표 추출
    close_price = safe_int(data.get("nowValue"))
    market_cap = safe_int(data.get("marketValue")) # 백만원 단위인 경우가 많음 (네이버 모바일 API 기준 원 단위 변환 확인)
    if market_cap:
        market_cap = market_cap * 1000000 # 억/백만원 단위 보정
        
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
        code = item.get('stock_code')
        name = item.get('stock_name', code)
        print(f"\n---> [{name} ({code})] 데이터 수집 시작...")
        
        # 1. 네이버 증권 데이터 수집
        stock_info = fetch_naver_stock_data(code)
        print(f"  └ 네이버 증권 수집 결과: 주가={stock_info.get('close_price')}, PER={stock_info.get('per')}, 외국인={stock_info.get('foreign_ratio')}%")

        # 2. DART 재무 수집 (당기순이익, 자본총계, ROE)
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

        time.sleep(0.5)

if __name__ == "__main__":
    run()
