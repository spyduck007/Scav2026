"""Safe Markdown rendering for organizer-authored challenge descriptions."""

import bleach
import markdown
from django import template
from django.utils.safestring import mark_safe


register = template.Library()


ALLOWED_TAGS = [
    "a",
    "blockquote",
    "br",
    "code",
    "del",
    "em",
    "h2",
    "h3",
    "h4",
    "hr",
    "li",
    "ol",
    "p",
    "pre",
    "strong",
    "ul",
]
ALLOWED_ATTRIBUTES = {"a": ["href", "title"]}
ALLOWED_PROTOCOLS = ["http", "https", "mailto"]


@register.filter
def render_markdown(value: str) -> str:
    """Render a limited Markdown subset and remove unsafe HTML and URL schemes."""
    rendered = markdown.markdown(
        value or "",
        extensions=["fenced_code", "sane_lists", "nl2br"],
    )
    sanitized = bleach.clean(
        rendered,
        tags=ALLOWED_TAGS,
        attributes=ALLOWED_ATTRIBUTES,
        protocols=ALLOWED_PROTOCOLS,
        strip=True,
    )
    return mark_safe(bleach.linkify(sanitized))
