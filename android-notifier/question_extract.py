"""Read-only question text assembly. Results are never actionable quiz objects."""
from dataclasses import dataclass, asdict
import re
import unicodedata


@dataclass(frozen=True)
class TextBlock:
    text: str
    box: tuple
    source: str = 'ocr'
    confidence: float | None = None


def reading_order(blocks):
    """Group overlapping text baselines, then read left to right within each row."""
    rows = []
    for block in sorted(blocks, key=lambda b: (b.box[1], b.box[0])):
        row = next((r for r in rows if min(r[0].box[3], block.box[3]) -
                    max(r[0].box[1], block.box[1]) > .5 * min(
                        r[0].box[3] - r[0].box[1], block.box[3] - block.box[1])), None)
        if row is None:
            rows.append([block])
        else:
            row.append(block)
    return [b for row in rows for b in sorted(row, key=lambda b: b.box[0])]


def extract(blocks, warnings=()):
    ordered = reading_order(blocks)
    problems = list(warnings)
    prompt, options, current = [], {}, None
    marker = re.compile(r'[\[【(（]\s*(单选题|判断题|多选题)\s*[\]】)）]')
    label = re.compile(r'^(?:[（(【\[]\s*)?([A-M])(?:[.、:：)）\]】]\s*|\s+|$)', re.I)
    lines = []
    for b in ordered:
        value = unicodedata.normalize('NFKC', b.text).strip()
        if not value:
            continue
        if b.source == 'ocr' and (b.confidence is None or b.confidence < .85):
            problems.append('有低可信度文字，请对照原图核对')
        lines.extend(value.splitlines())
    # A supplied full-page screenshot can include a toolbar and activity title.
    # Strip the preamble only when exactly one explicit question-type marker exists.
    hits = [(i, m) for i, line in enumerate(lines) for m in marker.finditer(line)]
    if len(hits) == 1:
        index, match = hits[0]
        lines = [lines[index][match.end():].strip()] + lines[index + 1:]
    elif len(hits) > 1:
        problems.append('发现多道题或重复题型，本阶段只支持单题预览')
    for value in lines:
        value = value.strip()
        if not value or value in {'提交', '返回', '确定', '确认提交', '随堂练习'}:
            continue
        if value.startswith(('剩余', '我的答案', '正确答案', '已提交')):
            continue
        match = label.match(value)
        if match:
            key = match[1].upper()
            if key in options:
                problems.append('选项标识重复，不能可靠对应图片内容')
            options.setdefault(key, [])
            current = key
            if value[match.end():].strip():
                options[key].append(value[match.end():].strip())
        elif current:
            options[current].append(value)
        else:
            prompt.append(value)
    prompt = '\n'.join(prompt).strip()
    options = {k: '\n'.join(v).strip() for k, v in options.items()}
    if not prompt:
        problems.append('未读到题干')
    if len(options) < 2 or list(options) != list('ABCDEFGHIJKLM'[:len(options)]):
        problems.append('选项标识缺失或乱序，需要清晰的 A、B…标签')
    if any(not value for value in options.values()):
        problems.append('有选项未读到正文')
    problems = list(dict.fromkeys(problems))
    return {'mode': 'preview_only', 'prompt': prompt, 'options': options,
            'status': 'needs_review' if problems else 'recognized',
            'warnings': problems, 'blocks': [asdict(b) for b in ordered],
            'notice': '仅识别文字，不代表理解图片或答案正确；不会点选或提交。'}
