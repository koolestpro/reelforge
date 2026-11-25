#!/usr/bin/env python3
"""Sensitive-word pass for burned English captions.

Rule (2026-11-14): a caption may not carry a bare kill or murder word. Rewrite it
as a dignified paraphrase first. Fall back to masked spelling only when the
paraphrase would run past the two-line caption width. The Arabic of a verified
Qur'an card is never passed through this module. The English translation may be
masked for display; the verified source keeps the full wording.

    python3 sensitive_words.py captions.json --check
    python3 sensitive_words.py captions.json --rewrite out.json --max-lines 2
"""
from __future__ import annotations

import argparse
import json
import re
from pathlib import Path

MASK = '!'

WORD = re.compile(r'\b(kill(?:s|ed|ing)?|murder(?:s|ed|ing)?)\b', re.IGNORECASE)

MASKED = {
    'kill': 'k!ll',
    'kills': 'k!lls',
    'killed': 'k!lled',
    'killing': 'k!lling',
    'murder': 'm!rder',
    'murders': 'm!rders',
    'murdered': 'm!rdered',
    'murdering': 'm!rdering',
}

_OBJECT = re.compile(r'\bkill (him|her|them|us|me)\b', re.IGNORECASE)
_POSSESSIVE = {
    'him': 'his life',
    'her': 'her life',
    'them': 'their lives',
    'us': 'our lives',
    'me': 'my life',
}

PHRASES = [
    (re.compile(r'\bdeserves death\b', re.IGNORECASE), 'has earned that penalty'),
    (re.compile(r'\b(must|will|would|can|could|should|shall) not be killed\b', re.IGNORECASE),
     r'\1 not have his life taken'),
    (re.compile(r'\b(may|might) be killed\b', re.IGNORECASE), r'\1 have his life taken'),
    (re.compile(r'\bwas killed\b', re.IGNORECASE), 'had his life taken'),
    (re.compile(r'\bwere killed\b', re.IGNORECASE), 'had their lives taken'),
    (re.compile(r'\bbe killed\b', re.IGNORECASE), 'have his life taken'),
    (re.compile(r'\bkilling (a|an|the|every|someone|anyone|each|this|that|his|her|their|our|my)\b',
                re.IGNORECASE), r'taking the life of \1'),
    (re.compile(r'\bkills (a|an|the|every|someone|anyone|each|this|that|his|her|their|our|my)\b',
                re.IGNORECASE), r'takes the life of \1'),
    (re.compile(r'\bkilled (a|an|the|every|someone|anyone|each|this|that|his|her|their|our|my)\b',
                re.IGNORECASE), r'took the life of \1'),
    (re.compile(r'\bkill (a|an|the|every|someone|anyone|each|this|that|his|her|their|our|my)\b',
                re.IGNORECASE), r'take the life of \1'),
    (re.compile(r'\bthe killing\b', re.IGNORECASE), 'the taking of life'),
    (re.compile(r'\bkilling\b', re.IGNORECASE), 'taking of life'),
    (re.compile(r'\bkills\b', re.IGNORECASE), 'takes a life'),
    (re.compile(r'\bkilled\b', re.IGNORECASE), 'took a life'),
    (re.compile(r'\bkill\b', re.IGNORECASE), 'take a life'),
    (re.compile(r'\bmurdering (a|an|the|every|someone|anyone|each|this|that|his|her|their|our|my)\b',
                re.IGNORECASE), r'wrongfully taking the life of \1'),
    (re.compile(r'\bmurdered (a|an|the|every|someone|anyone|each|this|that|his|her|their|our|my)\b',
                re.IGNORECASE), r'wrongfully took the life of \1'),
    (re.compile(r'\bmurders (a|an|the|every|someone|anyone|each|this|that|his|her|their|our|my)\b',
                re.IGNORECASE), r'wrongfully takes the life of \1'),
    (re.compile(r'\bmurder (a|an|the|every|someone|anyone|each|this|that|his|her|their|our|my)\b',
                re.IGNORECASE), r'wrongfully take the life of \1'),
    (re.compile(r'\bmurdering\b', re.IGNORECASE), 'wrongfully taking a life'),
    (re.compile(r'\bmurdered\b', re.IGNORECASE), 'wrongfully took a life'),
    (re.compile(r'\bmurders\b', re.IGNORECASE), 'wrongfully takes a life'),
    (re.compile(r'\bmurder\b', re.IGNORECASE), 'the wrongful taking of a life'),
]


def _match_case(match, replacement):
    if match.group(0)[:1].isupper():
        return replacement[:1].upper() + replacement[1:]
    return replacement


def find(text):
    """Return the bare sensitive words in text."""
    return [m.group(0) for m in WORD.finditer(text)]


def mask(text):
    """Replace every sensitive word with its masked spelling."""
    return WORD.sub(lambda m: _match_case(m, MASKED[m.group(0).lower()]), text)


def paraphrase(text):
    """Replace sensitive words and phrases with dignified wording."""
    out = text
    out = _OBJECT.sub(
        lambda m: _match_case(m, 'take ' + _POSSESSIVE[m.group(1).lower()]), out)
    for pattern, replacement in PHRASES:
        out = pattern.sub(
            lambda m, r=replacement: _match_case(m, m.expand(r)), out)
    return out


def line_count(text, width=1080, size=26, weight='SemiBold'):
    import cards
    font = cards.font(cards.inter(weight), size)
    return len(cards._wrap(text, font, int(width * 0.82)))


def rewrite(text, max_lines=2, lines=None):
    """Paraphrase, or mask when the paraphrase would exceed max_lines."""
    clean = paraphrase(text)
    if max_lines is None:
        return clean
    counter = lines or line_count
    if counter(clean) <= max_lines:
        return clean
    return mask(text)


def check(text):
    """Bare sensitive words that must be fixed before a caption is mapped."""
    return find(text)


def check_spec(spec):
    """Return (caption id, text, words) for every caption that still fails."""
    problems = []
    for index, item in enumerate(spec.get('captions') or [], 1):
        words = check(item.get('text', ''))
        if words:
            problems.append((item.get('id', index), item.get('text', ''), words))
    return problems


def rewrite_spec(spec, max_lines=2, lines=None):
    result = json.loads(json.dumps(spec))
    for item in result.get('captions') or []:
        original = item.get('text', '')
        if not find(original):
            continue
        clean = rewrite(original, max_lines=max_lines, lines=lines)
        item['text'] = clean
        item['sensitive'] = 'masked' if find(clean) else 'paraphrase'
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(description='Check or rewrite sensitive caption words.')
    parser.add_argument('captions')
    parser.add_argument('--check', action='store_true', help='fail on any bare kill or murder word')
    parser.add_argument('--rewrite', help='write a rewritten copy to this path')
    parser.add_argument('--max-lines', type=int, default=2)
    args = parser.parse_args(argv)

    path = Path(args.captions).expanduser()
    spec = json.loads(path.read_text(encoding='utf-8'))

    if args.rewrite:
        out = Path(args.rewrite).expanduser()
        result = rewrite_spec(spec, max_lines=args.max_lines)
        out.write_text(json.dumps(result, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
        changed = sum(1 for c in result.get('captions', []) if c.get('sensitive'))
        print(f'wrote {out} ({changed} captions rewritten)')
        return 0

    problems = check_spec(spec)
    for cid, text, words in problems:
        print(f'{path.name} {cid}: {", ".join(words)}: {text}')
    if problems:
        print(f'{len(problems)} caption(s) still carry sensitive words')
        return 1
    print(f'{path.name}: clean')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
