"""Style tokens for the indigo desktop control panel."""

NAV = "#262665"
NAV_TEXT = "#eef0ff"
CANVAS = "#edf0fa"
PAPER = "#ffffff"
INK = "#18223b"
MUTED = "#52627b"
ACCENT = "#4035b4"
EDGE = "#d7dded"


def configure_console(root, style_factory):
    style = style_factory(root)
    # Windows' native theme ignores background colors on themed buttons.
    if "clam" in style.theme_names():
        style.theme_use("clam")
    for name, color in (("Console", CANVAS), ("ConsoleNav", NAV), ("ConsolePaper", PAPER)):
        style.configure(name + ".TFrame", background=color)
    style.configure("ConsolePaper.TFrame", borderwidth=1, relief="solid", bordercolor=EDGE)
    for name, bg, fg, size, weight in (
        ("ConsoleTitle", PAPER, INK, 18, "bold"),
        ("ConsoleSection", PAPER, INK, 11, "bold"),
        ("ConsoleMuted", PAPER, MUTED, 10, "normal"),
        ("ConsoleAddress", "#f4f6ff", ACCENT, 11, "normal"),
        ("ConsoleFooter", CANVAS, MUTED, 9, "normal"),
        ("ConsoleNavTitle", NAV, NAV_TEXT, 12, "bold"),
        ("ConsoleNavHint", NAV, "#c6caec", 9, "normal"),
        ("ConsoleReady", "#dcf7e5", "#136c3c", 12, "bold"),
        ("ConsolePending", "#eef1fd", "#383084", 12, "bold"),
        ("ConsoleIssue", "#fff0e1", "#923c14", 12, "bold"),
    ):
        style.configure(name + ".TLabel", background=bg, foreground=fg,
                        font=("Microsoft YaHei UI", size, weight))
    for name, bg, fg, hover, border in (
        ("ConsoleNav", NAV, NAV_TEXT, "#3e3e89", NAV),
        ("ConsoleSelected", "#4a43a6", "#ffffff", "#574ebc", "#4a43a6"),
        ("ConsoleAction", "#ffffff", ACCENT, "#eef0fb", EDGE),
        ("ConsolePrimary", ACCENT, "#ffffff", "#4d42c7", ACCENT),
    ):
        style.configure(name + ".TButton", background=bg, foreground=fg,
                        bordercolor=border, lightcolor=border, darkcolor=border,
                        padding=(12, 8), font=("Microsoft YaHei UI", 10, "bold"))
        style.map(name + ".TButton",
                  background=[("disabled", "#f1f2f7"), ("pressed", hover), ("active", hover)],
                  foreground=[("disabled", "#707a92")], bordercolor=[("focus", "#938af0")])
