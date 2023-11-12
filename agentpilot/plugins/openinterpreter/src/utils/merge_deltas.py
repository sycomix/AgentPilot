import json
import re


def merge_deltas(original, delta):
    """
    Pushes the delta into the original and returns that.

    Great for reconstructing OpenAI streaming responses -> complete message objects.
    """
    for key, value in delta.items():
        if (
            isinstance(value, dict)
            and key not in original
            or not isinstance(value, dict)
            and key not in original
        ):
            original[key] = value
        elif isinstance(value, dict):
            merge_deltas(original[key], value)
        else:
            original[key] += value
    return original
