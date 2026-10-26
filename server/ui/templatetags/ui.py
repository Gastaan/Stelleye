from django import template
from django.utils.html import escape, format_html
from django.utils.safestring import mark_safe

from ui.icons import STATUS_BADGES, sprite, svg

register = template.Library()


@register.simple_tag
def icon(name, size="md", label=""):
    return mark_safe(svg(name, size, escape(label)))


@register.simple_tag
def icon_sprite():
    return mark_safe(sprite())


@register.simple_tag
def status_badge(status, label):
    tone, icon_name = STATUS_BADGES.get(str(status), ("muted", "clock"))
    return format_html('<span class="badge badge--{}">{}<span>{}</span></span>', tone, mark_safe(svg(icon_name, "sm")), label)
