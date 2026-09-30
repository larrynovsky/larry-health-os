import urllib.request, json
from datetime import date, timedelta

from secrets_paths import secrets_dir
token = (secrets_dir() / 'oura_token').read_text().strip()

def oura_get(endpoint, params=''):
    url = f'https://api.ouraring.com/v2/usercollection/{endpoint}?{params}'
    req = urllib.request.Request(url, headers={'Authorization': f'Bearer {token}'})
    with urllib.request.urlopen(req) as r:
        return json.loads(r.read())

start = (date.today() - timedelta(days=14)).isoformat()  # time-inject: ok
end = date.today().isoformat()  # time-inject: ok

# Daily sleep scores
print('=== DAILY SLEEP SCORES ===')
sleep = oura_get('daily_sleep', f'start_date={start}&end_date={end}')
for s in sleep.get('data', []):
    c = s.get('contributors', {})
    print(f"{s['day']}: score={s.get('score')} | deep={c.get('deep_sleep')} rem={c.get('rem_sleep')} eff={c.get('efficiency')} timing={c.get('timing')} rest={c.get('restfulness')}")

print()

# Detailed sleep sessions
print('=== SLEEP SESSIONS ===')
sessions = oura_get('sleep', f'start_date={start}&end_date={end}')
for s in sessions.get('data', []):
    total = s.get('total_sleep_duration', 0)
    t_h = total // 3600
    t_m = (total % 3600) // 60
    deep = s.get('deep_sleep_duration', 0) // 60
    rem = s.get('rem_sleep_duration', 0) // 60
    light = s.get('light_sleep_duration', 0) // 60
    awake = s.get('awake_time', 0) // 60
    eff = s.get('sleep_efficiency', 0)
    hrv = s.get('average_hrv')
    hr = s.get('average_heart_rate')
    temp_dev = s.get('skin_temperature_delta')
    bedtime_start = s.get('bedtime_start', '')[:16]
    bedtime_end = s.get('bedtime_end', '')[:16]
    stype = s.get('type')
    print(f"{s['day']} ({stype}): {t_h}ч{t_m}м | deep={deep}м rem={rem}м light={light}м awake={awake}м | eff={eff}% hrv={hrv} hr={hr} temp_dev={temp_dev} | {bedtime_start} → {bedtime_end}")

print()

# Readiness
print('=== READINESS ===')
readiness = oura_get('daily_readiness', f'start_date={start}&end_date={end}')
for r in readiness.get('data', []):
    c = r.get('contributors', {})
    print(f"{r['day']}: score={r.get('score')} | hrv_bal={c.get('hrv_balance')} body_temp={c.get('body_temperature')} recovery_idx={c.get('recovery_index')} rhr={c.get('resting_heart_rate')}")
