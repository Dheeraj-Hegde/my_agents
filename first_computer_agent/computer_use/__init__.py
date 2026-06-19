"""Computer-Use skill (Session 10).

Mirrors the Session-9 Browser skill but for the host OS. The skill owns a
five-layer cascade that escalates from the cheapest path to the most
expensive only when the previous layer cannot satisfy the goal:

    Layer 1   — system / app API     (subprocess, clipboard, file I/O)
    Layer 2a  — deterministic hotkeys (blind keyboard sequences)
    Layer 2b  — accessibility tree    (Windows UIA / pywinauto)
    Layer 2c  — Electron CDP page     (Playwright attach via
                                       electron_debugging_port)
    Layer 3   — vision Set-of-Marks   (screenshot + VLM via the gateway)

The cascade rule is identical to Browser's: a layer is *tried* only after
the cheaper one returned a typed "cannot proceed" signal (NotApplicable /
NeedsEscalation). A layer that succeeds is reported in
`ComputerUseOutput.path` and is the sole contributor to the trajectory's
`layer` field for that turn.
"""

from .schemas import ComputerUseOutput, ComputerUseLayer  # re-export
from .skill import ComputerUseSkill

__all__ = ["ComputerUseSkill", "ComputerUseOutput", "ComputerUseLayer"]
