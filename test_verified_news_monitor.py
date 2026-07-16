from __future__ import annotations

import unittest
from datetime import datetime, timedelta, timezone

from news_api.verified_news_monitor import (
    SubscriptionState,
    broadtape_contract_candidates,
    classify_delivery,
    monitoring_verdict,
    news_epoch_to_utc,
)


class FreshnessClassificationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.started = datetime(2026, 7, 15, 1, 0, tzinfo=timezone.utc)

    def classify(
        self,
        *,
        key: str = "BRFG:1",
        published_delta: float | None = 1,
        received_delta: float = 2,
        baseline: set[str] | None = None,
        seen: set[str] | None = None,
    ) -> str:
        published = (
            self.started + timedelta(seconds=published_delta)
            if published_delta is not None
            else None
        )
        classification, _ = classify_delivery(
            article_key=key,
            published_at=published,
            received_at=self.started + timedelta(seconds=received_delta),
            subscription_started_at=self.started,
            baseline_article_keys=baseline or set(),
            seen_article_keys=seen or set(),
        )
        return classification

    def test_new_id_published_after_start_is_live(self) -> None:
        self.assertEqual(self.classify(), "LIVE")

    def test_pre_subscription_history_is_snapshot(self) -> None:
        self.assertEqual(self.classify(baseline={"BRFG:1"}), "SNAPSHOT")

    def test_repeated_callback_is_duplicate(self) -> None:
        self.assertEqual(self.classify(seen={"BRFG:1"}), "DUPLICATE")

    def test_old_callback_during_warmup_is_not_live(self) -> None:
        self.assertEqual(
            self.classify(published_delta=-60, received_delta=3),
            "WARMUP",
        )

    def test_old_callback_after_warmup_is_backfill(self) -> None:
        self.assertEqual(
            self.classify(published_delta=-60, received_delta=20),
            "BACKFILL",
        )

    def test_missing_timestamp_never_proves_live(self) -> None:
        self.assertEqual(
            self.classify(published_delta=None, received_delta=20),
            "UNVERIFIED",
        )

    def test_epoch_parser_accepts_seconds_and_milliseconds(self) -> None:
        seconds = 1_789_000_000
        self.assertEqual(
            news_epoch_to_utc(seconds),
            news_epoch_to_utc(seconds * 1000),
        )

    def test_broadtape_channels_map_to_real_news_contracts(self) -> None:
        self.assertEqual(
            broadtape_contract_candidates(
                [
                    "BRFG",
                    "BRFUPDN",
                    "DJ-N",
                    "DJ-RTA",
                    "DJ-RTE",
                    "DJ-RTG",
                    "DJ-RTPRO",
                    "DJNL",
                ]
            ),
            [
                ("BRFG", "BRFG:BRFG_ALL", "BRFG"),
                ("BRFUPDN", "BRFUPDN:BRFUPDN_ALL", "BRFUPDN"),
                ("DJ-GLOBAL-TRADER", "DJ:N_DJGT", "DJ"),
                ("DJTOP-ASIAPAC", "DJTOP:ASIAPAC", "DJTOP"),
                ("DJTOP-EMEA", "DJTOP:EMEA", "DJTOP"),
                ("DJTOP-GLOBAL", "DJTOP:GLBNEWS", "DJTOP"),
                ("DJTOP-NORTHAM", "DJTOP:NORTHAM", "DJTOP"),
                ("DJTOP-COMPANY", "DJTOP:COMPNEWS", "DJTOP"),
                ("DJTOP-MARKET", "DJTOP:MKTDRVE", "DJTOP"),
                ("DJNL", "DJNL:DJNL_ALL", "DJNL"),
            ],
        )

    def test_verdict_does_not_call_unentitled_feed_healthy(self) -> None:
        state = SubscriptionState(
            req_id=1,
            name="all:BZ",
            symbol="ALL",
            kind="broadtape",
            provider_codes="BZ",
            contract=None,
            started_at=self.started,
            status="NOT_ENTITLED",
        )
        self.assertEqual(
            monitoring_verdict([state]),
            "NO_USABLE_SUBSCRIPTIONS",
        )


if __name__ == "__main__":
    unittest.main()
