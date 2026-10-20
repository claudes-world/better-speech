"""Lower semantic clips to contiguous UTF-8 provider requests at render time."""

from dataclasses import replace


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
        yield replace(
            clip, text=clip.text[start:end], display_text=clip.display_text if start == 0 else '',
            pause_after_s=clip.pause_after_s if end == len(clip.text) else 0,
            source_start=clip.source_start + start if clip.source_start is not None else None,
            source_end=clip.source_start + end if clip.source_start is not None else None,
        )
        start = end
