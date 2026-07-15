from __future__ import annotations

import sys
from types import ModuleType
from unittest import mock


class TextBlockStub:
    """Small editable-region stand-in for tests that do not install the vendor runtime."""

    def __init__(
        self,
        *,
        lines=None,
        texts=None,
        language="unknown",
        font_size=-1,
        angle=0,
        translation="",
        fg_color=(0, 0, 0),
        bg_color=(255, 255, 255),
        line_spacing=1.0,
        letter_spacing=1.0,
        font_family="",
        bold=False,
        underline=False,
        italic=False,
        direction="auto",
        alignment="auto",
        rich_text="",
        _bounding_rect=None,
        default_stroke_width=0.2,
        font_weight=50,
        source_lang="",
        target_lang="",
        opacity=1.0,
        shadow_radius=0.0,
        shadow_strength=1.0,
        shadow_color=(0, 0, 0),
        shadow_offset=None,
        prob=1.0,
        **kwargs,
    ):
        self.lines = list(lines or [])
        self.texts = list(texts or [""])
        self.language = language
        self.font_size = font_size
        self.angle = angle
        self.translation = translation
        self.fg_colors = list(fg_color)
        self.bg_colors = list(bg_color)
        self.line_spacing = line_spacing
        self.letter_spacing = letter_spacing
        self.font_family = font_family
        self.bold = bold
        self.underline = underline
        self.italic = italic
        self._direction = direction
        self._alignment = alignment
        self.rich_text = rich_text
        self._bounding_rect = list(_bounding_rect) if _bounding_rect else None
        self.default_stroke_width = default_stroke_width
        self.font_weight = font_weight
        self._source_lang = source_lang
        self.target_lang = target_lang
        self.opacity = opacity
        self.shadow_radius = shadow_radius
        self.shadow_strength = shadow_strength
        self.shadow_color = list(shadow_color)
        self.shadow_offset = list(shadow_offset or [0, 0])
        self.prob = prob
        for key, value in kwargs.items():
            setattr(self, key, value)

    @property
    def direction(self):
        return self._direction

    @direction.setter
    def direction(self, value):
        self._direction = value

    @property
    def alignment(self):
        return self._alignment

    @alignment.setter
    def alignment(self, value):
        self._alignment = value

    @property
    def xyxy(self):
        if self._bounding_rect:
            return list(self._bounding_rect)
        points = [point for line in self.lines for point in line]
        if not points:
            return [0, 0, 0, 0]
        xs = [int(point[0]) for point in points]
        ys = [int(point[1]) for point in points]
        return [min(xs), min(ys), max(xs), max(ys)]

    @property
    def min_rect(self):
        x1, y1, x2, y2 = self.xyxy
        return ([[x1, y1], [x2, y1], [x2, y2], [x1, y2]], 0, 0)

    def to_dict(self):
        payload = dict(self.__dict__)
        payload["direction"] = self.direction
        payload["alignment"] = self.alignment
        payload["fg_color"] = list(self.fg_colors)
        payload["bg_color"] = list(self.bg_colors)
        return payload


def textblock_module_patch():
    package = ModuleType("manga_translator")
    package.__path__ = []
    utils_package = ModuleType("manga_translator.utils")
    utils_package.__path__ = []
    textblock_module = ModuleType("manga_translator.utils.textblock")
    textblock_module.TextBlock = TextBlockStub
    package.utils = utils_package
    utils_package.textblock = textblock_module
    return mock.patch.dict(
        sys.modules,
        {
            "manga_translator": package,
            "manga_translator.utils": utils_package,
            "manga_translator.utils.textblock": textblock_module,
        },
    )
