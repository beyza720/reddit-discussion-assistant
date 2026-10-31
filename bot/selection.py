import re
from datetime import timedelta

from .models import utcnow

STOP = {'the', 'and', 'this', 'that', 'with', 'from', 'what', 'does', 'would', 'could',
        'have', 'your', 'their', 'there', 'about', 'into', 'when', 'which', 'they', 'some',
        'how', 'why', 'are', 'can', 'for', 'but', 'not', 'its', 'you', 'our', 'has', 'was',
        'will', 'than', 'also', 'only', 'more', 'been', 'were', 'such', 'through'}


def tokens(text):
    return {t for t in re.findall(r'[a-z0-9]+', text.lower()) if len(t) >= 3 and t not in STOP}


def assess(discussion, video, max_age_days, now=None):
    now = now or utcnow()
    if discussion.locked is True or discussion.archived is True:
        return False, 'Thread is locked or archived.', []
    if discussion.locked is None or discussion.archived is None:
        return False, 'Thread status is unknown; it is not verified open.', []
    if discussion.published_at is None:
        return False, 'Publication date is unknown or has no timezone.', []
    if discussion.published_at > now + timedelta(minutes=5):
        return False, 'Publication date is in the future.', []
    if discussion.published_at < now - timedelta(days=max_age_days):
        return False, 'Discussion is older than the configured limit.', []
    if not discussion.post_text.strip():
        return False, 'Public post text is unavailable.', []
    question = discussion.title + '\n' + discussion.post_text
    if '?' not in question and not re.search(r'\b(how|why|what|explain|help|understand)\b', question, re.I):
        return False, 'No substantive question was identified.', []
    evidence = []
    target = tokens(question)
    keyword_tokens = tokens(' '.join(video.keywords))
    for sentence in re.split(r'(?<=[.!?])\s+|\n+', video.transcript_or_summary):
        overlap = tokens(sentence) & target
        if len(overlap) >= 2 and overlap & keyword_tokens:
            evidence.append(sentence.strip())
    if not evidence:
        return False, 'No supporting passage in the supplied video content; keyword overlap alone is insufficient.', []
    return True, ('Potential content match: the supplied factual passage shares specific concepts with the question. '
                  'This lexical evidence gate is not semantic verification; human review is required.'), evidence[:3]
