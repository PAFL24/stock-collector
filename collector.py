import os
import datetime
from supabase import create_client, Client
import OpenDartReader as OpenDartReader
from pykrx import stock

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

def get_latest_trading_date():
    today = datetime.datetime.now()
    for i in range(10):
        target_dt = today - datetime.timedelta(days=i)
        target_str = target_dt.strftime("%Y%m%d")
        try:
            df = stock.get_market_ohlcv_by_date(target_str, target_str, "005930")
            if not df.empty and df['종가'].iloc[0] > 0:
                return target_str, target_dt.strftime("%Y-%m-%d")
        except:
            continue
    return today.strftime("%Y%m%d"), today.strftime("%Y-%m-%d")

def run():
    today_str, date_formatted = get_latest_trading_date()
    current_year = datetime.datetime.now().year - 1
    print(f"기준 거래일: {date_formatted} ({today_str})")

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
        
        close_price, market_cap, per, pbr, dividend_yield, foreign_ratio = None, None, None, None, None, None

        # 1. 시세 및 시가총액 수집
        try:
            df_price = stock.get_market_ohlcv_by_date(today_str, today_str, code)
            if not df_price.empty:
                close_price = int(df_price['종가'].iloc[0])
        except Exception as e:
            print(f"  └ 주가 수집 실패: {e}")

        try:
            df_cap = stock.get_market_cap_by_date(today_str, today_str, code)
            if not df_cap.empty:
                market_cap = int(df_cap['시가총액'].iloc[0])
        except Exception as e:
            print(f"  └ 시가총액 수집 실패: {e}")

        # 2. 투자지표 (PER, PBR, 배당수익률) 수집
        try:
            df_fund = stock.get_market_fundamental_by_date(today_str, today_str, code)
            if not df_fund.empty:
                per = float(df_fund['PER'].iloc[0]) if 'PER' in df_fund and df_fund['PER'].iloc[0] != 0 else None
                pbr = float(df_fund['PBR'].iloc[0]) if 'PBR' in df_fund and df_fund['PBR'].iloc[0] != 0 else None
                dividend_yield = float(df_fund['DIV'].iloc[0]) if 'DIV' in df_fund else None
        except Exception as e:
            print(f"  └ 펀더멘털 수집 실패: {e}")

        # 3. 외국인 지분율 수집
        try:
            df_foreign = stock.get_exhaustion_rates_of_foreign_investor_by_ticker(today_str, today_str, code)
            if not df_foreign.empty and '지분율' in df_foreign:
                foreign_ratio = float(df_foreign['지분율'].iloc[0])
        except Exception as e:
            print(f"  └ 외국인 지분율 수집 실패: {e}")

        # 4. DART 재무 수집
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
            "close_price": close_price,
            "market_cap": market_cap,
            "per": per,
            "pbr": pbr,
            "dividend_yield": dividend_yield,
            "foreign_ratio": foreign_ratio,
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

if __name__ == "__main__":
    run()
