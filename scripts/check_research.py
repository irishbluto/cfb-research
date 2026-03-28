import json, glob, os

files = sorted(glob.glob('research/*_latest.json'))
lines = ['Files: ' + str(len(files)), '']

for f in files:
    if f.endswith('test.txt'):
        continue
    d = json.load(open(f))
    team = d.get('team', '?')
    yt   = len(d.get('youtube_findings', []))
    bc   = len(d.get('beat_coverage', []))
    ks   = len(d.get('key_storylines', []))
    sent = d.get('overall_sentiment', '?')
    summ = d.get('agent_summary', '')[:80]
    lines.append(team + ' | YT:' + str(yt) + ' BC:' + str(bc) + ' KS:' + str(ks) + ' | ' + sent)
    lines.append('  ' + summ)
    lines.append('')

out = 'logs/quality_check.txt'
open(out, 'w').write('\n'.join(lines))
os.system('cat ' + out)
