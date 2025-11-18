from typing import Callable

from rich.ansi import AnsiDecoder
from rich.console import Console, ConsoleOptions, Group


class RichPlotext:
    def __init__(self, make_plot: Callable[[int, int], str]):
        self.__make_plot: Callable[[int, int], str] = make_plot

    def __rich_console__(self, console: Console, options: ConsoleOptions):
        width = min(options.max_width, int(console.width * 0.6))
        height = min(options.max_height, int(console.height * 0.6))
        yield Group(*AnsiDecoder().decode(self.__make_plot(width, height)))
