"""Safe, local-only pilotfish installer."""

__all__ = [
    "ApprovalRequired",
    "InstallPlan",
    "Installer",
    "InstallerError",
    "OperationResult",
    "PlanBlocked",
]


def __getattr__(name: str):
    if name not in __all__:
        raise AttributeError(name)
    from . import installer

    return getattr(installer, name)
