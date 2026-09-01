import asyncio
import math
from collections import defaultdict
from typing import Dict, List, Optional, Set
from app.schemas.telemetry import LatencyPercentiles, PIIBreakdown, TelemetrySummary


class TelemetryService:
    """
    Async, thread-safe Telemetry & Metric Aggregator tracking operational performance,
    token cost savings, PII redaction statistics, rolling p50/p95/p99 latency percentiles,
    and SSE streaming subscriber queues for real-time dashboards.
    """

    # Pricing per 1,000 tokens ($0.0015 input, $0.002 output)
    INPUT_TOKEN_COST_PER_1K = 0.0015
    OUTPUT_TOKEN_COST_PER_1K = 0.0020

    def __init__(self, max_latency_samples: int = 1000):
        self._lock = asyncio.Lock()
        self._max_latency_samples = max_latency_samples

        self._total_requests: int = 0
        self._cache_hits: int = 0
        self._cache_misses: int = 0
        self._total_prompt_tokens: int = 0
        self._total_completion_tokens: int = 0
        self._estimated_dollars_saved: float = 0.0
        self._fallback_triggered_count: int = 0

        self._pii_counts: Dict[str, int] = {
            "credit_cards": 0,
            "api_keys": 0,
            "emails": 0,
            "phones": 0,
            "ips": 0,
        }
        self._provider_counts: Dict[str, int] = defaultdict(int)
        self._latency_history: List[float] = []
        self._subscribers: Set[asyncio.Queue] = set()

    def subscribe(self) -> asyncio.Queue:
        """Subscribes an SSE streaming client queue."""
        queue: asyncio.Queue = asyncio.Queue(maxsize=100)
        self._subscribers.add(queue)
        return queue

    def unsubscribe(self, queue: asyncio.Queue) -> None:
        """Unsubscribes an SSE streaming client queue."""
        self._subscribers.discard(queue)

    def _notify_subscribers(self, summary_json: str) -> None:
        """Broadcasts updated JSON summary to all connected SSE clients."""
        dead_queues = set()
        for q in list(self._subscribers):
            try:
                q.put_nowait(summary_json)
            except asyncio.QueueFull:
                pass
            except Exception:
                dead_queues.add(q)
        for dq in dead_queues:
            self._subscribers.discard(dq)

    async def record_event(
        self,
        model: str,
        input_tokens: int,
        output_tokens: int,
        latency_ms: float,
        cache_hit: bool,
        pii_entities: Optional[Dict[str, int]] = None,
        fallback_used: bool = False,
        provider: str = "mock"
    ) -> None:
        """
        Records a telemetry event in a thread-safe async context and broadcasts to subscribers.
        """
        async with self._lock:
            self._total_requests += 1

            if cache_hit:
                self._cache_hits += 1
                input_savings = (input_tokens / 1000.0) * self.INPUT_TOKEN_COST_PER_1K
                output_savings = (output_tokens / 1000.0) * self.OUTPUT_TOKEN_COST_PER_1K
                self._estimated_dollars_saved += (input_savings + output_savings)
            else:
                self._cache_misses += 1

            self._total_prompt_tokens += max(0, input_tokens)
            self._total_completion_tokens += max(0, output_tokens)

            if fallback_used:
                self._fallback_triggered_count += 1

            self._provider_counts[provider] += 1

            if pii_entities:
                for key, count in pii_entities.items():
                    normalized_key = key.lower()
                    if "credit" in normalized_key or "card" in normalized_key:
                        self._pii_counts["credit_cards"] += count
                    elif "secret" in normalized_key or "key" in normalized_key:
                        self._pii_counts["api_keys"] += count
                    elif "email" in normalized_key:
                        self._pii_counts["emails"] += count
                    elif "phone" in normalized_key:
                        self._pii_counts["phones"] += count
                    elif "ip" in normalized_key:
                        self._pii_counts["ips"] += count

            if latency_ms >= 0:
                self._latency_history.append(latency_ms)
                if len(self._latency_history) > self._max_latency_samples:
                    self._latency_history.pop(0)

        # Broadcast update to SSE clients
        if self._subscribers:
            summary = await self.get_metrics_summary()
            self._notify_subscribers(summary.model_dump_json())

    def _calculate_percentile(self, sorted_samples: List[float], percentile: float) -> float:
        if not sorted_samples:
            return 0.0
        k = (len(sorted_samples) - 1) * (percentile / 100.0)
        f = math.floor(k)
        c = math.ceil(k)
        if f == c:
            return round(sorted_samples[int(k)], 2)
        d0 = sorted_samples[int(f)] * (c - k)
        d1 = sorted_samples[int(c)] * (k - f)
        return round(d0 + d1, 2)

    async def get_metrics_summary(self) -> TelemetrySummary:
        """
        Returns a snapshot summary of current telemetry and performance metrics.
        """
        async with self._lock:
            total_reqs = self._total_requests
            hit_ratio = (self._cache_hits / total_reqs * 100.0) if total_reqs > 0 else 0.0

            sorted_latencies = sorted(self._latency_history)
            p50 = self._calculate_percentile(sorted_latencies, 50.0)
            p95 = self._calculate_percentile(sorted_latencies, 95.0)
            p99 = self._calculate_percentile(sorted_latencies, 99.0)

            total_pii = sum(self._pii_counts.values())

            return TelemetrySummary(
                total_requests=total_reqs,
                cache_hits=self._cache_hits,
                cache_misses=self._cache_misses,
                cache_hit_ratio_percent=round(hit_ratio, 2),
                total_prompt_tokens=self._total_prompt_tokens,
                total_completion_tokens=self._total_completion_tokens,
                total_tokens=self._total_prompt_tokens + self._total_completion_tokens,
                estimated_dollars_saved=round(self._estimated_dollars_saved, 6),
                pii_breakdown=PIIBreakdown(
                    credit_cards=self._pii_counts["credit_cards"],
                    api_keys=self._pii_counts["api_keys"],
                    emails=self._pii_counts["emails"],
                    phones=self._pii_counts["phones"],
                    ips=self._pii_counts["ips"],
                    total=total_pii
                ),
                fallback_triggered_count=self._fallback_triggered_count,
                provider_execution_counts=dict(self._provider_counts),
                latency_percentiles_ms=LatencyPercentiles(
                    p50=p50,
                    p95=p95,
                    p99=p99
                )
            )

    async def reset(self) -> None:
        """
        Resets all aggregated telemetry counters.
        """
        async with self._lock:
            self._total_requests = 0
            self._cache_hits = 0
            self._cache_misses = 0
            self._total_prompt_tokens = 0
            self._total_completion_tokens = 0
            self._estimated_dollars_saved = 0.0
            self._fallback_triggered_count = 0
            self._pii_counts = {
                "credit_cards": 0,
                "api_keys": 0,
                "emails": 0,
                "phones": 0,
                "ips": 0,
            }
            self._provider_counts.clear()
            self._latency_history.clear()

        if self._subscribers:
            summary = await self.get_metrics_summary()
            self._notify_subscribers(summary.model_dump_json())


telemetry_service = TelemetryService()
