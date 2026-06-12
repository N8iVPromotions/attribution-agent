from __future__ import annotations
from pydantic import BaseModel


class RunIngestFlowInput(BaseModel):
    client_id: str
    dry_run: bool = True


class GenerateReportInput(BaseModel):
    client_id: str


class GetPipelineStatusInput(BaseModel):
    run_id: str | None = None
    limit: int = 10


class ListClientsInput(BaseModel):
    agency_id: str | None = None


class GetClientConfigInput(BaseModel):
    client_id: str


class GetCostSummaryInput(BaseModel):
    agency_id: str | None = None


class GetRecentMemoriesInput(BaseModel):
    client_id: str
    limit: int = 5
