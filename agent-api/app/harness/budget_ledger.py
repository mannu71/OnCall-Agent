"""Metabolic token economy for multi-child delegation (4.2).

Applied at the granularity the current architecture actually supports: one
child run (``subagent_factory._run_child``) is a single request/response —
there is no mid-run interruption hook to pause and reallocate budget
DURING a child's own turn loop. So the ledger operates BETWEEN children in
a multi-child fan-out (``delegate_batch``): each named specialist branch
gets an initial private-energy grant from the run's communal token budget,
spends it as its children's actual token usage comes back, earns more on
success, drifts a continuous role state (``phi``, 0=explore..1=exploit),
and — once its energy is exhausted or it stalls repeatedly — is retired
via lifecycle turnover: half its remaining energy returns to the communal
pool (the other half is the "turnover cost", discouraging thrash), and its
account resets to a fresh, exploration-biased "scout" profile.

Off by default (``settings.token_economy_enabled``); :func:`build_budget_ledger`
returns a no-op ledger when disabled, so callers never need an
``if enabled`` branch of their own.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, Optional


@dataclass
class BranchAccount:
    """Per-specialist-branch economic state."""

    energy: int
    phi: float = 0.5  # 0.0 = fully exploratory, 1.0 = fully exploitative
    stall: int = 0     # consecutive non-successes since the last credit
    turnovers: int = 0


class BudgetLedger:
    """One ledger per multi-child fan-out call (e.g. one delegate_batch run)."""

    def __init__(
        self,
        run_token_budget: int,
        *,
        base_grant: int = 40_000,
        energy_min: int = 5_000,
        stall_limit: int = 3,
    ) -> None:
        self.communal = max(0, int(run_token_budget))
        self.base_grant = max(1, int(base_grant))
        self.energy_min = max(0, int(energy_min))
        self.stall_limit = max(1, int(stall_limit))
        self._accounts: Dict[str, BranchAccount] = {}

    def account(self, branch: str) -> BranchAccount:
        """Return (creating with an initial grant if needed) a branch's account."""
        acct = self._accounts.get(branch)
        if acct is None:
            grant = min(self.base_grant, self.communal) if self.communal > 0 else 0
            self.communal -= grant
            acct = BranchAccount(energy=grant)
            self._accounts[branch] = acct
        return acct

    def spend(self, branch: str, tokens: int) -> None:
        """Debit actual token usage from a branch's private energy."""
        acct = self.account(branch)
        acct.energy = max(0, acct.energy - max(0, int(tokens)))

    def on_success(self, branch: str) -> None:
        """Reward improvement: top up energy from the communal pool, drift
        phi toward exploitation, and reset the stall counter."""
        acct = self.account(branch)
        grant = min(int(0.25 * self.base_grant), self.communal)
        if grant > 0:
            acct.energy += grant
            self.communal -= grant
        acct.phi = min(1.0, acct.phi + 0.2)
        acct.stall = 0

    def on_failure(self, branch: str) -> None:
        """Drift phi toward exploration and increment the stall counter."""
        acct = self.account(branch)
        acct.phi = max(0.0, acct.phi - 0.3)
        acct.stall += 1

    def should_turnover(self, branch: str) -> bool:
        """True once a branch has exhausted its energy or stalled too long."""
        acct = self.account(branch)
        return acct.energy <= self.energy_min or acct.stall >= self.stall_limit

    def turnover(self, branch: str) -> BranchAccount:
        """Retire a stalled/exhausted branch and replace it with a scout
        profile: half the remaining energy returns to the communal pool (a
        turnover cost that discourages thrashing on a dead branch), a
        smaller grant funds the fresh scout, phi resets to fully
        exploratory, and the stall counter clears."""
        acct = self.account(branch)
        self.communal += acct.energy // 2
        acct.turnovers += 1
        scout_grant = min(int(0.3 * self.base_grant), self.communal)
        self.communal -= scout_grant
        acct.energy = scout_grant
        acct.phi = 0.0
        acct.stall = 0
        return acct

    def snapshot(self) -> Dict[str, Dict[str, float]]:
        """Read-only view of every branch's state, for logging/telemetry."""
        return {
            name: {
                "energy": acct.energy, "phi": acct.phi,
                "stall": acct.stall, "turnovers": acct.turnovers,
            }
            for name, acct in self._accounts.items()
        }


class _NoopBudgetLedger:
    """Zero-cost stand-in when token_economy_enabled is False."""

    communal = 0

    def account(self, branch: str) -> BranchAccount:
        return BranchAccount(energy=0)

    def spend(self, branch: str, tokens: int) -> None:
        return None

    def on_success(self, branch: str) -> None:
        return None

    def on_failure(self, branch: str) -> None:
        return None

    def should_turnover(self, branch: str) -> bool:
        return False

    def turnover(self, branch: str) -> BranchAccount:
        return BranchAccount(energy=0)

    def snapshot(self) -> Dict[str, Dict[str, float]]:
        return {}


def build_budget_ledger(run_token_budget: Optional[int] = None):
    """Return a live BudgetLedger when enabled, else a no-op ledger."""
    try:
        from app.config import settings
        enabled = bool(getattr(settings, "token_economy_enabled", False))
    except Exception:  # noqa: BLE001
        enabled = False
    if not enabled:
        return _NoopBudgetLedger()
    try:
        from app.config import settings
        budget = run_token_budget if run_token_budget is not None else int(
            getattr(settings, "token_economy_run_budget", 200_000)
        )
        base_grant = int(getattr(settings, "token_economy_base_grant", 40_000))
        energy_min = int(getattr(settings, "token_economy_energy_min", 5_000))
        stall_limit = int(getattr(settings, "token_economy_stall_limit", 3))
    except Exception:  # noqa: BLE001
        budget, base_grant, energy_min, stall_limit = 200_000, 40_000, 5_000, 3
    return BudgetLedger(
        budget, base_grant=base_grant, energy_min=energy_min, stall_limit=stall_limit,
    )


__all__ = ["BranchAccount", "BudgetLedger", "build_budget_ledger"]
