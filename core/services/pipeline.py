import json
from pathlib import Path

from .content_extraction import fetch_and_clean
from .gemini_client import generate_json

TONE_MAP = {
    'moonlight_elder': {
        'name': 'Moonlight Elder',
        'style': 'Calm, slow, reflective. Simple sentences. Repetition. Gentle authority.'
    },
    'village_fire': {
        'name': 'Village Fire Storyteller',
        'style': 'Animated but controlled. Vivid imagery. Rhythm and pacing. Respectful.'
    },
    'wise_judge': {
        'name': 'Wise Judge',
        'style': 'Firm, analytical, morally grounded. Cause and consequence.'
    },
    'hopeful_healer': {
        'name': 'Hopeful Healer',
        'style': 'Compassionate, soothing, gentle optimism. Emphasize resilience.'
    },
    'playful_trickster': {
        'name': 'Playful Trickster',
        'style': 'Light humor, witty wisdom, gentle teasing. Not for tragedy.'
    },
}


def _as_mapping(value):
    if isinstance(value, dict):
        return value
    if isinstance(value, list):
        first_obj = next((item for item in value if isinstance(item, dict)), None)
        if first_obj:
            return first_obj
    return {}


def load_proverbs():
    path = Path(__file__).resolve().parent.parent / 'proverbs.json'
    return json.loads(path.read_text())


def extract_source(input_url, input_text):
    if input_url:
        return fetch_and_clean(input_url)
    return input_text.strip()


def extract_facts(source_text):
    system = (
        'You extract structured facts from news or articles. '
        'Return JSON only.'
    )
    user = (
        'Extract the main claims, timeline, actors, stakes, and what changed. '
        'Keep names, dates, numbers accurate.\n\n'
        f'SOURCE:\n{source_text[:8000]}'
    )
    return generate_json(system, user)


def infer_themes(facts):
    system = 'You infer themes and morals from structured facts. Return JSON only.'
    user = (
        'Identify themes such as greed, community, patience, injustice, resilience, caution. '
        'Return JSON with keys: themes (list), moral (short sentence).\n\n'
        f'FACTS:\n{json.dumps(facts)}'
    )
    return generate_json(system, user)


def rank_proverbs(facts, themes, proverbs):
    system = 'You select and rank best-fit proverbs. Return JSON only.'
    user = (
        'Given the story facts and themes, select the best-fit proverb and 1-2 alternates. '
        'Return JSON with keys: best, alternates. Each entry must include id, text, reason.\n\n'
        f'FACTS:\n{json.dumps(facts)}\n\n'
        f'THEMES:\n{json.dumps(themes)}\n\n'
        f'PROVERBS:\n{json.dumps(proverbs)}'
    )
    return generate_json(system, user)


def generate_story(facts, themes, proverb_match, tone_key):
    tone = TONE_MAP.get(tone_key, TONE_MAP['moonlight_elder'])
    system = (
        'You are a master African storyteller. '
        'Keep factual anchors accurate. '
        'For tragedy/violence: be respectful, no jokes. '
        'Return JSON only.'
    )
    user = (
        'Write a short narrative in an elder storyteller voice. '
        'Must include protagonist(s), conflict, turning point, resolution, moral. '
        'Embed 1-3 proverbs naturally. Keep names, dates, numbers accurate. '
        f"TONE: {tone['name']} - {tone['style']}.\n\n"
        f'FACTS:\n{json.dumps(facts)}\n\n'
        f'THEMES:\n{json.dumps(themes)}\n\n'
        f'PROVERB_MATCH:\n{json.dumps(proverb_match)}\n\n'
        'Return JSON with keys: story, moral, proverbs_used (list of proverb ids).'
    )
    return generate_json(system, user)


def run_pipeline(input_url, input_text, tone):
    source_text = extract_source(input_url, input_text)
    facts = extract_facts(source_text)
    theme_data = infer_themes(facts)
    proverbs = load_proverbs()
    proverb_match = rank_proverbs(facts, theme_data, proverbs)
    story = generate_story(facts, theme_data, proverb_match, tone)
    theme_data_obj = _as_mapping(theme_data)
    story_obj = _as_mapping(story)

    return {
        'source_text': source_text,
        'facts': facts,
        'themes': theme_data_obj.get('themes', []),
        'moral': theme_data_obj.get('moral', ''),
        'proverbs': proverb_match,
        'story_text': story_obj.get('story', ''),
        'story_moral': story_obj.get('moral', ''),
        'proverbs_used': story_obj.get('proverbs_used', []),
    }
