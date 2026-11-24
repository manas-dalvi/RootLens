"""
LogBERT Neural Network Architecture.
PyTorch Transformer Encoder for self-supervised log sequence modeling.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Dict, Optional, Tuple

import torch
import torch.nn as nn
import torch.nn.functional as F

from app.ml.logbert.config import LogBERTConfig


@dataclass
class LogBERTOutput:
    """Output container for LogBERT forward pass."""
    logits: torch.Tensor             # [batch_size, seq_len, vocab_size] (MLKP prediction logits)
    sequence_output: torch.Tensor    # [batch_size, seq_len, hidden_size] (hidden representations)
    cls_representation: torch.Tensor # [batch_size, hidden_size] (sequence-level [CLS] representation for VHM)
    dist_representation: Optional[torch.Tensor] = None # LogBERT [DIST] sequence representation (alias of cls_representation)

    def __post_init__(self) -> None:
        if self.dist_representation is None:
            self.dist_representation = self.cls_representation


class LogBERTEmbedding(nn.Module):
    """
    Construct embeddings from log event token IDs and learned sequence positions.
    """
    def __init__(self, config: LogBERTConfig):
        super().__init__()
        self.token_embeddings = nn.Embedding(
            config.vocab_size,
            config.hidden_size,
            padding_idx=config.pad_token_id,
        )
        self.position_embeddings = nn.Embedding(
            config.max_seq_len,
            config.hidden_size,
        )
        self.layer_norm = nn.LayerNorm(config.hidden_size, eps=config.layer_norm_eps)
        self.dropout = nn.Dropout(config.hidden_dropout_prob)
        self.max_seq_len = config.max_seq_len

    def forward(
        self,
        input_ids: torch.Tensor,
        position_ids: Optional[torch.Tensor] = None,
    ) -> torch.Tensor:
        seq_len = input_ids.size(1)
        if seq_len > self.max_seq_len:
            raise ValueError(
                f"Sequence length {seq_len} exceeds max_seq_len {self.max_seq_len}"
            )

        if position_ids is None:
            position_ids = torch.arange(
                seq_len,
                dtype=torch.long,
                device=input_ids.device,
            ).unsqueeze(0).expand_as(input_ids)

        token_embeds = self.token_embeddings(input_ids)
        pos_embeds = self.position_embeddings(position_ids)
        embeddings = self.layer_norm(token_embeds + pos_embeds)
        return self.dropout(embeddings)


class MultiHeadAttention(nn.Module):
    """
    Multi-head scaled dot-product self-attention mechanism.
    """
    def __init__(self, config: LogBERTConfig):
        super().__init__()
        self.num_attention_heads = config.num_attention_heads
        self.attention_head_size = config.attention_head_size
        self.all_head_size = self.num_attention_heads * self.attention_head_size

        self.query = nn.Linear(config.hidden_size, self.all_head_size)
        self.key = nn.Linear(config.hidden_size, self.all_head_size)
        self.value = nn.Linear(config.hidden_size, self.all_head_size)
        self.out_proj = nn.Linear(self.all_head_size, config.hidden_size)

        self.attn_dropout = nn.Dropout(config.attention_probs_dropout_prob)
        self.scale = 1.0 / math.sqrt(self.attention_head_size)

    def _transpose_for_scores(self, x: torch.Tensor) -> torch.Tensor:
        # [batch_size, seq_len, all_head_size] -> [batch_size, num_heads, seq_len, head_size]
        new_shape = x.size()[:-1] + (self.num_attention_heads, self.attention_head_size)
        x = x.view(*new_shape)
        return x.permute(0, 2, 1, 3)

    def forward(
        self,
        hidden_states: torch.Tensor,
        attention_mask: Optional[torch.Tensor] = None,
    ) -> torch.Tensor:
        q = self._transpose_for_scores(self.query(hidden_states))
        k = self._transpose_for_scores(self.key(hidden_states))
        v = self._transpose_for_scores(self.value(hidden_states))

        # Raw attention scores: [batch_size, num_heads, seq_len, seq_len]
        attention_scores = torch.matmul(q, k.transpose(-1, -2)) * self.scale

        if attention_mask is not None:
            # attention_mask: [batch_size, 1, 1, seq_len] or [batch_size, 1, seq_len, seq_len]
            # 0.0 for tokens to attend to, large negative for masked tokens
            attention_scores = attention_scores + attention_mask

        attention_probs = F.softmax(attention_scores, dim=-1)
        attention_probs = self.attn_dropout(attention_probs)

        # Context: [batch_size, num_heads, seq_len, head_size]
        context = torch.matmul(attention_probs, v)
        context = context.permute(0, 2, 1, 3).contiguous()
        new_shape = context.size()[:-2] + (self.all_head_size,)
        context = context.view(*new_shape)

        return self.out_proj(context)


class PositionwiseFeedForward(nn.Module):
    """
    Two-layer position-wise feed-forward neural network with GELU activation.
    """
    def __init__(self, config: LogBERTConfig):
        super().__init__()
        self.dense1 = nn.Linear(config.hidden_size, config.intermediate_size)
        self.dense2 = nn.Linear(config.intermediate_size, config.hidden_size)
        self.act = nn.GELU() if config.hidden_act.lower() == "gelu" else nn.ReLU()
        self.dropout = nn.Dropout(config.hidden_dropout_prob)

    def forward(self, hidden_states: torch.Tensor) -> torch.Tensor:
        x = self.act(self.dense1(hidden_states))
        x = self.dropout(x)
        return self.dense2(x)


class TransformerBlock(nn.Module):
    """
    Single LogBERT Transformer Encoder layer.
    Pre-LN / Post-LN residual architecture with multi-head attention and feed-forward sublayers.
    """
    def __init__(self, config: LogBERTConfig):
        super().__init__()
        self.attention = MultiHeadAttention(config)
        self.attn_layer_norm = nn.LayerNorm(config.hidden_size, eps=config.layer_norm_eps)
        self.attn_dropout = nn.Dropout(config.hidden_dropout_prob)

        self.feed_forward = PositionwiseFeedForward(config)
        self.ffn_layer_norm = nn.LayerNorm(config.hidden_size, eps=config.layer_norm_eps)
        self.ffn_dropout = nn.Dropout(config.hidden_dropout_prob)

    def forward(
        self,
        hidden_states: torch.Tensor,
        attention_mask: Optional[torch.Tensor] = None,
    ) -> torch.Tensor:
        # Self-Attention + Residual + LayerNorm
        attn_out = self.attention(hidden_states, attention_mask=attention_mask)
        hidden_states = self.attn_layer_norm(hidden_states + self.attn_dropout(attn_out))

        # Feed-Forward + Residual + LayerNorm
        ffn_out = self.feed_forward(hidden_states)
        hidden_states = self.ffn_layer_norm(hidden_states + self.ffn_dropout(ffn_out))
        return hidden_states


class LogBERTEncoder(nn.Module):
    """
    Stack of TransformerEncoder blocks.
    """
    def __init__(self, config: LogBERTConfig):
        super().__init__()
        self.layers = nn.ModuleList([
            TransformerBlock(config) for _ in range(config.num_hidden_layers)
        ])

    def forward(
        self,
        hidden_states: torch.Tensor,
        attention_mask: Optional[torch.Tensor] = None,
    ) -> torch.Tensor:
        for layer in self.layers:
            hidden_states = layer(hidden_states, attention_mask=attention_mask)
        return hidden_states


class MLKPHead(nn.Module):
    """
    Masked Log Key Prediction classification head.
    Projects contextual token representations to log vocabulary distribution.
    """
    def __init__(self, config: LogBERTConfig):
        super().__init__()
        self.dense = nn.Linear(config.hidden_size, config.hidden_size)
        self.act = nn.GELU() if config.hidden_act.lower() == "gelu" else nn.ReLU()
        self.layer_norm = nn.LayerNorm(config.hidden_size, eps=config.layer_norm_eps)
        self.decoder = nn.Linear(config.hidden_size, config.vocab_size)

    def forward(self, hidden_states: torch.Tensor) -> torch.Tensor:
        x = self.act(self.dense(hidden_states))
        x = self.layer_norm(x)
        return self.decoder(x)


class LogBERT(nn.Module):
    """
    Complete LogBERT model for log sequence self-supervised learning and anomaly detection.
    Combines embedding, encoder, sequence pooling, and MLKP projection head.
    """
    def __init__(self, config: LogBERTConfig):
        super().__init__()
        self.config = config
        self.embeddings = LogBERTEmbedding(config)
        self.encoder = LogBERTEncoder(config)
        self.mlkp_head = MLKPHead(config)

    def get_extended_attention_mask(
        self,
        attention_mask: torch.Tensor,
    ) -> torch.Tensor:
        """
        Convert 2D attention mask (1 for valid token, 0 for pad) into 4D additive mask
        compatible with attention logits: 0.0 for valid, -10000.0 for pad.
        """
        if attention_mask.dim() == 2:
            # [batch_size, seq_len] -> [batch_size, 1, 1, seq_len]
            extended_mask = attention_mask.unsqueeze(1).unsqueeze(2)
        elif attention_mask.dim() == 3:
            extended_mask = attention_mask.unsqueeze(1)
        else:
            extended_mask = attention_mask

        # Cast to float and invert: 1.0 -> 0.0, 0.0 -> -10000.0
        extended_mask = extended_mask.to(dtype=self.embeddings.token_embeddings.weight.dtype)
        extended_mask = (1.0 - extended_mask) * -10000.0
        return extended_mask

    def forward(
        self,
        input_ids: torch.Tensor,
        attention_mask: Optional[torch.Tensor] = None,
        position_ids: Optional[torch.Tensor] = None,
    ) -> LogBERTOutput:
        """
        Forward pass through LogBERT.

        Parameters:
            input_ids: [batch_size, seq_len] integer token IDs.
            attention_mask: Optional [batch_size, seq_len] (1 = keep, 0 = mask/pad).
            position_ids: Optional [batch_size, seq_len] position indices.

        Returns:
            LogBERTOutput containing logits, sequence_output, and cls_representation.
        """
        # Automatically generate attention mask from pad_token_id if none provided
        if attention_mask is None:
            attention_mask = (input_ids != self.config.pad_token_id).long()

        extended_attention_mask = self.get_extended_attention_mask(attention_mask)

        # 1. Embeddings
        hidden_states = self.embeddings(input_ids, position_ids=position_ids)

        # 2. Transformer Encoder Layers
        sequence_output = self.encoder(hidden_states, attention_mask=extended_attention_mask)

        # 3. Sequence-level representation for VHM: [CLS] token at position 0
        cls_representation = sequence_output[:, 0, :]

        # 4. MLKP vocabulary prediction logits
        logits = self.mlkp_head(sequence_output)

        return LogBERTOutput(
            logits=logits,
            sequence_output=sequence_output,
            cls_representation=cls_representation,
        )

    def count_parameters(self) -> Dict[str, int]:
        """Return total and trainable parameter counts."""
        total = sum(p.numel() for p in self.parameters())
        trainable = sum(p.numel() for p in self.parameters() if p.requires_grad)
        return {
            "total_parameters": total,
            "trainable_parameters": trainable,
        }
