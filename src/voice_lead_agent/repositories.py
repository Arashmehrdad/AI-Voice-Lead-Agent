from __future__ import annotations

from typing import Protocol

from voice_lead_agent.domain import LeadIntakeCommand, LeadIntakeResult


class LeadRepository(Protocol):
    async def create_or_get_initial_lead(self, command: LeadIntakeCommand) -> LeadIntakeResult:
        """Create or return a lead and its initial initiate_call job atomically."""

    async def readiness(self) -> bool:
        """Return whether the backing database is reachable."""
