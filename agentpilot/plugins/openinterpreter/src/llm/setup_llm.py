# from .setup_text_llm import setup_text_llm
# from .convert_to_coding_llm import convert_to_coding_llm
from .coding_llm import get_openai_coding_llm


def setup_llm(interpreter):
    """
    Takes an Interpreter (which includes a ton of LLM settings),
    returns a Coding LLM (a generator that streams deltas with `message` and `code`).
    """

    return get_openai_coding_llm(interpreter)
