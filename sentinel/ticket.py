"""
Ticket printer.

Draws one diagnosis as a "ticket" in the terminal:

    ╭─ #12  CRASHED ─────────────────────────────── 14:32:05 ─╮
    │ test-oom  ·  alpine  ·  exit 137                         │
    ├──────────────────────────────────────────────────────────┤
    │ ✖ PROBLEM                                                │
    │   The container used more memory than it is allowed...   │
    │ ✔ SOLUTION                                               │
    │   Give it more memory with --memory ...                  │
    │   $ docker ...                                           │
    ├──────────────────────────────────────────────────────────┤
    │ LAST LOGS                                                │
    │   ...                                                    │
    ╰──────────────────────────────────────────────────────────╯

Uses only plain Python: box-drawing characters and terminal color codes.
Colors are switched off when the output is not a terminal (for example
when redirected to a file) or when the NO_COLOR environment variable is set.
"""

import os
import shutil
import sys
import textwrap

from sentinel.rules import short_error


RESET = "\033[0m"
BOLD = "\033[1m"
DIM = "\033[2m"
RED = "\033[31m"
GREEN = "\033[32m"
YELLOW = "\033[33m"
CYAN = "\033[36m"

MAX_WIDTH = 80
MIN_WIDTH = 50
LOG_LINES_SHOWN = 5


def use_color():
    if os.environ.get("NO_COLOR"):
        return False
    return sys.stdout.isatty()


def paint(text, style):
    """
    Wrap text in a color code, or return it unchanged if colors are off.
    """
    if not use_color():
        return text
    return style + text + RESET


def get_width():
    columns = shutil.get_terminal_size((MAX_WIDTH, 24)).columns
    width = min(columns, MAX_WIDTH)
    width = max(width, MIN_WIDTH)
    return width


def trigger_color(trigger):
    if trigger == "unhealthy":
        return YELLOW
    return RED


class Ticket:
    """
    Collects the lines of one ticket, then prints them inside a box.

    The padding of each line is calculated from the plain text, and the
    color is added afterwards. Otherwise the invisible color codes would
    be counted as characters and the right border would be misaligned.
    """

    def __init__(self):
        self.width = get_width()
        self.inner = self.width - 4
        self.lines = []

    def add_line(self, text, style):
        text = text.replace("\t", "    ")

        if len(text) > self.inner:
            text = text[:self.inner - 1] + "…"

        padding = " " * (self.inner - len(text))
        border = paint("│", DIM)
        self.lines.append(border + " " + paint(text, style) + padding + " " + border)

    def add_wrapped(self, text, style, indent):
        """
        Add a long text, split over several lines to fit inside the box.
        """
        prefix = " " * indent
        wrapped = textwrap.wrap(text, self.inner - indent)
        for piece in wrapped:
            self.add_line(prefix + piece, style)

    def add_blank(self):
        self.add_line("", "")

    def add_divider(self):
        self.lines.append(paint("├" + "─" * (self.width - 2) + "┤", DIM))

    def add_header(self, left, right, style):
        """
        Top border with a title on the left and the time on the right.
        """
        fill = self.width - len(left) - len(right) - 8
        top = (
            paint("╭─ ", DIM)
            + paint(left, style)
            + paint(" " + "─" * fill + " ", DIM)
            + paint(right, DIM)
            + paint(" ─╮", DIM)
        )
        self.lines.append(top)

    def add_footer(self):
        self.lines.append(paint("╰" + "─" * (self.width - 2) + "╯", DIM))

    def show(self):
        for line in self.lines:
            print(line)
        print()


def print_ticket(ticket_id, when, trigger, name, image, exit_code, error, findings, logs):
    """
    Build and print one full ticket.
    """
    ticket = Ticket()
    color = trigger_color(trigger)

    title = "#" + str(ticket_id) + "  " + trigger.upper()
    ticket.add_header(title, when, BOLD + color)

    if trigger == "unhealthy" or trigger == "process killed (OOM)":
        state = "still running"
    else:
        state = "exit " + str(exit_code)

    summary = name + "  ·  " + image + "  ·  " + state
    ticket.add_line(summary, BOLD)

    docker_says = short_error(error)
    if docker_says != "":
        ticket.add_wrapped("Docker says: " + docker_says, DIM, 0)

    ticket.add_divider()

    number = 1
    for finding in findings:
        if number > 1:
            ticket.add_blank()

        label = "✖ PROBLEM"
        if len(findings) > 1:
            label = label + " " + str(number)

        ticket.add_line(label, BOLD + color)
        ticket.add_wrapped(finding["cause"], "", 2)

        details = finding.get("details", [])
        for detail in details:
            ticket.add_wrapped("• " + detail, BOLD, 4)

        ticket.add_line("✔ SOLUTION", BOLD + GREEN)
        ticket.add_wrapped(finding["fix"], "", 2)

        command = finding.get("command", "")
        if command != "":
            ticket.add_line("  $ " + command, CYAN)

        number = number + 1

    log_lines = []
    for line in logs.strip().splitlines():
        if line.strip() != "":
            log_lines.append(line)

    if len(log_lines) > 0:
        ticket.add_divider()
        ticket.add_line("LAST LOGS", BOLD)
        for line in log_lines[-LOG_LINES_SHOWN:]:
            ticket.add_line("  " + line, DIM)

    ticket.add_footer()
    ticket.show()
