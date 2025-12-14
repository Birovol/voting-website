import requests
from bs4 import BeautifulSoup

s = requests.Session()
base='http://127.0.0.1:5000'
print('GET /', s.get(base+'/').status_code)
print('GET /admin/login', s.get(base+'/admin/login').status_code)
# login
r = s.post(base+'/admin/login', data={'username':'admin','password':'secret'}, allow_redirects=False)
print('POST /admin/login ->', r.status_code, 'Location:', r.headers.get('Location'))
print('Cookies after login:', s.cookies.get_dict())
print('GET /admin', s.get(base+'/admin').status_code)
print('GET /admin/settings', s.get(base+'/admin/settings').status_code)
# change primary color
r = s.post(base+'/admin/settings', data={'site_title':'Тестовый сайт','banner_text':'Баннер','primary_color':'#ff0000','language':'ru'}, allow_redirects=False)
print('POST /admin/settings ->', r.status_code, 'Location:', r.headers.get('Location'))
# Add teacher
r = s.post(base+'/admin/add', data={'name':'Тест Учитель','subject':'ИСТ','description':'Описание'}, allow_redirects=False)
print('POST /admin/add ->', r.status_code)
# get teachers count and names in homepage
r = s.get(base+'/')
soup=BeautifulSoup(r.text,'html.parser')
names=[t.get_text(strip=True) for t in soup.select('.list-group-item .h4')]
print('Names in page (h4):', names[:5])
# find teacher id link from admin list
r2 = s.get(base+'/admin')
soup2=BeautifulSoup(r2.text,'html.parser')
a = soup2.find('a', href=True, text='Удалить')
if a:
    href=a['href']
    import re
    m=re.search(r'/admin/delete/(\d+)', href)
    if m:
        tid=int(m.group(1))
        print('found delete id', tid)
        # vote as new client
        s2=requests.Session()
        rvote = s2.post(base+f'/vote/{tid}', allow_redirects=False)
        print('POST /vote ->', rvote.status_code, 'Cookie set:', s2.cookies.get_dict())
else:
    print('No delete link found to extract id')

# --- Tests for change/unvote
lis = soup2.select('.list-group li')
ids = []
for li in lis:
    a_del = li.find('a', href=True, text='Удалить')
    if a_del:
        import re
        mm = re.search(r'/admin/delete/(\d+)', a_del['href'])
        if mm:
            ids.append(int(mm.group(1)))

if len(ids) >= 2:
    tid1, tid2 = ids[0], ids[1]
    print('Testing change/unvote with ids', tid1, tid2)
    # prefer to reuse s2 (if created earlier and voted) so we can change that vote; otherwise use a new session
    try:
        s_voter
    except NameError:
        s_voter = requests.Session()
    # baseline counts
    r_admin = s.get(base + '/admin')
    soup_admin = BeautifulSoup(r_admin.text, 'html.parser')
    def counts_map(soup):
        out = {}
        for li in soup.select('.list-group li'):
            name = li.select_one('strong').get_text(strip=True)
            votes = int(li.select_one('.mb-2 strong').get_text(strip=True))
            a_del = li.find('a', href=True, text='Удалить')
            import re
            mm = re.search(r'/admin/delete/(\d+)', a_del['href'])
            out[int(mm.group(1))] = votes
        return out
    before = counts_map(soup_admin)
    print('Before counts:', before)

    # vote for tid1 (if this session hasn't voted yet)
    r1 = s_voter.post(base + f'/vote/{tid1}', allow_redirects=False)
    print('POST /vote tid1 ->', r1.status_code, 'cookies:', s_voter.cookies.get_dict())
    r_admin = s.get(base + '/admin')
    after1 = counts_map(BeautifulSoup(r_admin.text, 'html.parser'))
    print('After voting tid1:', after1)

    # If vote was rejected due to IP uniqueness (no increment), simulate an existing voter by inserting a vote row directly
    if after1.get(tid1, 0) == before.get(tid1, 0):
        print('IP conflict prevented voting; inserting test voter row directly into DB to test change flow')
        import sqlite3
        conn = sqlite3.connect('instance/site.db')
        cur = conn.cursor()
        # create a test voter with a unique voter_id (use a different IP to avoid UNIQUE(ip,teacher_id) constraint)
        test_vid = 'testvoter_change_1'
        # ensure no leftover
        cur.execute('DELETE FROM votes WHERE voter_id = ?', (test_vid,))
        cur.execute('INSERT INTO votes (ip, teacher_id, voter_id) VALUES (?, ?, ?)', ('203.0.113.5', tid1, test_vid))
        cur.execute('UPDATE teachers SET votes = votes + 1 WHERE id = ?', (tid1,))
        conn.commit()
        conn.close()
        # use this cookie to perform change
        s_voter = requests.Session()
        s_voter.cookies.set('voter_id', test_vid)
        print('Inserted test voter and set cookie:', test_vid)

    # change vote to tid2
    r2 = s_voter.post(base + f'/vote/{tid2}', allow_redirects=False)
    print('POST /vote tid2 (change) ->', r2.status_code)
    r_admin = s.get(base + '/admin')
    after2 = counts_map(BeautifulSoup(r_admin.text, 'html.parser'))
    print('After changing to tid2:', after2)

    # unvote tid2
    r3 = s_voter.post(base + f'/unvote/{tid2}', allow_redirects=False)
    print('POST /unvote tid2 ->', r3.status_code)
    r_admin = s.get(base + '/admin')
    after3 = counts_map(BeautifulSoup(r_admin.text, 'html.parser'))
    print('After unvote tid2:', after3)
else:
    print('Not enough teachers to test change/unvote (need >=2)')
