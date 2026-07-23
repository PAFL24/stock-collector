import os
import datetime
from supabase import create_client, Client
from OpenDartReader import OpenDartReader
from pykrx import stock

SUPABASE_URL = os.environ.get("SUPABASE_URL")
SUPABASE_KEY = os.environ.get("SUPABASE_KEY")
DART_API_KEY = os.environ.get("DART_API_KEY")

supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)
dart = OpenDartReader(DART_API_KEY)

def run():
    today_dt = datetime.datetime.now()
    today_str = today_dt.strftime("%Y%m%d")
    date_formatted = today_dt.strftime("%Y-%m-%d")
    current_year = today_dt.year - 1

    # 워치리스트 조회
    stocks = supabase.table("watchlist").select("*").execute().data

    for item in stocks:
        code = item['stock_code']
        try:
            # 1. 일별 시세 수집
            df_price = stock.get_market_ohlcv_by_date(today_str, today_str, code)
            close_price = int(df_price['종가'].iloc[0]) if not df_price.empty else None

            df_cap = stock.get_market_cap_by_date(today_str, today_str, code)
            market_cap = int(df_cap['시가총액'].iloc[0]) if not df_cap.empty else None

            df_fund = stock.get_market_fundamental_by_date(today_str, today_str, code)
            per = float(df_fund['PER'].iloc[0]) if not df_fund.empty else None
            pbr = float(df_fund['PBR'].iloc[0]) if not df_fund.empty else None
            dividend_yield = float(df_fund['DIV'].iloc[0]) if not df_fund.empty else None

            df_foreign = stock.get_exhaustion_rates_of_foreign_investor_by_date(today_str, today_str, code)
            foreign_ratio = float(df_foreign['지분율'].iloc[0]) if not df_foreign.empty else None

            # 2. DART 재무 수집 (당기순이익, 자본총계)
            fin = dart.finstate(code, current_year, reprt_code='11011')
            net_income, total_equity, roe = None, None, None
            if fin is not None and not fin.empty:
                net_row = fin[fin['account_nm'].str.contains('당기순이익', na=False)]
                eq_row = fin[fin['account_nm'].str.contains('자본총계', na=False)]
                
                if not net_row.empty:
                    net_income = int(net_row['thstrm_amount'].iloc[0].replace(',', ''))
                if not eq_row.empty:
                    total_equity = int(eq_row['thstrm_amount'].iloc[0].replace(',', ''))
                if net_income and total_equity:
                    roe = round((net_income / total_equity) * 100, 2)

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

            supabase.table("daily_stock_metrics").upsert(payload, on_conflict="stock_code,date").execute()
            print(f"[{item['stock_name']}] 수집 성공")
        except Exception as e:
            print(f"[{code}] 오류 발생: {e}")

if __name__ == "__main__":
    run()
