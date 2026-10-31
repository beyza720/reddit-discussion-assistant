import json
import os
from pathlib import Path


def markdown_text(value):
    # Keep untrusted titles/reasons from injecting Markdown links or HTML.
    text = str(value).replace('\n', ' ')
    for char in ('\\', '`', '*', '_', '[', ']', '<', '>', '#', '|'):
        text = text.replace(char, '\\' + char)
    return text


def write_reports(report, output):
    output = Path(output)
    output.mkdir(parents=True, exist_ok=True)
    lines = ['# Discussion review', '', f"Run type: **{report['run_type']}**",
             f"Source status: **{report['source_status']}**", '', report['limitation'], '',
             '## LLM data disclosure', '', report['llm_data_disclosure'], '',
             'No Reddit actions are implemented. Replies omit promotional links. Raw discussion bodies are not retained.', '']
    for entry in report['reviews']:
        lines += ['## ' + markdown_text(entry['title']), '', entry['url'], '',
                  'Published: ' + (entry['published_at'] or 'unknown'),
                  'Matched video: ' + markdown_text(entry['video_title']),
                  'Video URL (review reference only): ' + entry['video_url'], '',
                  'Decision: ' + markdown_text(entry['decision']), '',
                  'Relevance: ' + markdown_text(entry['relevance_explanation']), '',
                  'Draft status: ' + markdown_text(entry['draft_explanation']), '',
                  'Community rules: ' + markdown_text(entry['community_rules']), '',
                  'Suggested reply:', '', '```text', (entry['suggested_reply'] or '(none)').replace('```', "'''"), '```', '']
    lines += ['## Access errors', '']
    for error in report['access_errors']:
        lines += ['- ' + markdown_text(f"{error['code']}: {error['url']} — {error['detail']}")]
    lines += ['', '## Skipped discussions', '']
    for entry in report['skipped']:
        lines += ['- ' + markdown_text(f"{entry['url']} | {entry['video_id']} | {entry['reason']}")]
    # Files are for the operator, not a public hosted interface.
    for name, content in [('review.json', json.dumps(report, ensure_ascii=False, indent=2)),
                          ('review.md', '\n'.join(lines) + '\n')]:
        temporary = output / (name + '.tmp')
        with temporary.open('w', encoding='utf-8') as handle:
            handle.write(content)
        os.chmod(temporary, 0o600)
        temporary.replace(output / name)
