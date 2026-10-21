"""Exact Google input serialization, shared by synthesis and request sizing."""

from html import escape


def speech_input(clip):
    if not clip.pronunciations or clip.provider != 'google' or clip.language != 'en':
        return 'text', clip.text
    parts, cursor = ['<speak>'], 0
    for pronunciation in clip.pronunciations:
        start, end = pronunciation['word_span']
        ipa = pronunciation['ipa']
        if (type(start) is not int or type(end) is not int or not cursor <= start < end <= len(clip.text)
                or not isinstance(ipa, str) or not ipa):
            raise ValueError('invalid pronunciation span or IPA')
        parts.extend((escape(clip.text[cursor:start]),
                      '<phoneme alphabet="ipa" ph="' + escape(ipa, quote=True) + '">',
                      escape(clip.text[start:end]), '</phoneme>'))
        cursor = end
    parts.extend((escape(clip.text[cursor:]), '</speak>'))
    return 'ssml', ''.join(parts)
