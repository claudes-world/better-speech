"""Lower semantic clips to contiguous UTF-8 provider requests at render time."""

from dataclasses import replace

from .pronunciation import speech_input


def lower_clip(clip, max_request_bytes):
    if type(max_request_bytes) is not int or max_request_bytes < 4:
        raise ValueError('invalid provider byte limit')
    start = 0
    while start < len(clip.text):
        end, size = start, 0
        while end < len(clip.text) and size + len(clip.text[end].encode()) <= max_request_bytes:
            size += len(clip.text[end].encode())
            end += 1
        if end < len(clip.text):
            cut = clip.text.rfind(' ', start, end)
            if cut > start:
                end = cut + 1
        def part(stop):
            pronunciations = [
                {**p, 'word_span': [p['word_span'][0] - start, p['word_span'][1] - start]}
                for p in clip.pronunciations if start <= p['word_span'][0] < p['word_span'][1] <= stop
            ]
            return replace(clip, text=clip.text[start:stop], pronunciations=pronunciations)

        if clip.pronunciations:
            low, high = start + 1, end
            while low <= high:
                middle = (low + high) // 2
                if len(speech_input(part(middle))[1].encode()) <= max_request_bytes:
                    low = middle + 1
                else:
                    high = middle - 1
            end = max(start + 1, high)
            for p in clip.pronunciations:
                a, b = p['word_span']
                if start < a < end < b:
                    end = a
            cut = clip.text.rfind(' ', start, end)
            if end < len(clip.text) and cut > start:
                end = cut + 1
        yield replace(
            part(end), display_text=clip.display_text if start == 0 else '',
            pause_after_s=clip.pause_after_s if end == len(clip.text) else 0,
            source_start=clip.source_start + start if clip.source_start is not None else None,
            source_end=clip.source_start + end if clip.source_start is not None else None,
        )
        start = end
