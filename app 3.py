import os, sqlite3, threading, time, json, logging
from pathlib import Path
from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.responses import FileResponse
from model import fetch_candles, fit_latest, backtest, first_touch

logging.basicConfig(level=logging.INFO)
log=logging.getLogger('btc250')
ROOT=Path(__file__).parent
DATA=Path(os.environ.get('DATA_DIR',str(ROOT/'data')))
DATA.mkdir(parents=True,exist_ok=True)
DB=DATA/'forecasts.db'
state={'last_error':None,'last_update':None,'backtest':None,'price':None}
lock=threading.Lock()
stop=threading.Event()

def connect():
    con=sqlite3.connect(DB,timeout=20)
    con.row_factory=sqlite3.Row
    con.execute('PRAGMA journal_mode=WAL')
    return con

def initialize():
    with connect() as c:
        c.execute('''CREATE TABLE IF NOT EXISTS forecasts (
          id INTEGER PRIMARY KEY, created_at INTEGER NOT NULL, candle_ts INTEGER NOT NULL UNIQUE,
          start_price REAL NOT NULL, direction INTEGER NOT NULL, target_price REAL NOT NULL,
          p_up REAL, p_down REAL, p_neither REAL, status TEXT DEFAULT 'pending',
          actual INTEGER, resolved_at INTEGER, outcome TEXT)''')

def settle(df):
    now=int(time.time())
    with connect() as c:
        pending=c.execute("SELECT * FROM forecasts WHERE status='pending' ORDER BY candle_ts").fetchall()
        idx={int(ts):i for i,ts in enumerate(df.index)}
        for f in pending:
            if now < f['candle_ts']+16*60: continue
            i=idx.get(f['candle_ts'])
            if i is None: continue # Do not fabricate an outcome if history no longer available
            actual=first_touch(df,i,250,15)
            if actual is None: continue
            if actual==2: status='ambiguous';outcome='ambiguous'
            elif actual==f['direction']: status='resolved';outcome='win'
            elif actual==0: status='resolved';outcome='no_touch'
            else: status='resolved';outcome='loss'
            c.execute('UPDATE forecasts SET status=?,actual=?,resolved_at=?,outcome=? WHERE id=?',
                      (status,actual,now,outcome,f['id']))

def loop():
    last_backtest=0
    while not stop.is_set():
        try:
            df=fetch_candles(hours=72)
            settle(df)
            now=int(time.time());candle_ts=int(df.index[-1])
            with lock:
                state['price']=float(df.close.iloc[-1]);state['last_update']=now;state['last_error']=None
            # Forecast only on completed quarter-hour candle close; Coinbase candle timestamp is start of minute.
            # Minute ending at :00/:15/:30/:45 has candle timestamp :59/:14/:29/:44.
            forecast_due=((candle_ts//60+1)%15==0)
            if forecast_due and now-candle_ts < 180:
                with connect() as c:
                    exists=c.execute('SELECT 1 FROM forecasts WHERE candle_ts=?',(candle_ts,)).fetchone()
                if not exists:
                    result=fit_latest(df)
                    direction=result['direction'];price=result['price']
                    with connect() as c:
                        c.execute('INSERT OR IGNORE INTO forecasts(created_at,candle_ts,start_price,direction,target_price,p_up,p_down,p_neither) VALUES(?,?,?,?,?,?,?,?)',
                                  (now,candle_ts,price,direction,price+250*direction,result['p_up'],result['p_down'],result['p_neither']))
            if now-last_backtest>6*3600:
                report=backtest(df)
                with lock: state['backtest']={k:v for k,v in report.items() if k!='records'}
                last_backtest=now
        except Exception as e:
            log.exception('Update failed')
            with lock:state['last_error']=str(e)
        stop.wait(55)

@asynccontextmanager
async def lifespan(app):
    initialize();t=threading.Thread(target=loop,daemon=True);t.start()
    yield
    stop.set();t.join(timeout=3)

app=FastAPI(title='BTC $250 First Touch Research',lifespan=lifespan)

@app.get('/')
def home():return FileResponse(ROOT/'static'/'index.html')

@app.get('/api/health')
def health():return {'ok':True,'last_update':state['last_update'],'error':state['last_error']}

@app.get('/api/dashboard')
def dashboard():
    with connect() as c:
        rows=[dict(x) for x in c.execute('SELECT * FROM forecasts ORDER BY candle_ts DESC LIMIT 250').fetchall()]
        stats=c.execute("SELECT COUNT(*) n,SUM(CASE WHEN outcome='win' THEN 1 ELSE 0 END) wins,SUM(CASE WHEN outcome='no_touch' THEN 1 ELSE 0 END) no_touch,SUM(CASE WHEN outcome='loss' THEN 1 ELSE 0 END) losses FROM forecasts WHERE status='resolved'").fetchone()
        ambiguous=c.execute("SELECT COUNT(*) FROM forecasts WHERE status='ambiguous'").fetchone()[0]
    n=stats['n'] or 0;wins=stats['wins'] or 0
    with lock: snapshot=state.copy()
    return {'market':{'price':snapshot['price'],'updated_at':snapshot['last_update']},
            'live':{'resolved':n,'wins':wins,'losses':stats['losses'] or 0,'no_touch':stats['no_touch'] or 0,
                    'ambiguous':ambiguous,'win_rate':round(wins/n*100,2) if n else None},
            'backtest':snapshot['backtest'],'predictions':rows,'error':snapshot['last_error'],
            'rules':'Forecast every 15m. Starting price is latest completed 1m close. First $250 barrier touched in next 15 full minutes wins; opposite first or neither loses. Both in one minute = ambiguous, excluded.'}
