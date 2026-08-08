"""
Pydantic schemas for DocGuard AI API.

These models define the structured contract between the Agent tools, the
HTTP API, and the core engine. Risk items, analysis results, and search
results all conform to these schemas so they can be consumed by Qoder,
WorkBuddy, or TRAE Work without ambiguity.
"""
from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field


# =====================================================================
# Enumerations
# =====================================================================
class RiskLevel(str, Enum):
    LOW = "Low"
    MEDIUM = "Medium"
    HIGH = "High"


class DocumentType(str, Enum):
    CONTRACT = "contract"
    TENDER = "tender"
    TECHNICAL = "technical"
    PRD = "prd"
    POLICY = "policy"
    GENERAL = "general"


class AnalysisStatus(str, Enum):
    PENDING = "pending"
    PARSING = "parsing"
    OCR = "ocr"
    CHUNKING = "chunking"
    EMBEDDING = "embedding"
    INDEXING = "indexing"
    ANALYZING = "analyzing"
    LLM_REASONING = "llm_reasoning"
    REPORTING = "reporting"
    COMPLETED = "completed"
    FAILED = "failed"


# =====================================================================
# Risk & finding models
# =====================================================================
class RiskItem(BaseModel):
    """A single risk / issue found in a document."""

    id: str = Field(..., description="Stable identifier, e.g. R-001")
    category: str = Field(..., description="e.g. 付款条款 / 违约责任 / 安全合规")
    risk_level: RiskLevel = RiskLevel.MEDIUM
    issue: str = Field(..., description="Problem description")
    location: str = Field(..., description="Clause / section / page reference")
    explanation: str = Field("", description="Why this is a risk")
    suggestion: str = Field("", description="Recommended revision")
    evidence: str = Field("", description="Original text snippet from the document")


class RequirementItem(BaseModel):
    """A requirement extracted from a tender / technical document."""

    id: str
    requirement: str
    category: str = "general"           # 商务 / 技术 / 资质 / 交付 / 其他
    matched: bool = False
    evidence: str = ""
    gap_note: str = ""


class ChapterCheck(BaseModel):
    """Result of checking whether a required chapter/section exists."""

    chapter: str
    present: bool
    note: str = ""


# =====================================================================
# Document analysis result
# =====================================================================
class DocumentSummary(BaseModel):
    title: str = ""
    doc_type: DocumentType = DocumentType.GENERAL
    parties: List[str] = Field(default_factory=list)
    key_points: List[str] = Field(default_factory=list)
    summary_text: str = ""


class DocumentAnalysis(BaseModel):
    """Full structured output of analyze_document."""

    document_id: str
    file_name: str
    file_path: str
    file_type: str
    file_size_bytes: int
    page_count: int = 0
    char_count: int = 0
    chunk_count: int = 0
    language: str = "zh"

    summary: DocumentSummary = Field(default_factory=DocumentSummary)
    risks: List[RiskItem] = Field(default_factory=list)

    # Tender-specific
    requirements: List[RequirementItem] = Field(default_factory=list)
    capability_match_score: Optional[float] = None
    missing_capabilities: List[str] = Field(default_factory=list)

    # Technical-document-specific
    chapter_checks: List[ChapterCheck] = Field(default_factory=list)
    architecture_issues: List[RiskItem] = Field(default_factory=list)
    security_issues: List[RiskItem] = Field(default_factory=list)
    performance_risks: List[RiskItem] = Field(default_factory=list)

    overall_risk_level: RiskLevel = RiskLevel.LOW
    risk_count_by_level: Dict[str, int] = Field(default_factory=dict)

    llm_used: bool = False
    llm_model_name: str = ""
    engine_notes: str = ""

    created_at: datetime = Field(default_factory=datetime.now)

    def to_public_dict(self) -> Dict[str, Any]:
        """Return a JSON-serializable dict suitable for the Agent/tools."""
        return self.model_dump(mode="json")


# =====================================================================
# Search / RAG
# =====================================================================
class RetrievedChunk(BaseModel):
    chunk_id: str
    text: str
    page: Optional[int] = None
    section: str = ""
    score: float = 0.0
    metadata: Dict[str, Any] = Field(default_factory=dict)


class SearchResult(BaseModel):
    query: str
    document_id: Optional[str] = None
    answer: str = ""
    chunks: List[RetrievedChunk] = Field(default_factory=list)
    llm_used: bool = False


# =====================================================================
# Comparison
# =====================================================================
class DiffSegment(BaseModel):
    type: str = Field(..., description="added | removed | modified | unchanged")
    text_a: str = ""
    text_b: str = ""
    location_a: str = ""
    location_b: str = ""


class ComparisonResult(BaseModel):
    document_a: str
    document_b: str
    segments: List[DiffSegment] = Field(default_factory=list)
    summary: str = ""
    change_count: int = 0


# =====================================================================
# Report
# =====================================================================
class Report(BaseModel):
    report_id: str
    document_id: str
    title: str
    format: str = "markdown"          # markdown | html | json
    file_path: str
    content: str = ""
    created_at: datetime = Field(default_factory=datetime.now)


# =====================================================================
# API request models
# =====================================================================
class AnalyzeRequest(BaseModel):
    file_path: str = Field(..., description="Absolute path to the document")
    doc_type_hint: Optional[DocumentType] = None
    user_id: str = "default"
    session_id: str = "default"
    use_llm: bool = True
    use_cloud: bool = False


class SearchRequest(BaseModel):
    query: str
    document_id: Optional[str] = None
    top_k: Optional[int] = None
    user_id: str = "default"
    session_id: str = "default"
    use_cloud: bool = False


class ReportRequest(BaseModel):
    analysis_result: Dict[str, Any]
    format: str = "markdown"
    user_id: str = "default"


class CompareRequest(BaseModel):
    file_path_a: str
    file_path_b: str
    doc_type_hint: Optional[DocumentType] = None


# =====================================================================
# API response wrappers
# =====================================================================
class HealthStatus(BaseModel):
    status: str = "ok"
    version: str = "1.0.0"
    model_loaded: bool = False
    model_name: str = ""
    model_device: str = ""
    embedding_loaded: bool = False
    embedding_model: str = ""
    ocr_available: bool = False
    documents_indexed: int = 0
    server_time: datetime = Field(default_factory=datetime.now)
    provider: str = "local"
    cloud_available: bool = False
    cloud_enabled: bool = False
    local_only: bool = True


class StandardResponse(BaseModel):
    success: bool = True
    message: str = ""
    data: Optional[Any] = None
    error: Optional[str] = None


class ProgressEvent(BaseModel):
    """Server-Sent Event payload for real-time analysis progress."""

    stage: AnalysisStatus
    progress: float = Field(..., ge=0.0, le=1.0)
    message: str = ""
    document_id: Optional[str] = None
