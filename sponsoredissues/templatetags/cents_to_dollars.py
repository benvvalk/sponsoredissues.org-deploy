from django import template

register = template.Library()

@register.filter
def cents_to_dollars(cents: int) -> str:
    """
    Takes a integer cents USD value and returns a string representing
    the equivalent dollar value (with two decimal places).

    Example: 1635 -> "16.35"

    Note!: The naive approach of simply dividing the cents value by
    100.00 (float) is vulnerable to floating-point precision errors,
    so we use integer arithmetic instead.
    """
    dollars_part = str(cents // 100)
    cents_value = cents % 100
    if cents_value < 10:
        cents_part = f"0{cents_value}"
    else:
        cents_part = str(cents_value)
    return f"{dollars_part}.{cents_part}"