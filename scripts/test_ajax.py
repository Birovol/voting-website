import requests
s = requests.Session()
url = 'http://127.0.0.1:5000/vote/2'
r = s.post(url, headers={'X-Requested-With': 'XMLHttpRequest','Accept':'application/json'})
print('Status', r.status_code)
try:
    print('JSON:', r.json())
except Exception as e:
    print('Non-JSON response', e)
# second attempt (new session)
s2 = requests.Session()
r2 = s2.post(url, headers={'X-Requested-With': 'XMLHttpRequest','Accept':'application/json'})
print('Second Status', r2.status_code)
try:
    print('Second JSON:', r2.json())
except Exception as e:
    print('Second non-JSON', e)
# unvote without voter_id
r3 = s2.post('http://127.0.0.1:5000/unvote/2', headers={'X-Requested-With': 'XMLHttpRequest','Accept':'application/json'})
print('Unvote without cookie:', r3.status_code)
try:
    print('Unvote JSON:', r3.json())
except Exception as e:
    print('Unvote non-JSON', e)
