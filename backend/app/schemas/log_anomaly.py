"""
Pydantic schemas for LogBERT anomaly detection and sequence evaluation.
"""

from typing import Any, Dict, List, Optional
from pydantic import BaseModel, ConfigDict, Field


class LogBERTSequenceItem(BaseModel):
    """Single sequence of log events for anomaly evaluation."""

    model_config = ConfigDict(from_attributes=True)

    session_id: Optional[str] = Field(None, description="Identifier for log session (e.g. block_id or trace_id)")
    events: List[str] = Field(..., description="Ordered list of log event templates or tokens")


class LogBERTInferenceRequest(BaseModel):
    """Request payload to evaluate a single sequence of log events."""

    model_config = ConfigDict(from_attributes=True)

    session_id: Optional[str] = Field(None, description="Identifier for log session (e.g. block_id or trace_id)")
    events: List[str] = Field(..., description="Ordered list of log event templates or tokens (e.g. ['E1', 'E2'])")


class LogBERTBatchInferenceRequest(BaseModel):
    """Request payload to evaluate multiple sequences of log events."""

    model_config = ConfigDict(from_attributes=True)

    sequences: List[LogBERTSequenceItem] = Field(..., description="Batch of event sequences to evaluate")


class CandidateTemplatePrediction(BaseModel):
    """Predicted candidate event template and its probability."""

    model_config = ConfigDict(from_attributes=True)

    event: str = Field(..., description="Predicted event template code or name")
    token_id: int = Field(..., description="Vocabulary token identifier")
    probability: float = Field(..., description="Predicted softmax probability (0.0 to 1.0)")


class TokenExplanationItem(BaseModel):
    """Detailed token/event-level explainability for a single position."""

    model_config = ConfigDict(from_attributes=True)

    position: int = Field(..., description="Zero-based index of the event in the sequence")
    observed_event: str = Field(..., description="Observed event template or token")
    observed_token_id: int = Field(..., description="Observed vocabulary token ID (1 for UNK)")
    is_mismatch: bool = Field(..., description="Whether observed token is NOT in top-k candidate predictions")
    observed_probability: float = Field(..., description="Model softmax probability assigned to observed token")
    observed_nll: float = Field(..., description="Negative log-likelihood of the observed token")
    top_candidates: List[CandidateTemplatePrediction] = Field(
        default_factory=list,
        description="Top-k candidate templates predicted by the model at this position",
    )
    contributes_to_anomaly: bool = Field(
        ...,
        description="Whether this position directly contributed to an anomalous score (e.g. MLKP mismatch)",
    )


class VHMEvidence(BaseModel):
    """Volume of Hypersphere Minimization (VHM) global embedding evidence."""

    model_config = ConfigDict(from_attributes=True)

    vhm_distance: float = Field(..., description="Squared Euclidean distance ||h_[DIST] - c||^2")
    vhm_alpha: float = Field(..., description="Weight coefficient alpha for VHM distance contribution")
    vhm_contribution: float = Field(..., description="Scaled VHM distance contribution (alpha * vhm_distance)")
    center_norm: Optional[float] = Field(None, description="L2 norm of the VHM hypersphere center")
    explanation: str = Field(..., description="Human-readable explanation of VHM global embedding distance")


class AnomalyExplanation(BaseModel):
    """Comprehensive explainability breakdown for a LogBERT sequence prediction."""

    model_config = ConfigDict(from_attributes=True)

    explanation_type: str = Field(
        ...,
        description="Classification of anomaly cause: 'TOKEN_MISMATCH', 'EMBEDDING_DISTANCE', 'HYBRID', or 'NORMAL'",
    )
    dominant_factor: str = Field(
        ...,
        description="Dominant factor in composite score: 'MLKP_MISMATCH', 'VHM_DISTANCE', or 'NONE'",
    )
    is_anomaly: bool = Field(..., description="Whether sequence meets or exceeds anomaly threshold")
    composite_score: float = Field(..., description="Composite anomaly score S(x)")
    threshold: float = Field(..., description="Applied anomaly decision threshold")
    mlkp_contribution: float = Field(..., description="Contribution of MLKP mismatches (mismatch_ratio)")
    vhm_contribution: float = Field(..., description="Contribution of VHM distance (alpha * vhm_distance)")
    mismatch_count: int = Field(..., description="Total count of unexpected event mismatches")
    total_evaluated_events: int = Field(..., description="Number of evaluated non-special events")
    mismatch_positions: List[int] = Field(
        default_factory=list,
        description="Indices of positions where observed token was NOT in top-k candidates",
    )
    token_explanations: List[TokenExplanationItem] = Field(
        default_factory=list,
        description="Per-position token explainability records",
    )
    vhm_evidence: VHMEvidence = Field(..., description="VHM embedding-distance diagnostic evidence")
    summary: str = Field(..., description="Human-readable summary grounding the prediction in model evidence")


class LogBERTInferenceResponse(BaseModel):
    """Evaluation result for an event sequence."""

    model_config = ConfigDict(from_attributes=True)

    session_id: Optional[str] = Field(None, description="Identifier of the evaluated session")
    is_anomaly: bool = Field(..., description="Whether composite anomaly score meets or exceeds threshold")
    composite_score: float = Field(..., description="Composite anomaly score S(x) = mismatch_ratio + alpha * vhm_dist")
    threshold: float = Field(..., description="Applied anomaly decision threshold")
    vhm_distance: float = Field(..., description="Volume of Hypersphere squared Euclidean distance ||h - c||^2")
    mismatch_count: int = Field(..., description="Number of events not in top-g candidate predictions")
    mismatch_ratio: float = Field(..., description="Fraction of unexpected token transitions")
    mean_nll: float = Field(..., description="Mean negative log-likelihood of true tokens")
    num_events: int = Field(..., description="Number of non-special evaluated events in sequence")
    events: List[str] = Field(..., description="Original event sequence tokens")
    explanation: Optional[AnomalyExplanation] = Field(
        None,
        description="Structured token-level and embedding-distance explainability breakdown",
    )


class LogBERTBatchInferenceResponse(BaseModel):
    """Batch evaluation response."""

    model_config = ConfigDict(from_attributes=True)

    total_evaluated: int = Field(..., description="Total number of evaluated sequences")
    total_anomalies: int = Field(..., description="Total number of sequences flagged as anomalies")
    results: List[LogBERTInferenceResponse] = Field(..., description="Individual sequence evaluation results")


class LogBERTModelInfoResponse(BaseModel):
    """Non-sensitive metadata for loaded LogBERT model."""

    model_config = ConfigDict(from_attributes=True)

    model_name: str = Field(..., description="Model identifier")
    version: str = Field(..., description="Model version")
    hidden_size: int = Field(..., description="Hidden vector dimension")
    num_hidden_layers: int = Field(..., description="Number of Transformer encoder layers")
    num_attention_heads: int = Field(..., description="Number of attention heads")
    vocab_size: int = Field(..., description="Number of tokens in vocabulary")
    max_seq_len: int = Field(..., description="Maximum supported sequence length")
    total_parameters: int = Field(..., description="Total model parameter count")
    threshold: float = Field(..., description="Active validation anomaly threshold")
    vhm_alpha: float = Field(..., description="Weight of VHM distance in composite scoring")
    device: str = Field(..., description="Inference execution device (e.g. cpu)")
    center_norm: float = Field(..., description="L2 norm of the VHM hypersphere center")
    status: str = Field(..., description="Operational status of the inference engine")
