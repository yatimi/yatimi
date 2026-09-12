#!/usr/bin/env python3
"""Render GitHub-native SVG widgets from public data. Python standard library only."""
import argparse
import datetime as dt
import html
import json
import math
import os
from pathlib import Path
import time
import urllib.error
import urllib.request
import urllib.parse

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


def search_prs(date_filter):
    query = f'author:{USER} is:pr is:public -repo:{REPO} {date_filter}'
    items = []
    for page in range(1, 11):
        result = api('search/issues?' + urllib.parse.urlencode({
            'q': query, 'per_page': 100, 'page': page}))
        if result.get('incomplete_results') or result['total_count'] > 1000:
            raise RuntimeError('GitHub returned incomplete pull request search results.')
        items.extend(result['items'])
        if len(items) >= result['total_count']:
            return items
    raise RuntimeError('Could not collect all pull requests.')


def collect():
    now = dt.datetime.now(dt.timezone.utc)
    start = (now - dt.timedelta(days=29)).replace(hour=0, minute=0, second=0, microsecond=0)
    query = '''query($login:String!, $from:DateTime!, $to:DateTime!){user(login:$login){
      contributionsCollection(from:$from,to:$to){
        totalRepositoriesWithContributedCommits
        commitContributionsByRepository(maxRepositories:100){
          repository{nameWithOwner isPrivate}
          contributions(first:100){pageInfo{hasNextPage} nodes{occurredAt commitCount}}}
      }
    }}'''
    result = api('graphql', {'query': query, 'variables': {
        'login': USER, 'from': start.isoformat(), 'to': now.isoformat()}})
    if result.get('errors') or not result.get('data', {}).get('user'):
        raise RuntimeError('GitHub could not return complete contribution data.')
    cc = result['data']['user']['contributionsCollection']
    if cc['totalRepositoriesWithContributedCommits'] > 100:
        raise RuntimeError('Commit activity exceeds the repository collection limit.')
    days = {(start.date()+dt.timedelta(days=i)).isoformat(): 0 for i in range(30)}
    commits = 0
    for group in cc['commitContributionsByRepository']:
        repo = group['repository']
        # Keep the same public scope even when rendering with a personal token.
        # Profile maintenance must not inflate the activity of actual projects.
        if repo['isPrivate'] or repo['nameWithOwner'].lower() == REPO.lower():
            continue
        if group['contributions']['pageInfo']['hasNextPage']:
            raise RuntimeError('GitHub returned incomplete daily commit activity.')
        for contribution in group['contributions']['nodes']:
            date = contribution['occurredAt'][:10]
            if date in days:
                commits += contribution['commitCount']
                days[date] += contribution['commitCount']
    period = start.strftime('%Y-%m-%dT%H:%M:%SZ')+'..'+now.strftime('%Y-%m-%dT%H:%M:%SZ')
    opened = search_prs('created:'+period)
    merged = search_prs('is:merged merged:'+period)
    for pr in opened:
        date = pr['created_at'][:10]
        if date in days:
            days[date] += 1
    shipped = []
    for pr in merged:
        merged_at = pr['pull_request'].get('merged_at')
        if not merged_at:
            raise RuntimeError('GitHub omitted a merged pull request date.')
        shipped.append({'title': pr['title'], 'url': pr['html_url'],
                        'repo': '/'.join(pr['html_url'].split('/')[3:5]),
                        'number': pr['number'], 'mergedAt': merged_at})
    shipped.sort(key=lambda pr: (pr['mergedAt'], pr['url']), reverse=True)
    return {'collected': now.date().isoformat(), 'from': start.date().isoformat(),
            'commits': commits, 'opened': len(opened), 'merged': len(merged),
            'days': [{'date': date, 'count': count} for date, count in days.items()],
            'shipped': shipped[:3]}


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


def calendar(s, x, y, w, h, days):
    s.rect(x, y, w, h, s.c['card'], 16, s.c['border'])
    s.text(x+20, y+29, 'LAST 30 DAYS', 12, s.c['muted'], 600)
    s.text(x+20, y+49, 'Commits + PRs opened', 11, s.c['muted'])
    first = dt.date.fromisoformat(days[0]['date'])
    offset = (first.weekday()+1) % 7
    columns = math.ceil((offset+len(days))/7)
    cell, step = 12, 16
    origin, top = x+(w-(columns*step-4))/2+8, y+65
    for row, label in ((1, 'M'), (3, 'W'), (5, 'F')):
        s.text(origin-10, top+row*step+10, label, 9, s.c['muted'], anchor='end')
    maxcount = max(d['count'] for d in days) or 1
    for day in days:
        index = offset+(dt.date.fromisoformat(day['date'])-first).days
        cx, cy = origin+(index//7)*step, top+(index%7)*step
        count = day['count']
        s.parts.append(f'<g><title>{day["date"]}: {count} commits and PRs opened</title>')
        s.rect(cx, cy, cell, cell, s.c['grid'], 3)
        if count:
            s.parts.append(f'<g opacity="{.35+.65*math.sqrt(count/maxcount):.2f}">')
            s.rect(cx, cy, cell, cell, s.c['mint'], 3)
            s.parts.append('</g>')
        s.parts.append('</g>')
    s.text(x+20, y+h-17, days[0]['date'][5:]+' — '+days[-1]['date'][5:], 10, s.c['muted'])


def compact(value, limit):
    value = ' '.join(value.split())
    return value if len(value) <= limit else value[:limit-1].rstrip()+'…'


def shipped(s, x, y, w, h, items, mobile):
    s.rect(x, y, w, h, s.c['card'], 16, s.c['border'])
    s.text(x+20, y+29, 'RECENTLY SHIPPED', 12, s.c['muted'], 600)
    s.text(x+w-20, y+29, 'Merged PRs', 11, s.c['muted'], anchor='end')
    if not items:
        s.text(x+20, y+88, 'No merged PRs in the last 30 days.', 15)
        return
    for i, pr in enumerate(items):
        row = y+62+i*54
        s.circle(x+24, row-4, 4, s.c['purple'])
        s.text(x+38, row, compact(pr['title'], 43 if mobile else 62), 14, weight=600)
        label = compact(pr['repo'], 33 if mobile else 52)+' · #'+str(pr['number'])
        s.text(x+38, row+19, label, 11, s.c['muted'])
        s.text(x+w-20, row+19, pr['mergedAt'][:10], 10, s.c['muted'], anchor='end')
        if i < len(items)-1:
            s.line(x+20, row+31, x+w-20, row+31, s.c['grid'])


def dashboard(data, theme, mobile=False):
    w, h = (500, 710) if mobile else (1000, 490)
    s = SVG(w, h, theme, 'Currently building: public project activity in the last 30 days, updated '+data['collected'])
    s.circle(29, 30, 4, s.c['mint'])
    s.text(43, 35, 'PUBLIC PROJECT ACTIVITY', 12, s.c['muted'], 600)
    if not mobile:
        s.text(w-25, 35, data['from']+' — '+data['collected'], 11, s.c['muted'], anchor='end')
    s.text(25, 76, 'Currently building.', 30, weight=650)
    metrics = [(data['commits'], 'Commits', s.c['mint']),
               (data['merged'], 'PRs merged', s.c['purple']),
               (data['opened'], 'PRs opened', s.c['orange'])]
    cw = (w-74)/3
    for i, (value, label, color) in enumerate(metrics):
        stat(s, 25+i*(cw+12), 98, cw, value, label, color)
    if mobile:
        shipped(s, 25, 214, 450, 225, data['shipped'], True)
        calendar(s, 25, 453, 450, 216, data['days'])
    else:
        shipped(s, 25, 214, 650, 225, data['shipped'], False)
        calendar(s, 689, 214, 286, 225, data['days'])
    s.text(25, h-30, 'Last 30 days · public repos · excludes profile maintenance', 10, s.c['muted'])
    s.text(25, h-14, 'Open activity details and PR links ↗', 11, s.c['mint'], 600)
    return s.finish()


def activity_details(data):
    text = ['# Recent project activity', '',
            f"{data['from']} – {data['collected']} · public repositories · excludes profile maintenance.", '',
            f"**{data['commits']} commits · {data['merged']} PRs merged · {data['opened']} PRs opened**", '',
            '## Recently shipped', '']
    for pr in data['shipped']:
        # HTML escaping also prevents titles from introducing Markdown link syntax.
        title = html.escape(' '.join(pr['title'].split()), quote=True)
        url = html.escape(pr['url'], quote=True)
        text.append(f'<p><a href="{url}"><strong>{title}</strong></a><br />'
                    f'<sub>{html.escape(pr["repo"])} · #{pr["number"]} · merged {pr["mergedAt"][:10]}</sub></p>')
    if not data['shipped']:
        text.append('No merged PRs in the last 30 days.')
    text.extend(['', 'Commits follow GitHub contribution rules. The calendar counts commits and PRs opened. '
                 'Merged PRs include changes to the author’s own projects; merging does not necessarily mean a release.', '',
                 '[Back to profile](https://github.com/'+USER+')', ''])
    return '\n'.join(text)


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
    details = args.output/'recent.md'
    details.write_text(activity_details(data)); files.append(details)
    if args.publish: publish(files)
    print('Rendered 4 theme-aware dashboard variants.')

if __name__=='__main__': main()
