#!/usr/bin/env python3
"""Render GitHub-native SVG widgets from public data. Python standard library only."""
import argparse
import collections
import datetime as dt
import html
import json
import math
import os
from pathlib import Path
import time
import urllib.error
import urllib.request

USER = 'yatimi'
REPO = 'yatimi/yatimi'
BRANCH = 'profile-widgets'
THEMES = {
    'dark': dict(bg='#0b101b', card='#121b2b', border='#26344d', ink='#eef4ff', muted='#95a6c1', purple='#b5a0ff', mint='#73e2c7', orange='#ffb280', grid='#1d2a40'),
    'light': dict(bg='#f3f6fc', card='#ffffff', border='#d9e1f0', ink='#18253d', muted='#596b85', purple='#7752cc', mint='#148575', orange='#b95927', grid='#e4eaf4'),
}

def api(path, payload=None, method=None):
    token = os.environ['GH_TOKEN']
    headers = {'Authorization': 'Bearer ' + token, 'Accept': 'application/vnd.github+json', 'User-Agent': 'yatimi-profile-widgets'}
    data = None if payload is None else json.dumps(payload).encode()
    req = urllib.request.Request('https://api.github.com/' + path, data=data, headers=headers, method=method)
    for attempt in range(3):
        try:
            with urllib.request.urlopen(req, timeout=45) as response:
                return json.load(response)
        except urllib.error.HTTPError as exc:
            if exc.code not in (429, 500, 502, 503, 504) or attempt == 2:
                raise
            time.sleep(2 ** (attempt + 1))


def collect():
    # Run with the repository-scoped GITHUB_TOKEN, which cannot read private work repos.
    now = dt.datetime.now(dt.timezone.utc)
    year_start = now.replace(month=1, day=1, hour=0, minute=0, second=0, microsecond=0)
    recent_start = (now - dt.timedelta(days=89)).replace(hour=0, minute=0, second=0, microsecond=0)
    query = '''query($login:String!, $yearStart:DateTime!, $recentStart:DateTime!, $to:DateTime!){user(login:$login){
      yearToDate:contributionsCollection(from:$yearStart,to:$to){
        totalCommitContributions totalPullRequestContributions
        contributionCalendar{weeks{contributionDays{date contributionCount weekday}}}}
      recent:contributionsCollection(from:$recentStart,to:$to){
        contributionCalendar{weeks{contributionDays{date contributionCount weekday}}}}
    }}'''
    result = api('graphql', {'query': query, 'variables': {
        'login': USER, 'yearStart': year_start.isoformat(),
        'recentStart': recent_start.isoformat(), 'to': now.isoformat()}})
    if result.get('errors'):
        raise RuntimeError('GitHub GraphQL could not return the complete public contribution data.')
    user = result['data']['user']
    repos, page = [], 1
    while True:
        batch = api(f'users/{USER}/repos?type=owner&per_page=100&page={page}')
        repos.extend(r for r in batch if not r['private'] and not r['fork'] and r['name'] != USER)
        if len(batch) < 100:
            break
        page += 1
    languages = collections.Counter()
    for repo in repos:
        languages.update(api(f'repos/{repo["full_name"]}/languages'))
    return {'collected': now.date().isoformat(), 'projects': len(repos),
            'languages': dict(languages), 'yearToDate': user['yearToDate'],
            'recent': user['recent']}


class SVG:
    def __init__(self, width, height, theme, title):
        self.c = THEMES[theme]
        self.parts = [f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}" role="img" aria-labelledby="title"><title id="title">{html.escape(title)}</title>',
          '<defs><linearGradient id="fill" x1="0" y1="0" x2="0" y2="1">'
          f'<stop stop-color="{self.c["mint"]}" stop-opacity=".25"/><stop offset="1" stop-color="{self.c["mint"]}" stop-opacity="0"/></linearGradient></defs>']
        self.rect(1, 1, width-2, height-2, self.c['bg'], 22, self.c['border'])
    def rect(self, x, y, w, h, fill, r=0, stroke='none'):
        self.parts.append(f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="{r}" fill="{fill}" stroke="{stroke}"/>')
    def text(self, x, y, value, size=14, color=None, weight=400, anchor='start'):
        self.parts.append(f'<text x="{x}" y="{y}" fill="{color or self.c["ink"]}" font-family="-apple-system,BlinkMacSystemFont,Segoe UI,Arial,sans-serif" font-size="{size}" font-weight="{weight}" text-anchor="{anchor}">{html.escape(str(value))}</text>')
    def line(self, x1, y1, x2, y2, color=None, width=1):
        self.parts.append(f'<path d="M{x1} {y1}L{x2} {y2}" fill="none" stroke="{color or self.c["border"]}" stroke-width="{width}"/>')
    def path(self, d, fill='none', stroke='none', width=1):
        self.parts.append(f'<path d="{d}" fill="{fill}" stroke="{stroke}" stroke-width="{width}" stroke-linejoin="round" stroke-linecap="round"/>')
    def circle(self, x, y, r, color):
        self.parts.append(f'<circle cx="{x}" cy="{y}" r="{r}" fill="{color}"/>')
    def finish(self):
        return ''.join(self.parts) + '</svg>\n'


def stat(s, x, y, w, value, label, accent):
    s.rect(x, y, w, 102, s.c['card'], 14, s.c['border'])
    s.rect(x+17, y+19, 4, 22, accent, 2)
    s.text(x+31, y+46, f'{value:,}', 31, weight=650)
    s.text(x+19, y+77, label, 12, s.c['muted'])


def activity(s, x, y, w, h, days):
    recent = days[-84:]
    # Weekly bins, anchored to the final visible day. All values come from GitHub.
    values = [sum(d['contributionCount'] for d in recent[i:i+7]) for i in range(0, len(recent), 7)]
    s.rect(x, y, w, h, s.c['card'], 16, s.c['border'])
    s.text(x+22, y+31, 'ACTIVITY / LAST 12 WEEKS', 12, s.c['muted'], 600)
    s.text(x+22, y+65, f'{sum(values):,} contributions', 23, weight=650)
    px, py, pw, ph = x+43, y+88, w-68, h-132
    top = max(4, math.ceil(max(values, default=0)/4)*4)
    for tick in range(3):
        ty = py+ph*(1-tick/2)
        s.line(px, ty, px+pw, ty, s.c['grid'])
        s.text(px-10, ty+4, round(top*tick/2), 10, s.c['muted'], anchor='end')
    pts = [(px+i*pw/max(1,len(values)-1), py+ph-v/top*ph) for i,v in enumerate(values)]
    if pts:
        path = 'M' + 'L'.join(f'{a:.1f} {b:.1f}' for a,b in pts)
        s.path(path+f'L{pts[-1][0]:.1f} {py+ph}L{px} {py+ph}Z', 'url(#fill)')
        s.path(path, stroke=s.c['mint'], width=2.5)
        for a,b in pts:
            s.circle(round(a,1),round(b,1),3,s.c['mint'])
    if recent:
        s.text(px, y+h-19, recent[0]['date'][5:], 11, s.c['muted'])
        s.text(px+pw, y+h-19, recent[-1]['date'][5:], 11, s.c['muted'], anchor='end')


def calendar(s, x, y, w, h, days):
    s.rect(x, y, w, h, s.c['card'], 16, s.c['border'])
    s.text(x+22, y+31, 'CONSISTENCY / LAST 90 DAYS', 12, s.c['muted'], 600)
    recent = days[-90:]
    active = sum(d['contributionCount'] > 0 for d in recent)
    s.text(x+22, y+55, f'{active} active days · brighter cells = more contributions', 11, s.c['muted'])
    if not recent:
        return
    first = dt.date.fromisoformat(recent[0]['date'])
    # GitHub calendars begin on Sunday; preserve weekday alignment at both edges.
    offset = (first.weekday()+1) % 7
    columns = math.ceil((offset+len(recent))/7)
    cell, gap = 11, 3
    step = cell+gap
    grid_width = columns*step-gap
    origin, top = x+(w-grid_width)/2+12, y+70
    for row, label in ((1, 'M'), (3, 'W'), (5, 'F')):
        s.text(origin-12, top+row*step+9, label, 9, s.c['muted'], anchor='end')
    maxcount = max((d['contributionCount'] for d in recent), default=0) or 1
    for d in recent:
        index = offset+(dt.date.fromisoformat(d['date'])-first).days
        cx, cy = origin+(index//7)*step, top+(index%7)*step
        count = d['contributionCount']
        s.parts.append(f'<g><title>{d["date"]}: {count} contributions</title>')
        s.rect(cx, cy, cell, cell, s.c['grid'], 2)
        if count:
            opacity = .35+.65*math.sqrt(count/maxcount)
            s.parts.append(f'<g opacity="{opacity:.2f}">')
            s.rect(cx, cy, cell, cell, s.c['mint'], 2)
            s.parts.append('</g>')
        s.parts.append('</g>')
    s.text(x+22, y+h-18, recent[0]['date'], 10, s.c['muted'])
    s.text(x+w-22, y+h-18, recent[-1]['date'], 10, s.c['muted'], anchor='end')


def language(s,x,y,w,h,languages):
    s.rect(x,y,w,h,s.c['card'],16,s.c['border'])
    s.text(x+22,y+31,'LANGUAGES / PUBLIC CODE',12,s.c['muted'],600)
    total=sum(languages.values())
    items=sorted(languages.items(),key=lambda p:p[1],reverse=True)[:3]
    if not total:
        s.text(x+22,y+76,'No public language data yet',16)
        return
    colors=[s.c['orange'],s.c['purple'],s.c['mint']]
    for i,(name,value) in enumerate(items):
        yy=y+66+i*43
        pct=value/total*100
        s.text(x+22,yy,name,14,weight=600)
        s.text(x+w-22,yy,f'{pct:.1f}%',13,s.c['muted'],anchor='end')
        s.rect(x+22,yy+10,w-44,5,s.c['grid'],2.5)
        s.rect(x+22,yy+10,(w-44)*pct/100,5,colors[i],2.5)
    s.text(x+22,y+h-18,'By bytes · excludes forks and profile repo',10,s.c['muted'])


def dashboard(data,theme,mobile=False):
    w,h=(500,1070) if mobile else (1000,755)
    s=SVG(w,h,theme,'Public GitHub activity for Artem Zabihailo, updated '+data['collected'])
    year = data['collected'][:4]
    cc = data['yearToDate']
    year_days = [d for week in cc['contributionCalendar']['weeks'] for d in week['contributionDays']
                 if f'{year}-01-01' <= d['date'] <= data['collected']]
    recent_start = (dt.date.fromisoformat(data['collected'])-dt.timedelta(days=89)).isoformat()
    days = sorted((d for week in data['recent']['contributionCalendar']['weeks'] for d in week['contributionDays']
                   if recent_start <= d['date'] <= data['collected']), key=lambda d: d['date'])
    s.circle(29,31,4,s.c['mint'])
    s.text(43,36,'OPEN-SOURCE ACTIVITY',13,s.c['muted'],600)
    s.text(25,77,'The work, in numbers.',30,weight=650)
    if not mobile:s.text(w-25,35,'UPDATED '+data['collected'],11,s.c['muted'],anchor='end')
    metrics=[(cc['totalCommitContributions'],f'Commits / {year}',s.c['mint']),
        (cc['totalPullRequestContributions'],f'PRs opened / {year}',s.c['purple']),
        (sum(d['contributionCount'] > 0 for d in year_days),f'Active days / {year}',s.c['orange']),
        (data['projects'],'Public projects',s.c['mint'])]
    gap=12; cw=(w-50-gap*(1 if mobile else 3))/(2 if mobile else 4)
    for i,(value,label,color) in enumerate(metrics):
        col=i%2 if mobile else i; row=i//2 if mobile else 0
        stat(s,25+col*(cw+gap),101+row*114,cw,value,label,color)
    ay=343 if mobile else 223
    activity(s,25,ay,w-50,260,days)
    if mobile:
        calendar(s,25,619,450,204,days)
        language(s,25,839,450,185,data['languages'])
        s.text(25,1050,'Public data · updated '+data['collected'],11,s.c['muted'])
    else:
        calendar(s,25,499,598,220,days)
        language(s,637,499,338,220,data['languages'])
        s.text(25,742,'Public GitHub data. Commercial work is described in the experience section.',10,s.c['muted'])
    return s.finish()


def publish(files):
    root=f'repos/{REPO}/git/'
    try:
        parent=api(root+'ref/heads/'+BRANCH)['object']['sha']
    except urllib.error.HTTPError as exc:
        if exc.code != 404: raise
        parent=None
    payload={'tree':[{'path':p.name,'mode':'100644','type':'blob','content':p.read_text()} for p in files]}
    if parent:
        previous=api(root+'commits/'+parent)
        payload['base_tree']=previous['tree']['sha']
    tree=api(root+'trees',payload)['sha']
    if parent and tree==previous['tree']['sha']:
        print('Widgets are unchanged.'); return
    commit=api(root+'commits',{'message':'Refresh public profile widgets','tree':tree,'parents':[parent] if parent else []})['sha']
    if parent:
        api(root+'refs/heads/'+BRANCH,{'sha':commit,'force':False},'PATCH')
    else:
        api(root+'refs',{'ref':'refs/heads/'+BRANCH,'sha':commit})
    print('Published widgets to '+BRANCH)


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--data',type=Path,help='Render an existing data snapshot locally')
    parser.add_argument('--output',type=Path,default=Path('widget-output'))
    parser.add_argument('--publish',action='store_true')
    args=parser.parse_args()
    if args.publish and args.data:
        parser.error('Only freshly collected GitHub data may be published.')
    data=json.loads(args.data.read_text()) if args.data else collect()
    args.output.mkdir(parents=True,exist_ok=True)
    files=[]
    for theme in THEMES:
        for mobile in (False,True):
            path=args.output/f'activity-{theme}{"-mobile" if mobile else ""}.svg'
            path.write_text(dashboard(data,theme,mobile)); files.append(path)
    if args.publish: publish(files)
    print('Rendered 4 theme-aware dashboard variants.')

if __name__=='__main__': main()
