"""
Anomaly scoring methodology for LogBERT.
Combines Masked Log Key Prediction (MLKP) candidates and
Volume of Hypersphere Minimization (VHM) sequence representations.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple

import torch
import torch.nn.functional as F

from app.ml.logbert.config import LogBERTConfig
from app.ml.logbert.model import LogBERT


def compute_sequence_anomaly_score(
    model: LogBERT,
    input_ids: torch.Tensor,
    attention_mask: Optional[torch.Tensor],
    center: torch.Tensor,
    num_candidates: int = 6,
    alpha: float = 0.1,
    vocab: Optional[Any] = None,
    original_events: Optional[List[str]] = None,
    threshold: Optional[float] = None,
) -> Dict[str, Any]:
    """
    Compute multi-faceted anomaly scores and token-level explainability for a single log sequence.

    Methodology:
    1. VHM Hypersphere Distance:
       d_VHM = || h_[DIST] - c ||_2^2
       Measures global sequence-level deviation from the compact normal hypersphere.

    2. MLKP Candidate Evaluation (Top-g):
       For each unpadded event position in the sequence, checks whether the true event
       key is included in the model's top-g predicted candidates. If not, it constitutes
       an unexpected execution transition (mismatch).

    3. MLKP Perplexity / NLL:
       Average negative log-likelihood across sequence tokens.

    4. Composite Score:
       S(x) = mlkp_mismatch_ratio + alpha * normalized_vhm_distance

    5. Token-Level Explainability:
       Identifies exact mismatched positions, extracts top-k candidate predictions and
       probabilities, and distinguishes token-level MLKP evidence from VHM distance drift.

    Parameters:
        model: Trained LogBERT model in eval mode.
        input_ids: [1, seq_len] token IDs.
        attention_mask: [1, seq_len] attention mask (1 = token, 0 = pad).
        center: [hidden_size] hypersphere center.
        num_candidates: Top-g candidate threshold (default: 6 as in paper).
        alpha: Weight for VHM distance contribution.
        vocab: Optional EventVocab for resolving token IDs to template strings.
        original_events: Optional list of raw input event strings.
        threshold: Optional anomaly decision threshold (default: 0.0388).

    Returns:
        Dictionary of anomaly metrics and structured token-level explainability.
    """
    model.eval()
    with torch.no_grad():
        output = model(input_ids, attention_mask=attention_mask)
        # 1. VHM Distance: [1, hidden_size] - [hidden_size]
        dist_rep = output.dist_representation[0]
        vhm_distance = float(torch.sum((dist_rep - center) ** 2).item())

        # 2. MLKP Top-g Evaluation
        logits = output.logits[0]  # [seq_len, vocab_size]
        seq_tokens = input_ids[0]

        # Identify evaluatable positions (exclude special tokens: PAD, CLS/DIST, SEP)
        config = model.config
        eval_mask = (
            (seq_tokens != config.pad_token_id)
            & (seq_tokens != config.cls_token_id)
            & (seq_tokens != config.sep_token_id)
        )
        eval_indices = eval_mask.nonzero(as_tuple=False).view(-1)

        num_events = len(eval_indices)
        mismatches = 0
        total_nll = 0.0
        token_explanations: List[Dict[str, Any]] = []
        mismatch_positions: List[int] = []
        top_k = min(num_candidates, config.vocab_size)

        if num_events > 0:
            probs = F.softmax(logits[eval_indices], dim=-1)  # [num_events, vocab_size]
            top_probs, top_candidates = torch.topk(probs, k=top_k, dim=-1)  # [num_events, k]

            for i, idx in enumerate(eval_indices):
                true_token = int(seq_tokens[idx].item())
                candidates = top_candidates[i].tolist()
                candidate_probs = top_probs[i].tolist()
                is_mismatch = true_token not in candidates
                if is_mismatch:
                    mismatches += 1
                    mismatch_positions.append(i)

                # Negative log-likelihood of true token
                token_prob = probs[i, true_token].item()
                # Epsilon floor to prevent log(0)
                nll = -torch.log(torch.tensor(max(token_prob, 1e-12))).item()
                total_nll += nll

                # Candidate template list
                candidate_items = []
                for k_idx, cand_id in enumerate(candidates):
                    cand_str = vocab.id_to_token(cand_id) if vocab is not None else f"TOKEN_{cand_id}"
                    candidate_items.append({
                        "event": cand_str,
                        "token_id": cand_id,
                        "probability": round(float(candidate_probs[k_idx]), 4),
                    })

                # Observed event name
                if original_events is not None and i < len(original_events):
                    observed_event_str = original_events[i]
                elif vocab is not None:
                    observed_event_str = vocab.id_to_token(true_token)
                else:
                    observed_event_str = f"TOKEN_{true_token}"

                token_explanations.append({
                    "position": i,
                    "observed_event": observed_event_str,
                    "observed_token_id": true_token,
                    "is_mismatch": is_mismatch,
                    "observed_probability": round(token_prob, 4),
                    "observed_nll": round(nll, 4),
                    "top_candidates": candidate_items,
                    "contributes_to_anomaly": is_mismatch,
                })

            mismatch_ratio = mismatches / num_events
            mean_nll = total_nll / num_events
        else:
            mismatch_ratio = 0.0
            mean_nll = 0.0

        # Composite score
        composite_score = mismatch_ratio + alpha * vhm_distance

        # Anomaly decision
        active_threshold = threshold if threshold is not None else 0.0388
        is_anomaly = bool(composite_score >= active_threshold)

        # Categorize explanation type and dominant factor
        mlkp_contrib = mismatch_ratio
        vhm_contrib = round(alpha * vhm_distance, 4)
        center_norm = round(float(torch.norm(center).item()), 4)

        if not is_anomaly:
            explanation_type = "NORMAL"
            dominant_factor = "NONE"
            summary = (
                f"Normal sequence: composite score {composite_score:.4f} is below threshold {active_threshold:.4f} "
                f"({mismatches} mismatches out of {num_events} evaluated events)."
            )
        else:
            if mismatches > 0 and vhm_contrib < 0.25 * mlkp_contrib:
                explanation_type = "TOKEN_MISMATCH"
                dominant_factor = "MLKP_MISMATCH"
                summary = (
                    f"Anomaly driven by {mismatches} unexpected token transition(s) (MLKP mismatch ratio: {mismatch_ratio:.4f}) "
                    f"at position(s) {mismatch_positions}."
                )
            elif mismatches == 0:
                explanation_type = "EMBEDDING_DISTANCE"
                dominant_factor = "VHM_DISTANCE"
                summary = (
                    f"Anomaly driven by global sequence embedding deviation (VHM distance: {vhm_distance:.4f}, "
                    f"contribution: {vhm_contrib:.4f}); all observed event transitions appeared locally valid in top-{top_k} predictions."
                )
            else:
                explanation_type = "HYBRID"
                dominant_factor = "MLKP_MISMATCH" if mlkp_contrib >= vhm_contrib else "VHM_DISTANCE"
                summary = (
                    f"Hybrid anomaly: {mismatches} unexpected token transition(s) (MLKP contribution: {mlkp_contrib:.4f}) "
                    f"combined with significant hypersphere embedding deviation (VHM contribution: {vhm_contrib:.4f})."
                )

        vhm_explanation = (
            f"VHM squared distance ||h_[DIST] - c||^2 = {vhm_distance:.4f} "
            f"(scaled by alpha={alpha:.2f} -> contribution {vhm_contrib:.4f}, center norm: {center_norm:.4f})."
        )

        vhm_evidence = {
            "vhm_distance": round(vhm_distance, 4),
            "vhm_alpha": round(alpha, 4),
            "vhm_contribution": vhm_contrib,
            "center_norm": center_norm,
            "explanation": vhm_explanation,
        }

        explanation = {
            "explanation_type": explanation_type,
            "dominant_factor": dominant_factor,
            "is_anomaly": is_anomaly,
            "composite_score": round(composite_score, 4),
            "threshold": round(active_threshold, 4),
            "mlkp_contribution": round(mlkp_contrib, 4),
            "vhm_contribution": vhm_contrib,
            "mismatch_count": mismatches,
            "total_evaluated_events": num_events,
            "mismatch_positions": mismatch_positions,
            "token_explanations": token_explanations,
            "vhm_evidence": vhm_evidence,
            "summary": summary,
        }

        return {
            "vhm_distance": round(vhm_distance, 4),
            "mismatch_count": mismatches,
            "mismatch_ratio": round(mismatch_ratio, 4),
            "mean_nll": round(mean_nll, 4),
            "composite_score": round(composite_score, 4),
            "num_evaluated_events": num_events,
            "explanation": explanation,
        }


def find_validation_threshold(
    scores: List[float],
    labels: List[int],
) -> Dict[str, Any]:
    """
    Determine an anomaly decision threshold using ONLY validation data.
    Iterates candidate thresholds across validation score percentiles
    to maximize validation F1 score.

    Parameters:
        scores: Sequence anomaly scores on validation split.
        labels: Validation ground truth (0 = Normal, 1 = Anomaly).

    Returns:
        Dictionary with optimal threshold, validation precision, recall, and F1.
    """
    if not scores or not labels:
        return {"threshold": 1.0, "val_precision": 0.0, "val_recall": 0.0, "val_f1": 0.0}

    sorted_scores = sorted(set(scores))
    if len(sorted_scores) == 1:
        return {"threshold": sorted_scores[0], "val_precision": 0.0, "val_recall": 0.0, "val_f1": 0.0}

    best_f1 = -1.0
    best_threshold = sorted_scores[0]
    best_metrics = {"precision": 0.0, "recall": 0.0, "f1": 0.0}

    # Evaluate potential cutoffs
    for thresh in sorted_scores:
        preds = [1 if s >= thresh else 0 for s in scores]
        tp = sum(1 for p, y in zip(preds, labels) if p == 1 and y == 1)
        fp = sum(1 for p, y in zip(preds, labels) if p == 1 and y == 0)
        fn = sum(1 for p, y in zip(preds, labels) if p == 0 and y == 1)

        precision = tp / (tp + fp) if (tp + fp) > 0 else 0.0
        recall = tp / (tp + fn) if (tp + fn) > 0 else 0.0
        f1 = (2 * precision * recall) / (precision + recall) if (precision + recall) > 0 else 0.0

        if f1 > best_f1:
            best_f1 = f1
            best_threshold = thresh
            best_metrics = {
                "precision": round(precision, 4),
                "recall": round(recall, 4),
                "f1": round(f1, 4),
            }

    return {
        "threshold": round(best_threshold, 4),
        "val_precision": best_metrics["precision"],
        "val_recall": best_metrics["recall"],
        "val_f1": best_metrics["f1"],
    }
