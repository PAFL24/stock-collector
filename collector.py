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
    if not val or val == '-':
        return None
    try:
        return int(str(val).replace(',', '').replace('원', '').strip())
    except:
        return None

def safe_float(val):
    if not val or val == '-':
        return None
    try:
        return float(str(val).replace(',', '').replace('%', '').strip())
    except:
        return None

def fetch_naver_finance_html(code):
    """네이버 증권 PC 웹페이지 스크래핑"""
    url = f"https://finance.naver.com/item/main.naver?code={code}"
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    }
    
    res = requests.get(url, headers=headers)
    if res.status_code != 200:
        return {}
    
    soup = BeautifulSoup(res.text, 'html.parser')
    
    close_price, market_cap, per, pbr, dividend_yield, foreign_ratio = None, None, None, None, None, None
    
    try:
        # 1. 현재가
        no_today = soup.find('p', {'class': 'no_today'})
        if no_today:
            close_price = safe_int(no_today.find('span', {'class': 'blind'}).text)

        # 2. 시가총액 (억 단위 -> 원 단위 변환)
        market_cap_elem = soup.find('id', {'id': '_market_sum'})
        if not market_cap_elem:
            # alternative selector
            for tr in soup.find_all('tr'):
                if '시가총액' in tr.text:
                    td = tr.find('td')
                    if td:
                        cap_str = td.text.replace('\n', '').replace('\t', '').replace(',', '').strip()
                        # 억 단위 파싱 예: 412조 5,119 -> 숫자 변환
                        raw_cap = safe_int(cap_str)
                        if raw_cap:
                            market_cap = raw_cap * 100000000
                    break
        else:
            raw_cap = safe_int(market_cap_elem.text)
            if raw_cap:
                market_cap = raw_cap * 100000000

        # 3. PER / PBR / 배당수익률 / 외국인소진율
        per_elem = soup.find('id', {'id': '_per'})
        if per_elem:
            per = safe_float(per_elem.text)

        pbr_elem = soup.find('id', {'id': '_pbr'})
        if pbr_elem:
            pbr = safe_float(pbr_elem.text)

        dvr_elem = soup.find('id', {'id': '_dvd_yield'})
        if dvr_elem:
            dividend_yield = safe_float(dvr_elem.text)

        # 우측 서브테이블 파싱 (PER, PBR, 외국인소진율)
        aside = soup.find('div', {'class': 'aside_invest_info'})
        if aside:
            table = aside.find('table')
            if table:
                for tr in table.find_all('tr'):
                    th = tr.find('th')
                    td = tr.find('td')
                    if th and td:
                        th_text = th.text.strip()
                        if '외국인한도소진율' in th_text or '외국인소진율' in th_text:
                            foreign_ratio = safe_float(td.text)
                        elif 'PER' in th_text and not per:
                            em = td.find('em')
                            if em:
                                per = safe_float(em.text)
                        elif 'PBR' in th_text and not pbr:
                            em = td.find('em')
                            if em:
                                pbr = safe_float(em.text)
    except Exception as e:
        print(f"  └ 파싱 중 오류: {e}")

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
        stock_info = fetch_naver_finance_html(code)
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

        time.sleep(0.5)

if __name__ == "__main__":
    run()
