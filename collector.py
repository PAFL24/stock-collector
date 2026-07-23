import os
import time
import datetime
import requests
from bs4 import BeautifulSoup
from supabase import create_client, Client
import OpenDartReader as OpenDartReader

SUPABASE_URL = os.environ.get("SUPABASE_URL")
SUPABASE_KEY = os.environ.get("SUPABASE_KEY")
DART_API_KEY = os.environ.get("DART_API_KEY")

supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)
dart = OpenDartReader(DART_API_KEY)

def safe_int(val):
    if val is None or val == '-' or val == '':
        return None
    try:
        clean = str(val).replace(',', '').replace('원', '').replace('억', '').strip()
        return int(clean)
    except:
        return None

def safe_float(val):
    if val is None or val == '-' or val == '':
        return None
    try:
        clean = str(val).replace(',', '').replace('%', '').replace('배', '').strip()
        return float(clean)
    except:
        return None

def fetch_naver_finance(code):
    formatted_code = str(code).zfill(6)
    url = f"https://finance.naver.com/item/main.naver?code={formatted_code}"
    
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
        "Accept-Language": "ko-KR,ko;q=0.9,en-US;q=0.8,en;q=0.7"
    }
    
    try:
        res = requests.get(url, headers=headers, timeout=10)
        if res.status_code != 200:
            print(f"  └ HTTP 오류: {res.status_code}")
            return {}
            
        soup = BeautifulSoup(res.text, 'html.parser')
        
        close_price, market_cap, per, pbr, dividend_yield, foreign_ratio = None, None, None, None, None, None
        
        # 1. 현재가 파싱
        today_div = soup.select_one("p.no_today span.today")
        if today_div:
            blind_span = today_div.select_one("span.blind")
            if blind_span:
                close_price = safe_int(blind_span.text)
        
        if not close_price:
            no_today = soup.select_one("p.no_today")
            if no_today:
                blind_spans = no_today.select("span.blind")
                if blind_spans:
                    close_price = safe_int(blind_spans[0].text)

        # 2. 시가총액 파싱 (억 단위 -> 원 단위)
        market_cap_elem = soup.select_one("#_market_sum")
        if market_cap_elem:
            cap_raw = market_cap_elem.text.replace('\n', '').replace('\t', '').replace(',', '').strip()
            if '조' in cap_raw:
                parts = cap_raw.split('조')
                cho = safe_int(parts[0]) or 0
                eok = safe_int(parts[1]) or 0
                market_cap = (cho * 10000 + eok) * 100000000
            else:
                eok = safe_int(cap_raw) or 0
                market_cap = eok * 100000000

        # 3. PER, PBR 파싱
        per_elem = soup.select_one("#_per")
        if per_elem:
            per = safe_float(per_elem.text)
            
        pbr_elem = soup.select_one("#_pbr")
        if pbr_elem:
            pbr = safe_float(pbr_elem.text)
            
        # 4. 배당수익률 (#_dvd_yield 우선 탐색 후, 우측/하단 테이블 탐색)
        dvd_elem = soup.select_one("#_dvd_yield")
        if dvd_elem:
            dividend_yield = safe_float(dvd_elem.text)
            
        # 5. 우측 우측 투자정보 테이블 반복 탐색 (외국인소진율, 배당수익률 보완)
        for tr in soup.select("div.aside_invest_info table tr"):
            tr_text = tr.text.strip()
            if "외국인소진율" in tr_text:
                td = tr.select_one("td")
                if td:
                    foreign_ratio = safe_float(td.text)
            elif "추정배당수익률" in tr_text or "배당수익률" in tr_text:
                if not dividend_yield:
                    td = tr.select_one("td")
                    if td:
                        em = td.select_one("em")
                        val_str = em.text if em else td.text
                        dividend_yield = safe_float(val_str)

        return {
            "close_price": close_price,
            "market_cap": market_cap,
            "per": per,
            "pbr": pbr,
            "dividend_yield": dividend_yield,
            "foreign_ratio": foreign_ratio
        }
    except Exception as e:
        print(f"  └ 네이버 스크래핑 오류: {e}")
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
        code = str(raw_code).zfill(6)
        name = item.get('stock_name', code)
        
        print(f"\n---> [{name} ({code})] 데이터 수집 시작...")
        
        # 1. 네이버 증권 데이터 수집
        stock_info = fetch_naver_finance(code)
        print(f"  └ 수집 결과: 주가={stock_info.get('close_price')}원, PER={stock_info.get('per')}, PBR={stock_info.get('pbr')}, 배당수익률={stock_info.get('dividend_yield')}%, 외국인={stock_info.get('foreign_ratio')}%")

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
