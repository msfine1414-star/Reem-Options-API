from fastapi import FastAPI, Query
from fastapi.middleware.cors import CORSMiddleware
import yfinance as yf
import pandas as pd
from datetime import datetime

# 1. إنشاء تطبيق السيرفر وتوثيق الحقوق لريم
app = FastAPI(
    title="Reem Saleh Options Open Interest API",
    description="سيرفر ريم صالح الرسمي لتوزيع بيانات الأوبن إنترست الحقيقية لمنصات الشارتات",
    version="1.0"
)

# تفعيل خاصية الـ CORS لكي يتمكن TradingView أو أي موقع من سحب البيانات بدون حظر أمني
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"], 
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

def is_third_friday(date_str):
    d = datetime.strptime(date_str, '%Y-%m-%d')
    return d.weekday() == 4 and 15 <= d.day <= 21

def get_options_data(symbol: str):
    symbol = symbol.upper().strip()
    ticker = yf.Ticker(symbol)
    
    try:
        expirations = list(ticker.options)
    except:
        return None
        
    if not expirations:
        return None
        
    opex_dates = [d for d in expirations if is_third_friday(d)][:3]
    
    def classify_expiry(exp_date_str):
        d = datetime.strptime(exp_date_str, '%Y-%m-%d').date()
        today = datetime.now().date()
        days_to_expiry = (d - today).days
        
        if exp_date_str in opex_dates:
            idx = opex_dates.index(exp_date_str)
            return f"Op{idx+1}" # اختصارات رشيقة تناسب الـ API
        elif days_to_expiry <= 0:
            return "0D"
        elif days_to_expiry == 1:
            return "1D"
        else:
            return "Wk"

    all_data = []
    for exp_date in expirations[:20]:
        try:
            opt = ticker.option_chain(exp_date)
            expiry_type = classify_expiry(exp_date)
            
            # الكول
            c_df = opt.calls[['strike', 'openInterest']].copy()
            c_df['Type'] = expiry_type
            c_df['OptType'] = 'Call'
            all_data.append(c_df)
            
            # البوت
            p_df = opt.puts[['strike', 'openInterest']].copy()
            p_df['Type'] = expiry_type
            p_df['OptType'] = 'Put'
            all_data.append(p_df)
        except:
            continue
            
    if not all_data:
        return None
        
    df = pd.concat(all_data, ignore_index=True)
    df = df.dropna(subset=['openInterest'])
    
    # فلترة أعلى 7 مستويات
    df['Rank'] = df.groupby(['Type', 'OptType'])['openInterest'].rank(method='first', ascending=False).astype(int)
    top_strikes = df[df['Rank'] <= 7].copy()
    
    # في السيرفر سنعطي التغير كقيمة صفرية أو نربطها بقاعدة بيانات لاحقاً، تهمنا الأرقام الحقيقية الحالية حالياً
    top_strikes['Chg'] = 0
    
    # تحويل الجدول إلى قاموس برميجي (JSON) نظيف ومفرز
    result = []
    for _, row in top_strikes.iterrows():
        result.append({
            "strike": float(row['strike']),
            "oi": int(row['openInterest']),
            "type": str(row['Type']),
            "opt_type": str(row['OptType']),
            "chg": int(row['Chg']),
            "rank": int(row['Rank'])
        })
    return result

# 2. إنشاء نقطة الاتصال (Endpoint) التي سيطلبها التداول وعملاؤكِ
@app.get("/api/options")
def fetch_options_api(symbol: str = Query(..., description="رمز السهم المطلوب مثل NVDA أو SPY")):
    data = get_options_data(symbol)
    if data is None:
        return {"status": "error", "message": f"فشل جلب البيانات للرمز {symbol}"}
    return {"status": "success", "symbol": symbol.upper(), "data": data}

# نقطة ترحيبية للتأكد من عمل السيرفر
@app.get("/")
def home():
    return {"message": "سيرفر ريم صالح لبيانات الأوبشن يعمل بنجاح وكفاءة!"}