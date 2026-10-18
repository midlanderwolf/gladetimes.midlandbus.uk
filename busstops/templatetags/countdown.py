from django.template import Library

register = Library()


@register.filter
def countdown(time, now):
    """Format the time remaining until `time` as "N mins"/"1 min"/"Due",
    rounding to the nearest minute (half up), the way a bus stop display would.
    """
    if not time or not now:
        return ""

    seconds = (time - now).total_seconds()
    minutes = int((seconds + 30) // 60)

    if minutes <= 0:
        return "Due"
    if minutes == 1:
        return "1 min"
    return f"{minutes} mins"
